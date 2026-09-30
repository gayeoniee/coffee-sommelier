"""Phase 1 analysis: cross-check the MFDS 식품영양성분 DB (음식 DB, 2026-08-28) coffee drinks against our
own collected menus, sanity-check a protein-based milk-detector, and inventory brands we don't track yet.

Analysis only: no DB writes, no shipped-config changes. Reads:
  - data/raw/mfds_food/20260828_음식DB.xlsx   (gitignored source; see docs/adr/0023-mfds-food-db.md)
  - data/normalized/menu_items.jsonl          (our own collected menus, 8 brands)
  - data/curated/menu_milk_labels.yaml        (human milk/no-milk labels, keyed by menu_items `name`)

Writes:
  - data/eval/phase12_mfds_crosscheck.json

Usage: uv run python scripts/eval_mfds_crosscheck.py
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.normalize.mfds_food import BRAND_KEY_BY_COMPANY, normalize_mfds_food  # noqa: E402

XLSX_PATH = ROOT / "data/raw/mfds_food/20260828_음식DB.xlsx"
MENU_ITEMS_PATH = ROOT / "data/normalized/menu_items.jsonl"
MILK_LABELS_PATH = ROOT / "data/curated/menu_milk_labels.yaml"
OUT_PATH = ROOT / "data/eval/phase12_mfds_crosscheck.json"

OUR_BRAND_KEYS = {f"brand:{v}" for v in BRAND_KEY_BY_COMPANY.values()}

_TEMP_HOT = re.compile(r"\bHOT\b|핫", re.I)
_TEMP_ICE = re.compile(r"\bICE(D)?\b|아이스", re.I)
_STRIP = re.compile(r"[\s()\[\]/·,.\-]+")
_DECAF = re.compile(r"디카페인|decaf", re.I)


def temp_of(name: str) -> str:
    if _TEMP_ICE.search(name):
        return "ICED"
    if _TEMP_HOT.search(name):
        return "HOT"
    return "UNK"


def norm_name(name: str) -> str:
    s = _DECAF.sub("", name)
    s = _TEMP_HOT.sub("", s)
    s = _TEMP_ICE.sub("", s)
    s = re.sub(r"\((?:R|L|M|S|V|EX|Max|Tall|Grande|Venti|ML|J)\)", "", s, flags=re.I)
    s = _STRIP.sub("", s)
    return s.lower().strip()


def load_menu_items() -> list[dict]:
    out = []
    with open(MENU_ITEMS_PATH, encoding="utf-8") as f:
        for line in f:
            out.append(json.loads(line))
    return out


def load_milk_labels() -> dict[str, bool]:
    return yaml.safe_load(MILK_LABELS_PATH.read_text(encoding="utf-8"))


def build_mfds_index(drinks) -> dict[tuple, list]:
    """(brand_key, norm_name, temp, is_decaf) -> list[MfdsCoffeeDrink]"""
    idx = defaultdict(list)
    for d in drinks:
        if d.brand_key is None:
            continue
        idx[(d.brand_key, norm_name(d.drink_name), d.temperature or "UNK", d.is_decaf)].append(d)
    return idx


def cross_check(drinks, menu_items) -> dict:
    mfds_idx = build_mfds_index(drinks)
    by_brand = defaultdict(list)  # brand -> list of dicts (our_name, our_val, pub_val, ratio, sizes)
    decaf_coverage = defaultdict(lambda: {"our_decaf": 0, "matched_decaf": 0})

    for mi in menu_items:
        brand = mi["brand_key"]
        if brand not in OUR_BRAND_KEYS:
            continue
        our_caff = mi.get("caffeine_mg")
        name = mi["name"]
        temp = temp_of(name)
        nname = norm_name(name)
        is_decaf = bool(mi.get("is_decaf"))
        if is_decaf:
            decaf_coverage[brand]["our_decaf"] += 1

        group = mfds_idx.get((brand, nname, temp, is_decaf))
        fallback = False
        if not group:
            # fall back: ignore temperature (many of our names don't carry a HOT/ICED marker)
            for t in ("HOT", "ICED", "UNK"):
                group = mfds_idx.get((brand, nname, t, is_decaf))
                if group:
                    fallback = True
                    break
        if not group or our_caff is None:
            continue
        if is_decaf:
            decaf_coverage[brand]["matched_decaf"] += 1

        pub_vals = [g.caffeine_mg_per_serving for g in group if g.caffeine_mg_per_serving is not None]
        if not pub_vals:
            continue
        pub_median = statistics.median(pub_vals)
        if our_caff <= 0:
            continue
        ratio = pub_median / our_caff
        sizes = sorted({g.size_tag for g in group if g.size_tag} | {"?"} - {"?"}) or None
        by_brand[brand].append({
            "name": name, "temp": temp, "is_decaf": is_decaf, "our_caffeine_mg": our_caff,
            "public_caffeine_mg_median": round(pub_median, 1), "n_public_rows": len(pub_vals),
            "ratio_public_over_ours": round(ratio, 3), "sizes_seen": sizes, "temp_fallback_match": fallback,
        })

    report = {}
    for brand, rows in by_brand.items():
        ratios = [r["ratio_public_over_ours"] for r in rows]
        n = len(ratios)
        within10 = sum(1 for r in ratios if 0.9 <= r <= 1.1) / n if n else None
        within25 = sum(1 for r in ratios if 0.75 <= r <= 1.25) / n if n else None
        worst = sorted(rows, key=lambda r: abs(r["ratio_public_over_ours"] - 1), reverse=True)[:5]
        dc = decaf_coverage[brand]
        report[brand] = {
            "matched_n": n,
            "median_ratio_public_over_ours": round(statistics.median(ratios), 3) if ratios else None,
            "share_within_10pct": round(within10, 3) if within10 is not None else None,
            "share_within_25pct": round(within25, 3) if within25 is not None else None,
            "top5_disagreements": worst,
            "decaf_menu_items": dc["our_decaf"],
            "decaf_matched": dc["matched_decaf"],
        }
    return report


def milk_threshold_analysis(drinks, menu_items, milk_labels) -> dict:
    mfds_idx = defaultdict(list)  # (brand, nname) -> list of protein_per_basis (basis ~100g/ml)
    for d in drinks:
        if d.brand_key is None or d.protein_g_per_basis is None:
            continue
        mfds_idx[(d.brand_key, norm_name(d.drink_name))].append(d.protein_g_per_basis)

    points = []  # (protein_per_100, milk_bool, brand, name)
    seen_names = set()
    for mi in menu_items:
        name = mi["name"]
        if name not in milk_labels or name in seen_names:
            continue
        brand = mi["brand_key"]
        vals = mfds_idx.get((brand, norm_name(name)))
        if not vals:
            continue
        seen_names.add(name)
        points.append((statistics.median(vals), bool(milk_labels[name]), brand, name))

    if not points:
        return {"n_matched": 0, "note": "no menu items with a milk label matched an MFDS protein value"}

    thresholds = sorted({round(p[0], 2) for p in points})
    best = None
    for t in thresholds:
        tp = sum(1 for p, milk, *_ in points if p >= t and milk)
        fp = sum(1 for p, milk, *_ in points if p >= t and not milk)
        fn = sum(1 for p, milk, *_ in points if p < t and milk)
        tn = sum(1 for p, milk, *_ in points if p < t and not milk)
        acc = (tp + tn) / len(points)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        cand = {"threshold_g_per_100": t, "accuracy": round(acc, 3),
                "precision": round(precision, 3) if precision is not None else None,
                "recall": round(recall, 3) if recall is not None else None, "tp": tp, "fp": fp, "fn": fn, "tn": tn}
        if best is None or cand["accuracy"] > best["accuracy"]:
            best = cand

    return {
        "n_matched": len(points),
        "n_milk_true": sum(1 for p in points if p[1]),
        "n_milk_false": sum(1 for p in points if not p[1]),
        "best": best,
        "sample_points": [{"name": n, "brand": b, "protein_g_per_100": round(p, 2), "milk_label": m}
                           for p, m, b, n in sorted(points, key=lambda x: x[0])],
    }


def new_brand_inventory(drinks) -> list[dict]:
    by_company = defaultdict(list)
    for d in drinks:
        if d.brand_key and d.brand_key not in OUR_BRAND_KEYS:
            by_company[(d.company_raw, d.brand_key)].append(d)
    rows = []
    for (company, bkey), ds in by_company.items():
        rows.append({
            "company": company, "brand_key": bkey, "n_drinks": len(ds),
            "n_with_caffeine": sum(1 for d in ds if d.caffeine_mg_per_basis is not None),
            "n_decaf": sum(1 for d in ds if d.is_decaf),
            "sizes": sorted({d.size_tag for d in ds if d.size_tag}),
        })
    rows.sort(key=lambda r: -r["n_drinks"])
    return rows


def main():
    print(f"loading {XLSX_PATH} ...")
    drinks = normalize_mfds_food(XLSX_PATH)
    print(f"{len(drinks)} coffee-shop drink rows normalized")
    menu_items = load_menu_items()
    milk_labels = load_milk_labels()

    result = {
        "source_xlsx": str(XLSX_PATH.relative_to(ROOT)).replace("\\", "/"),
        "n_mfds_coffee_rows": len(drinks),
        "n_mfds_rows_no_brand": sum(1 for d in drinks if d.brand_key is None),
        "cross_check_by_brand": cross_check(drinks, menu_items),
        "milk_threshold": milk_threshold_analysis(drinks, menu_items, milk_labels),
        "new_brand_inventory_top15": new_brand_inventory(drinks)[:15],
        "n_distinct_new_brands": len({d.brand_key for d in drinks if d.brand_key and d.brand_key not in OUR_BRAND_KEYS}),
    }
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
