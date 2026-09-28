import logging
from pathlib import Path

from psycopg.types.json import Jsonb

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


def _require_rows(rows: list, name: str) -> list:
    if not rows:
        raise ValueError(f"{name} 소스가 비어 있어요 — 전체 카탈로그를 지우지 않도록 적재를 중단합니다")
    return rows


def run_load(conn, norm_dir: Path, enriched_dir: Path, embedded_dir: Path) -> dict[str, int]:
    """Upsert the knowledge tables by key in one transaction (a failed load rolls back).

    User tables are never touched; rows that vanished from the source are deleted unless a tasting
    (or a brand bean) still references them — those stay with active = false. An empty coffees or brands
    source is refused rather than wiping the catalog.
    """
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
                                "caffeine_mg", "coffee_id", "source_url", "collected_at"],
            [(m.key, brand_ids[m.brand_key], m.name, m.name_en, m.category, m.is_decaf, m.decaf_option,
              m.caffeine_mg, coffee_ids.get(m.coffee_key), m.source_url, m.collected_at) for m in items])

    deleted_reviews = _delete_missing(cur, "reviews", [r.key for r in reviews])
    deleted_menu, kept_menu = _retire_missing(cur, "menu_items", [m.key for m in items], PROTECTED_MENU_ITEMS)
    deleted_brands, kept_brands = _retire_missing(cur, "brands", brand_keys, PROTECTED_BRANDS)
    deleted_coffees, kept_coffees = _retire_missing(cur, "coffees", source_keys, PROTECTED_COFFEES)

    enrich_log = {e["key"]: e for e in _read_lines(enriched_dir / "cache.jsonl")}
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
            "kept_referenced_brands": kept_brands}
