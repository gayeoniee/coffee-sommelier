"""Build config/tag_cooc_open.json -- the open variant's flavor-tag co-occurrence table (app/core/tagcooc.py,
docs/adr/0018-open-tag-cooccurrence.md). The only writer of that file.

Beans: active coffee_open rows from the licence-clean note sources (COOC_SOURCES: roasters_kr incl. Blue Bottle Korea,
shopify, shopify_gauged) -- never coffeereview, RoasterDB (CC BY-NC) or the Zenodo panel (evaluation only). A bean's
tags = its stored flavor_tags plus what the current rule mapper (pipeline.enrich rule_tags + KO_TAG_ALIASES, via
app.core.textcues.text_tags) reads in its note summary, so aliases added since the last enrich already count.

    uv run python scripts/build_tag_cooc.py [--db postgresql://...coffee_open]
"""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.tagcooc import COOC_FILE, TagCooc  # noqa: E402
from pipeline import settings  # noqa: E402

COOC_SOURCES = ("roasters_kr", "shopify", "shopify_gauged")
OPEN_URL = os.getenv("OPEN_DATABASE_URL", "postgresql://coffee:coffee@localhost:5432/coffee_open")


def parent_map(conn) -> dict[str, str]:
    """Level-3 SCA tag -> its level-2 node name (flavor_taxonomy keys "sca:cat>node>leaf"); leaf == node skipped."""
    out = {}
    for r in conn.execute("SELECT key, level, name_en FROM flavor_taxonomy WHERE level = 3").fetchall():
        node = r["key"].split(">")[1].lower()
        if node != r["name_en"].lower():
            out[r["name_en"].lower()] = node
    return out


def bean_tag_rows(conn, tag_to_cat: dict[str, str], tag_ko: dict[str, str]) -> list[dict]:
    """[{id, roaster, source, tags}] for every active licence-clean bean with at least one tag."""
    from app.core.textcues import text_tags
    rows = conn.execute(
        "SELECT id, roaster, source, flavor_summary, flavor_tags FROM coffees WHERE active AND source = ANY(%s)"
        " ORDER BY key", (list(COOC_SOURCES),)).fetchall()
    out = []
    for r in rows:
        tags = {t.lower() for t in r["flavor_tags"] or ()}
        if r["flavor_summary"]:
            tags |= {t.lower() for t in text_tags(r["flavor_summary"], tag_to_cat, tag_ko, limit=12)}
        if tags:
            out.append({"id": r["id"], "roaster": r["roaster"], "source": r["source"], "tags": tags})
    return out


def build(db: str = OPEN_URL) -> tuple[TagCooc, dict]:
    from app.repo import Repo
    repo = Repo(db)
    try:
        tag_to_cat, tag_ko = repo.taxonomy()
    finally:
        repo.close()
    with psycopg.connect(db, row_factory=dict_row) as conn:
        rows = bean_tag_rows(conn, tag_to_cat, tag_ko)
        parent = parent_map(conn)
    cooc = TagCooc.from_beans([r["tags"] for r in rows], parent)
    meta = {"sources": list(COOC_SOURCES), "n_beans": cooc.n_beans,
            "n_roasters": len({r["roaster"] for r in rows}),
            "by_source": {s: sum(1 for r in rows if r["source"] == s) for s in COOC_SOURCES}}
    return cooc, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=OPEN_URL)
    ap.add_argument("--out", type=Path, default=settings.CONFIG_DIR / COOC_FILE)
    args = ap.parse_args()
    cooc, meta = build(args.db)
    doc = {"what": "open-variant flavor-tag co-occurrence, docs/adr/0018-open-tag-cooccurrence.md; "
                   "written by scripts/build_tag_cooc.py only", **meta, **cooc.to_doc()}
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {cooc.n_beans} beans, {len(cooc.tag_count)} tags, {meta['n_roasters']} roasters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
