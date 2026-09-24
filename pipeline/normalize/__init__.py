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
    from pipeline.normalize.datasets import normalize_coffeereview, normalize_cqi, normalize_roasterdb, normalize_sca
    from pipeline.normalize.menus import normalize_mega, normalize_paik, normalize_shopify, normalize_starbucks

    return {
        "coffeereview_kaggle": normalize_coffeereview, "cqi": normalize_cqi, "roasterdb": normalize_roasterdb,
        "sca_wheel": normalize_sca, "starbucks": normalize_starbucks, "mega": normalize_mega,
        "paik": normalize_paik, "shopify": normalize_shopify,
    }


def _dedupe(records):
    seen, out = set(), []
    for r in records:
        if r.key not in seen:
            seen.add(r.key)
            out.append(r)
    return out


def run_normalize(raw_root: Path, out_dir: Path, curated_dir: Path) -> dict[str, int]:
    from pipeline.normalize.menus import normalize_brands

    total, per_source = Normalized(), {}
    for name, fn in _normalizers().items():
        snap = latest_snapshot(raw_root, name)
        if snap is None:
            per_source[f"src:{name}"] = 0
            continue
        n = fn(snap, snap.name)
        total.extend(n)
        per_source[f"src:{name}"] = n.size()
    total.brands = normalize_brands(curated_dir)
    brand_keys = {b.key for b in total.brands}
    total.menu_items = [m for m in total.menu_items if m.brand_key in brand_keys]
    counts = {}
    for field_name in ("coffees", "reviews", "brands", "menu_items", "taxonomy"):
        records = _dedupe(getattr(total, field_name))
        counts[field_name] = write_jsonl(out_dir / f"{field_name}.jsonl", records)
    return {**counts, **per_source}
