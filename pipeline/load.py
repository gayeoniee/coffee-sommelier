import json
import logging
from pathlib import Path

import yaml
from psycopg.types.json import Jsonb

from pipeline import settings

from pipeline.enrich import read_json_lines
from pipeline.query import to_vector_literal
from pipeline.records import (
    BrandRecord,
    CoffeeRecord,
    MenuItemRecord,
    ReviewRecord,
    TaxonomyNode,
    read_jsonl,
)

# Rows a user's tasting points at are never deleted, even if they vanish from the source.
PROTECTED_COFFEES = ("SELECT coffee_id FROM tastings WHERE coffee_id IS NOT NULL "
                     "UNION SELECT default_bean_coffee_id FROM brands WHERE default_bean_coffee_id IS NOT NULL "
                     "UNION SELECT decaf_bean_coffee_id FROM brands WHERE decaf_bean_coffee_id IS NOT NULL "
                     "UNION SELECT coffee_id FROM menu_items WHERE coffee_id IS NOT NULL")
PROTECTED_MENU_ITEMS = "SELECT menu_item_id FROM tastings WHERE menu_item_id IS NOT NULL"
PROTECTED_BRANDS = f"SELECT brand_id FROM menu_items WHERE id IN ({PROTECTED_MENU_ITEMS})"

log = logging.getLogger("pipeline.load")


def _read_lines(path: Path) -> list[dict]:
    return read_json_lines(path)[0]


def _ids(conn, table: str) -> dict[str, int]:
    return dict(conn.execute(f"SELECT key, id FROM {table}").fetchall())


def _upsert(cur, table: str, cols: list[str], rows: list[tuple], casts: dict[str, str] | None = None) -> None:
    if not rows:
        return
    casts = casts or {}
    values = ", ".join(f"%s{casts.get(c, '')}" for c in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "key")
    cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({values}) "
                    f"ON CONFLICT (key) DO UPDATE SET {updates}", rows)


def _delete_missing(cur, table: str, keys: list[str], protected_ids_sql: str | None = None) -> int:
    sql = f"DELETE FROM {table} WHERE NOT (key = ANY(%s))"
    if protected_ids_sql:
        sql += f" AND id NOT IN ({protected_ids_sql})"
    cur.execute(sql, (keys,))
    return cur.rowcount


def _retire_missing(cur, table: str, keys: list[str], protected_ids_sql: str) -> tuple[int, int]:
    """Delete rows that vanished from the source; the protected ones that must stay become active = false.

    Returns (deleted, kept). Source rows are (re)activated.
    """
    deleted = _delete_missing(cur, table, keys, protected_ids_sql)
    cur.execute(f"UPDATE {table} SET active = (key = ANY(%s))", (keys,))
    kept = [k for (k,) in cur.execute(f"SELECT key FROM {table} WHERE NOT active ORDER BY key").fetchall()]
    if kept:
        log.warning("%s: kept %d retired row(s) still referenced: %s", table, len(kept), kept[:5])
    return deleted, len(kept)


# Coffee "groups" for a scoped (refresh) load: a group that produced no rows this run is left alone, so one broken
# roastery/store scraper never retires its beans (docs/adr/0015-automated-refresh.md). Multi-shop sources are
# grouped per shop (the roaster column); single-feed sources are one group.
PER_ROASTER_SOURCES = ("roasters_kr", "shopify_gauged")


def coffee_group(source: str, roaster: str | None) -> str:
    return f"{source}:{roaster or ''}" if source in PER_ROASTER_SOURCES else source


MFDS_PROTEIN_LABELS_FILENAME = "mfds_protein_milk_labels.json"  # written by run_normalize, see mfds_food.py


def load_milk_labels(curated_dir: Path | None = None, norm_dir: Path | None = None) -> dict[str, bool]:
    """Menu name -> contains milk: hand labels (data/curated/menu_milk_labels.yaml) merged with MFDS
    protein-derived labels (docs/adr/0023-mfds-food-db.md Phase 2) for names the hand file doesn't
    cover yet. A hand label always wins; the protein file is a generated pipeline artifact
    (`norm_dir`/mfds_protein_milk_labels.json, not committed), so it silently contributes nothing when
    absent (no MFDS raw file this run) -- see menu_items_from_mfds in pipeline/normalize/mfds_food.py."""
    path = (curated_dir or settings.CURATED_DIR) / "menu_milk_labels.yaml"
    hand_labels = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    protein_path = (norm_dir or settings.NORMALIZED_DIR) / MFDS_PROTEIN_LABELS_FILENAME
    protein_labels = json.loads(protein_path.read_text(encoding="utf-8")) if protein_path.exists() else {}
    return {**protein_labels, **hand_labels}


