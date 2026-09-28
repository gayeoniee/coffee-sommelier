"""What a refresh would change, before anything is written (docs/adr/0015-automated-refresh.md).

Pure functions over plain dicts/records so they are testable without a database:
  - diff_menus:   new/removed menu items, caffeine changes (flagged when > 20 %), decaf flag changes, new brands,
                  and new menu names with no hand milk label ("라벨 필요" -- loaded with needs_review)
  - diff_coffees: new/removed/changed beans of the refreshed groups, new roasters; unchanged beans are reused
                  from the DB as is (no LLM call, no embedding call)
  - size_check:   refuse a refresh that would remove too much of the catalog (likely a broken scraper)
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from pipeline.load import coffee_group
from pipeline.records import CoffeeRecord, MenuItemRecord

CAFFEINE_FLAG = 0.20          # relative caffeine change that is called out for a human look
MAX_REMOVED_SHARE = 0.20      # a table losing more than this share of its active rows fails the refresh
GROUP_SHRINK = 0.50           # ... and so does one scraper group shrinking to under half of its rows
GROUP_MIN_ROWS = 5            # (only for groups big enough for that to mean something)

MENU_FIELDS = ("name", "name_en", "category", "is_decaf", "decaf_option", "caffeine_mg", "source_url")
# Coffee fields the source itself sets (enrich never changes them): any difference = the bean changed.
COFFEE_SOURCE_FIELDS = ("name", "roaster", "origin_country", "origin_region", "process", "roast_level",
                        "decaf_process", "flavor_summary", "source_url", "altitude_m", "variety")
# Fields enrich may fill in: they only count when the source itself gives a value (e.g. a roaster's gauge).
COFFEE_ENRICHABLE = ("acidity", "body", "sweetness")


def _num(x):
    return None if x is None else float(x)


def menu_unchanged(old: dict, new: MenuItemRecord) -> bool:
    return all((_num(old.get(f)) == _num(getattr(new, f))) if f == "caffeine_mg" else old.get(f) == getattr(new, f)
               for f in MENU_FIELDS)


def diff_menus(old: dict[str, dict], new: list[MenuItemRecord], labels: dict[str, bool],
               brand_scope: set[str], old_brands: set[str] | None = None, limit: int = 30) -> dict:
    """`old` = active DB menu items by key ({"brand_key", "name", "is_decaf", "decaf_option", "caffeine_mg", ...})."""
    new_by_key = {m.key: m for m in new}
    added = sorted(k for k in new_by_key if k not in old)
    removed = sorted(k for k, o in old.items() if o["brand_key"] in brand_scope and k not in new_by_key)
    caffeine, flagged, decaf = [], [], []
    for k, m in new_by_key.items():
        o = old.get(k)
        if o is None:
            continue
        before, after = _num(o.get("caffeine_mg")), _num(m.caffeine_mg)
        if before != after:
            rel = abs(after - before) / before if before and after is not None else None
            row = {"key": k, "brand": m.brand_key, "name": m.name, "before": before, "after": after,
                   "change": round(rel, 3) if rel is not None else None}
            caffeine.append(row)
            if rel is None or rel > CAFFEINE_FLAG:
                flagged.append(row)
        if (o.get("is_decaf"), o.get("decaf_option")) != (m.is_decaf, m.decaf_option):
            decaf.append({"key": k, "brand": m.brand_key, "name": m.name,
                          "before": {"is_decaf": o.get("is_decaf"), "decaf_option": o.get("decaf_option")},
                          "after": {"is_decaf": m.is_decaf, "decaf_option": m.decaf_option}})
    known_brands = old_brands if old_brands is not None else {o["brand_key"] for o in old.values()}
    new_brands = sorted({m.brand_key for m in new} - known_brands)
    unlabelled = sorted({m.name for m in new if m.name not in labels})
    kept = sorted({o["brand_key"] for o in old.values()} - brand_scope)
    return {
        "counts": {"current": len(old), "collected": len(new), "new": len(added), "removed": len(removed),
                   "caffeine_changed": len(caffeine), "caffeine_flagged": len(flagged), "decaf_changed": len(decaf),
                   "new_brands": len(new_brands), "label_needed": len(unlabelled)},
        "new": [{"key": k, "brand": new_by_key[k].brand_key, "name": new_by_key[k].name} for k in added][:limit],
        "removed": [{"key": k, "brand": old[k]["brand_key"], "name": old[k]["name"]} for k in removed][:limit],
        "caffeine_flagged": flagged[:limit], "decaf_changed": decaf[:limit], "new_brands": new_brands,
        "label_needed": unlabelled, "brands_in_scope": sorted(brand_scope), "brands_kept_not_collected": kept,
        "removed_by_brand": dict(Counter(old[k]["brand_key"] for k in removed)),
    }


def coffee_changed(old: dict, new: CoffeeRecord, old_review: str | None, new_review: str | None) -> bool:
    if any(old.get(f) != getattr(new, f) for f in COFFEE_SOURCE_FIELDS):
        return True
    if new.is_decaf and not old.get("is_decaf"):
        return True
    if any(getattr(new, f) is not None and old.get(f) != getattr(new, f) for f in COFFEE_ENRICHABLE):
        return True
    if new.flavor_tags and list(old.get("flavor_tags") or []) != list(new.flavor_tags):
        return True
    if old.get("embedding") is None:
        return True
    return (old_review or None) != (new_review or None)


def diff_coffees(old: dict[str, dict], new: list[CoffeeRecord], old_reviews: dict[str, str],
                 new_reviews: dict[str, str], limit: int = 30) -> dict:
    """`old` = active DB coffees by key of the LIVE sources ({"source", "roaster", fields..., "embedding"}).
    Returns counts/details plus `todo` (keys to enrich + embed) and `scope` (the groups this run refreshed)."""
    scope = {coffee_group(c.source, c.roaster) for c in new}
    new_by_key = {c.key: c for c in new}
    added = sorted(k for k in new_by_key if k not in old)
    removed = sorted(k for k, o in old.items() if coffee_group(o["source"], o.get("roaster")) in scope
                     and k not in new_by_key)
    changed = sorted(k for k, c in new_by_key.items() if k in old
                     and coffee_changed(old[k], c, old_reviews.get(k), new_reviews.get(k)))
    old_roasters = {(o["source"], o.get("roaster")) for o in old.values()}
    new_roasters = sorted({f"{c.source}:{c.roaster}" for c in new if (c.source, c.roaster) not in old_roasters})
    kept_groups = sorted({coffee_group(o["source"], o.get("roaster")) for o in old.values()} - scope)
    return {
        "counts": {"current": len(old), "collected": len(new), "new": len(added), "removed": len(removed),
                   "changed": len(changed), "unchanged": len(new) - len(added) - len(changed),
                   "new_roasters": len(new_roasters)},
        "new": [{"key": k, "name": new_by_key[k].name, "roaster": new_by_key[k].roaster} for k in added][:limit],
        "removed": [{"key": k, "name": old[k]["name"], "roaster": old[k].get("roaster")} for k in removed][:limit],
        "changed": changed[:limit], "new_roasters": new_roasters, "groups_kept_not_collected": kept_groups,
        "removed_by_group": dict(Counter(coffee_group(old[k]["source"], old[k].get("roaster")) for k in removed)),
        "todo": added + changed, "scope": sorted(scope),
    }


def size_check(tables: dict[str, tuple[int, int]], groups: Iterable[tuple[str, int, int]] = ()) -> list[str]:
    """tables = {name: (active rows now, rows the refresh would remove)};
    groups = [(group, rows now, rows after)]. Returns failure messages (empty = pass)."""
    fails = []
    for name, (current, removed) in tables.items():
        if current and removed / current > MAX_REMOVED_SHARE:
            fails.append(f"{name}: {removed}/{current}행({removed / current:.0%})이 사라짐 — "
                         f"{MAX_REMOVED_SHARE:.0%} 초과, 스크레이퍼 고장 의심")
    for group, before, after in groups:
        if before >= GROUP_MIN_ROWS and after < before * GROUP_SHRINK:
            fails.append(f"{group}: {before}개 → {after}개 — 절반 미만으로 줄어듦, 스크레이퍼 고장 의심")
    return fails
