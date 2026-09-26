from pathlib import Path

from psycopg.types.json import Jsonb

from pipeline.enrich import read_json_lines
from pipeline.query import to_vector_literal
from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, TaxonomyNode, read_jsonl

# Rows a user's tasting points at are never deleted, even if they vanish from the source.
PROTECTED_COFFEES = ("SELECT coffee_id FROM tastings WHERE coffee_id IS NOT NULL "
                     "UNION SELECT default_bean_coffee_id FROM brands WHERE default_bean_coffee_id IS NOT NULL "
                     "UNION SELECT decaf_bean_coffee_id FROM brands WHERE decaf_bean_coffee_id IS NOT NULL")
PROTECTED_MENU_ITEMS = "SELECT menu_item_id FROM tastings WHERE menu_item_id IS NOT NULL"
PROTECTED_BRANDS = f"SELECT brand_id FROM menu_items WHERE id IN ({PROTECTED_MENU_ITEMS})"


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


def run_load(conn, norm_dir: Path, enriched_dir: Path, embedded_dir: Path) -> dict[str, int]:
    """Upsert the knowledge tables by key in one transaction (a failed load rolls back).

    User tables are never touched; rows that vanished from the source are deleted unless a tasting
    (or a brand bean) still references them.
    """
    cur = conn.cursor()

    taxonomy = sorted(read_jsonl(norm_dir / "taxonomy.jsonl", TaxonomyNode), key=lambda t: t.level)
    for t in taxonomy:
        cur.execute(
            "INSERT INTO flavor_taxonomy (key, parent_id, level, name_en, name_ko) "
            "VALUES (%s, (SELECT id FROM flavor_taxonomy WHERE key = %s), %s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET parent_id = EXCLUDED.parent_id, level = EXCLUDED.level, "
            "name_en = EXCLUDED.name_en, name_ko = EXCLUDED.name_ko",
            (t.key, t.parent_key, t.level, t.name_en, t.name_ko))

    vectors = {e["key"]: e["vector"] for e in _read_lines(embedded_dir / "embeddings.jsonl")}
    coffees = read_jsonl(enriched_dir / "coffees.jsonl", CoffeeRecord)
    _upsert(cur, "coffees",
            ["key", "name", "roaster", "origin_country", "origin_region", "process", "roast_level", "is_decaf",
             "decaf_process", "acidity", "body", "sweetness", "flavor_tags", "flavor_summary", "embedding",
             "source", "source_url", "collected_at"],
            [(c.key, c.name, c.roaster, c.origin_country, c.origin_region, c.process, c.roast_level, c.is_decaf,
              c.decaf_process, c.acidity, c.body, c.sweetness, c.flavor_tags, c.flavor_summary,
              to_vector_literal(vectors[c.key]) if c.key in vectors else None, c.source, c.source_url,
              c.collected_at) for c in coffees],
            casts={"embedding": "::vector"})
    coffee_ids = _ids(conn, "coffees")

    all_reviews = read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord)
    reviews = [r for r in all_reviews if r.coffee_key in coffee_ids]
    _upsert(cur, "reviews", ["key", "coffee_id", "text", "rating", "sub_scores", "source", "source_url", "collected_at"],
            [(r.key, coffee_ids[r.coffee_key], r.text, r.rating, Jsonb(r.sub_scores), r.source, r.source_url,
              r.collected_at) for r in reviews])

    brands = read_jsonl(norm_dir / "brands.jsonl", BrandRecord)
    _upsert(cur, "brands", ["key", "name", "decaf_available", "decaf_surcharge_krw", "default_bean_coffee_id",
                            "decaf_bean_coffee_id", "notes", "source_url", "verified_at"],
            [(b.key, b.name, b.decaf_available, b.decaf_surcharge_krw, coffee_ids.get(b.default_bean_coffee_key),
              coffee_ids.get(b.decaf_bean_coffee_key), b.notes, b.source_url, b.verified_at) for b in brands])
    brand_ids = _ids(conn, "brands")

    all_items = read_jsonl(norm_dir / "menu_items.jsonl", MenuItemRecord)
    items = [m for m in all_items if m.brand_key in brand_ids]
    _upsert(cur, "menu_items", ["key", "brand_id", "name", "name_en", "category", "is_decaf", "decaf_option",
                                "caffeine_mg", "coffee_id", "source_url", "collected_at"],
            [(m.key, brand_ids[m.brand_key], m.name, m.name_en, m.category, m.is_decaf, m.decaf_option,
              m.caffeine_mg, coffee_ids.get(m.coffee_key), m.source_url, m.collected_at) for m in items])

    deleted_reviews = _delete_missing(cur, "reviews", [r.key for r in reviews])
    deleted_menu = _delete_missing(cur, "menu_items", [m.key for m in items], PROTECTED_MENU_ITEMS)
    deleted_brands = _delete_missing(cur, "brands", [b.key for b in brands], PROTECTED_BRANDS)
    source_keys = [c.key for c in coffees]
    stale = cur.execute("SELECT count(*) FROM coffees WHERE NOT (key = ANY(%s))", (source_keys,)).fetchone()[0]
    deleted_coffees = _delete_missing(cur, "coffees", source_keys, PROTECTED_COFFEES)

    log = {e["key"]: e for e in _read_lines(enriched_dir / "cache.jsonl")}
    cur.execute("DELETE FROM enrich_log")
    cur.executemany(
        "INSERT INTO enrich_log (row_ref, stage, status, error, model) VALUES (%s, 'enrich', %s, %s, %s)",
        [(k, e["status"], e.get("error"), e.get("model")) for k, e in log.items()])

    conn.commit()
    return {"coffees": len(coffees), "reviews": len(reviews), "brands": len(brands), "menu_items": len(items),
            "flavor_taxonomy": len(taxonomy), "enrich_log": len(log),
            "dropped_reviews": len(all_reviews) - len(reviews), "dropped_menu_items": len(all_items) - len(items),
            "deleted_coffees": deleted_coffees, "deleted_reviews": deleted_reviews,
            "deleted_menu_items": deleted_menu, "deleted_brands": deleted_brands,
            "kept_referenced_coffees": stale - deleted_coffees}
