import json
from pathlib import Path

from psycopg.types.json import Jsonb

from pipeline.db import reset_tables
from pipeline.query import to_vector_literal
from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, TaxonomyNode, read_jsonl


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _ids(conn, table: str) -> dict[str, int]:
    return dict(conn.execute(f"SELECT key, id FROM {table}").fetchall())


def run_load(conn, norm_dir: Path, enriched_dir: Path, embedded_dir: Path) -> dict[str, int]:
    reset_tables(conn)
    cur = conn.cursor()

    taxonomy = sorted(read_jsonl(norm_dir / "taxonomy.jsonl", TaxonomyNode), key=lambda t: t.level)
    for t in taxonomy:
        cur.execute(
            "INSERT INTO flavor_taxonomy (key, parent_id, level, name_en, name_ko) "
            "VALUES (%s, (SELECT id FROM flavor_taxonomy WHERE key = %s), %s, %s, %s)",
            (t.key, t.parent_key, t.level, t.name_en, t.name_ko))

    vectors = {e["key"]: e["vector"] for e in _read_lines(embedded_dir / "embeddings.jsonl")}
    coffees = read_jsonl(enriched_dir / "coffees.jsonl", CoffeeRecord)
    cur.executemany(
        "INSERT INTO coffees (key, name, roaster, origin_country, origin_region, process, roast_level, is_decaf,"
        " decaf_process, acidity, body, sweetness, flavor_tags, flavor_summary, embedding, source, source_url,"
        " collected_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s,%s,%s)",
        [(c.key, c.name, c.roaster, c.origin_country, c.origin_region, c.process, c.roast_level, c.is_decaf,
          c.decaf_process, c.acidity, c.body, c.sweetness, c.flavor_tags, c.flavor_summary,
          to_vector_literal(vectors[c.key]) if c.key in vectors else None, c.source, c.source_url, c.collected_at)
         for c in coffees])
    coffee_ids = _ids(conn, "coffees")

    reviews = [r for r in read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord) if r.coffee_key in coffee_ids]
    cur.executemany(
        "INSERT INTO reviews (key, coffee_id, text, rating, sub_scores, source, source_url, collected_at)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        [(r.key, coffee_ids[r.coffee_key], r.text, r.rating, Jsonb(r.sub_scores), r.source, r.source_url,
          r.collected_at) for r in reviews])

    brands = read_jsonl(norm_dir / "brands.jsonl", BrandRecord)
    cur.executemany(
        "INSERT INTO brands (key, name, decaf_available, decaf_surcharge_krw, default_bean_coffee_id,"
        " decaf_bean_coffee_id, notes, source_url, verified_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [(b.key, b.name, b.decaf_available, b.decaf_surcharge_krw, coffee_ids.get(b.default_bean_coffee_key),
          coffee_ids.get(b.decaf_bean_coffee_key), b.notes, b.source_url, b.verified_at) for b in brands])
    brand_ids = _ids(conn, "brands")

    items = [m for m in read_jsonl(norm_dir / "menu_items.jsonl", MenuItemRecord) if m.brand_key in brand_ids]
    cur.executemany(
        "INSERT INTO menu_items (key, brand_id, name, name_en, category, is_decaf, decaf_option, caffeine_mg,"
        " coffee_id, source_url, collected_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [(m.key, brand_ids[m.brand_key], m.name, m.name_en, m.category, m.is_decaf, m.decaf_option,
          m.caffeine_mg, coffee_ids.get(m.coffee_key), m.source_url, m.collected_at) for m in items])

    log = {e["key"]: e for e in _read_lines(enriched_dir / "cache.jsonl")}
    cur.executemany(
        "INSERT INTO enrich_log (row_ref, stage, status, error, model) VALUES (%s, 'enrich', %s, %s, %s)",
        [(k, e["status"], e.get("error"), e.get("model")) for k, e in log.items()])

    conn.commit()
    return {"coffees": len(coffees), "reviews": len(reviews), "brands": len(brands), "menu_items": len(items),
            "flavor_taxonomy": len(taxonomy), "enrich_log": len(log)}
