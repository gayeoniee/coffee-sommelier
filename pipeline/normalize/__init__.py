from dataclasses import dataclass, field

from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, TaxonomyNode


@dataclass
class Normalized:
    coffees: list[CoffeeRecord] = field(default_factory=list)
    reviews: list[ReviewRecord] = field(default_factory=list)
    brands: list[BrandRecord] = field(default_factory=list)
    menu_items: list[MenuItemRecord] = field(default_factory=list)
    taxonomy: list[TaxonomyNode] = field(default_factory=list)

    def extend(self, other: "Normalized") -> None:
        self.coffees += other.coffees
        self.reviews += other.reviews
        self.brands += other.brands
        self.menu_items += other.menu_items
        self.taxonomy += other.taxonomy

    def size(self) -> int:
        return len(self.coffees) + len(self.reviews) + len(self.brands) + len(self.menu_items) + len(self.taxonomy)


from pathlib import Path  # noqa: E402

from pipeline.collect import latest_snapshot  # noqa: E402
from pipeline.records import write_jsonl  # noqa: E402


def _normalizers():
    from pipeline.normalize.datasets import (
        normalize_coffeereview, normalize_cqi, normalize_roasterdb, normalize_roasters_kr, normalize_sca,
    )
    from pipeline.normalize.menus import (
        normalize_coffeebean, normalize_compose, normalize_ediya, normalize_hollys, normalize_mega, normalize_paik, normalize_paulbassett, normalize_shopify, normalize_starbucks,
    )
    from pipeline.normalize.shopify_gauged import normalize_shopify_gauged

    return {
        "coffeereview_kaggle": normalize_coffeereview, "cqi": normalize_cqi, "roasterdb": normalize_roasterdb,
        "sca_wheel": normalize_sca, "starbucks": normalize_starbucks, "mega": normalize_mega,
        "paik": normalize_paik, "shopify": normalize_shopify, "hollys": normalize_hollys, "compose": normalize_compose, "coffeebean": normalize_coffeebean, "paulbassett": normalize_paulbassett,
        "ediya": normalize_ediya,
        "shopify_gauged": normalize_shopify_gauged,
        "roasters_kr": normalize_roasters_kr,   # last: its URL duplicates of earlier sources are dropped
    }


# Sources written as one flat folder by their own command (no dated snapshot + manifest).
FLAT_SOURCES = {"roasters_kr": "beans.jsonl"}


def _source_dir(raw_root: Path, name: str) -> Path | None:
    if name in FLAT_SOURCES:
        d = raw_root / name
        return d if (d / FLAT_SOURCES[name]).exists() else None
    return latest_snapshot(raw_root, name)


def _dedupe(records):
    seen, out = set(), []
    for r in records:
        if r.key not in seen:
            seen.add(r.key)
            out.append(r)
    return out


def drop_cross_source_url_duplicates(coffees):
    """Keep the first coffee per product URL across sources (one source may repeat a URL, e.g. CQI's repo link)."""
    owner: dict[str, str] = {}
    out, dropped = [], 0
    for c in coffees:
        if c.source_url and owner.setdefault(c.source_url, c.source) != c.source:
            dropped += 1
            continue
        out.append(c)
    return out, dropped


def run_normalize(raw_root: Path, out_dir: Path, curated_dir: Path,
                  exclude_sources: tuple[str, ...] = ()) -> dict[str, int | str]:
    from pipeline.normalize.menus import apply_kca_caffeine, kca_tea_drinks, normalize_brands

    total, per_source = Normalized(), {}
    for name, fn in _normalizers().items():
        if name in exclude_sources:
            per_source[f"src:{name}"] = "excluded"
            continue
        snap = _source_dir(raw_root, name)
        if snap is None:
            per_source[f"src:{name}"] = 0
            continue
        n = fn(snap, snap.name)
        total.extend(n)
        per_source[f"src:{name}"] = n.size()
    total.brands = normalize_brands(curated_dir)
    brand_keys = {b.key for b in total.brands}
    total.menu_items = [m for m in total.menu_items if m.brand_key in brand_keys]
    total.menu_items, kca = apply_kca_caffeine(total.menu_items, kca_tea_drinks(curated_dir))
    total.coffees, dropped = drop_cross_source_url_duplicates(total.coffees)
    counts = {"dropped_url_duplicates": dropped, "kca_caffeine_filled": kca["filled"],
              "kca_caffeine_matched": len(kca["matched"])}
    for field_name in ("coffees", "reviews", "brands", "menu_items", "taxonomy"):
        records = _dedupe(getattr(total, field_name))
        counts[field_name] = write_jsonl(out_dir / f"{field_name}.jsonl", records)
    return {**counts, **per_source}