def _out_of_scope_keys(cur, sql: str, in_scope) -> list[str]:
    return [k for k, *group in cur.execute(sql).fetchall() if not in_scope(*group)]


def _require_rows(rows: list, name: str) -> list:
    if not rows:
        raise ValueError(f"{name} 소스가 비어 있어요 — 전체 카탈로그를 지우지 않도록 적재를 중단합니다")
    return rows


def run_load(conn, norm_dir: Path, enriched_dir: Path, embedded_dir: Path, *,
             coffee_scope: set[str] | None = None, brand_scope: set[str] | None = None,
             milk_labels: dict[str, bool] | None = None) -> dict[str, int]:
    """Upsert the knowledge tables by key in one transaction (a failed load rolls back).

    User tables are never touched; rows that vanished from the source are deleted unless a tasting
    (or a brand bean) still references them — those stay with active = false. An empty coffees or brands
    source is refused rather than wiping the catalog.

    Scoped load (the automated refresh, docs/adr/0015-automated-refresh.md): with `coffee_scope` (a set of
    `coffee_group` ids) only coffees -- and their reviews -- in those groups can be retired; with `brand_scope`
    (brand keys) only those brands' menu items can. Everything else already in the DB is kept as is, and the
    enrich_log is left alone (the run enriched a subset).

    A menu item whose name has no hand milk label (`milk_labels`, default data/curated/menu_milk_labels.yaml)
    is loaded with needs_review = true, which keeps it out of recommendations until someone labels it.
    """
    labels = load_milk_labels(norm_dir=norm_dir) if milk_labels is None else milk_labels
    coffees = _require_rows(read_jsonl(enriched_dir / "coffees.jsonl", CoffeeRecord), "coffees")
    brands = _require_rows(read_jsonl(norm_dir / "brands.jsonl", BrandRecord), "brands")
    cur = conn.cursor()

    taxonomy = sorted(read_jsonl(norm_dir / "taxonomy.jsonl", TaxonomyNode), key=lambda t: t.level)
    for t in taxonomy:
        cur.execute(
            "INSERT INTO flavor_taxonomy (key, parent_id, level, name_en, name_ko) "
            "VALUES (%s, (SELECT id FROM flavor_taxonomy WHERE key = %s), %s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET parent_id = EXCLUDED.parent_id, level = EXCLUDED.level, "
            "name_en = EXCLUDED.name_en, name_ko = EXCLUDED.name_ko",
            (t.key, t.parent_key, t.level, t.name_en, t.name_ko))

    emb_path = embedded_dir / "embeddings.jsonl"
    if not emb_path.exists():
        raise ValueError(f"{emb_path} 가 없어요 — 이 임베딩 모델로 embed 단계를 먼저 돌리세요 (임베딩을 비우지 않도록 적재 중단)")
    vectors = {e["key"]: e["vector"] for e in _read_lines(emb_path)}
    source_keys = [c.key for c in coffees]
    _upsert(cur, "coffees",
            ["key", "name", "roaster", "origin_country", "origin_region", "process", "roast_level", "is_decaf",
             "decaf_process", "acidity", "body", "sweetness", "flavor_tags", "flavor_summary", "embedding",
             "source", "source_url", "collected_at", "altitude_m", "variety", "attr_label_source"],
            [(c.key, c.name, c.roaster, c.origin_country, c.origin_region, c.process, c.roast_level, c.is_decaf,
              c.decaf_process, c.acidity, c.body, c.sweetness, c.flavor_tags, c.flavor_summary,
              to_vector_literal(vectors[c.key]) if c.key in vectors else None, c.source, c.source_url,
              c.collected_at, c.altitude_m, c.variety, Jsonb(c.attr_label_source)) for c in coffees],
            casts={"embedding": "::vector"})
    source_key_set = set(source_keys)
    coffee_ids = _ids(conn, "coffees")

    all_reviews = read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord)
    # Only source coffees keep a review; coffee_ids itself stays unrestricted (it is also used
    # below for menu_item/brand bean FKs, whose stale-but-still-referenced rows must not be nulled
    # out before the protected-delete check runs).
    reviews = [r for r in all_reviews if r.coffee_key in source_key_set]
    _upsert(cur, "reviews", ["key", "coffee_id", "text", "rating", "sub_scores", "source", "source_url", "collected_at"],
            [(r.key, coffee_ids[r.coffee_key], r.text, r.rating, Jsonb(r.sub_scores), r.source, r.source_url,
              r.collected_at) for r in reviews])

    brand_keys = [b.key for b in brands]
    _upsert(cur, "brands", ["key", "name", "decaf_available", "decaf_surcharge_krw", "default_bean_coffee_id",
                            "decaf_bean_coffee_id", "notes", "source_url", "verified_at", "bean", "decaf_bean",
                            "bean_open", "decaf_bean_open"],
            [(b.key, b.name, b.decaf_available, b.decaf_surcharge_krw, coffee_ids.get(b.default_bean_coffee_key),
              coffee_ids.get(b.decaf_bean_coffee_key), b.notes, b.source_url, b.verified_at,
              Jsonb(b.bean.model_dump()) if b.bean else None,
              Jsonb(b.decaf_bean.model_dump()) if b.decaf_bean else None,
              Jsonb(b.bean_open.model_dump()) if b.bean_open else None,
              Jsonb(b.decaf_bean_open.model_dump()) if b.decaf_bean_open else None) for b in brands])
    brand_key_set = set(brand_keys)
    brand_ids = _ids(conn, "brands")

    all_items = read_jsonl(norm_dir / "menu_items.jsonl", MenuItemRecord)
    # As with reviews above: filter against the current source brands, not the (possibly stale) id map.
    items = [m for m in all_items if m.brand_key in brand_key_set]
    _upsert(cur, "menu_items", ["key", "brand_id", "name", "name_en", "category", "is_decaf", "decaf_option",
                                "caffeine_mg", "coffee_id", "source", "source_url", "collected_at", "needs_review"],
            [(m.key, brand_ids[m.brand_key], m.name, m.name_en, m.category, m.is_decaf, m.decaf_option,
              m.caffeine_mg, coffee_ids.get(m.coffee_key), m.source, m.source_url, m.collected_at,
              m.name not in labels) for m in items])

    review_keys, menu_keys, coffee_keys = [r.key for r in reviews], [m.key for m in items], list(source_keys)
    if coffee_scope is not None:
        def in_scope(src, roaster):
            return coffee_group(src, roaster) in coffee_scope
        coffee_keys += _out_of_scope_keys(cur, "SELECT key, source, roaster FROM coffees", in_scope)
        review_keys += _out_of_scope_keys(
            cur, "SELECT r.key, c.source, c.roaster FROM reviews r JOIN coffees c ON c.id = r.coffee_id", in_scope)
    if brand_scope is not None:
        menu_keys += _out_of_scope_keys(
            cur, "SELECT m.key, b.key FROM menu_items m JOIN brands b ON b.id = m.brand_id",
            lambda brand: brand in brand_scope)
    deleted_reviews = _delete_missing(cur, "reviews", review_keys)
    deleted_menu, kept_menu = _retire_missing(cur, "menu_items", menu_keys, PROTECTED_MENU_ITEMS)
    deleted_brands, kept_brands = _retire_missing(cur, "brands", brand_keys, PROTECTED_BRANDS)
    deleted_coffees, kept_coffees = _retire_missing(cur, "coffees", coffee_keys, PROTECTED_COFFEES)

    enrich_log = {e["key"]: e for e in _read_lines(enriched_dir / "cache.jsonl")}
    if coffee_scope is None:
        cur.execute("DELETE FROM enrich_log")
        cur.executemany(
            "INSERT INTO enrich_log (row_ref, stage, status, error, model) VALUES (%s, 'enrich', %s, %s, %s)",
            [(k, e["status"], e.get("error"), e.get("model")) for k, e in enrich_log.items()])

    conn.commit()
    return {"coffees": len(coffees), "reviews": len(reviews), "brands": len(brands), "menu_items": len(items),
            "flavor_taxonomy": len(taxonomy), "enrich_log": len(enrich_log),
            "dropped_reviews": len(all_reviews) - len(reviews), "dropped_menu_items": len(all_items) - len(items),
            "deleted_coffees": deleted_coffees, "deleted_reviews": deleted_reviews,
            "deleted_menu_items": deleted_menu, "deleted_brands": deleted_brands,
            "kept_referenced_coffees": kept_coffees, "kept_referenced_menu_items": kept_menu,
            "kept_referenced_brands": kept_brands,
            "needs_review_menu_items": sum(m.name not in labels for m in items)}
