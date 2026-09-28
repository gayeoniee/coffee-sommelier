"""Copy the CATALOG tables of one Postgres into another by key -- never the user tables.

    SYNC_SRC_URL=postgresql://... SYNC_DST_URL=postgresql://... uv run python -m scripts.refresh.catalog_sync \
        [--variant open] [--dry-run] [--apply-schema]

Used by the automated refresh (docs/adr/0015-automated-refresh.md) for three things:
  1. clone the current catalog (production Neon, or the local dev DB) into an empty staging DB,
  2. derive the open-data staging DB from the full one (`--variant open`: the build_open_db.sh logic -- no
     coffeereview_kaggle coffees or reviews, brands.bean/decaf_bean overwritten with the licence-clean *_open values),
  3. publish the gated staging DBs to production -- and `scripts/deploy/migrate_to_neon.sh MODE=catalog`.

What it does, per catalog table (flavor_taxonomy, coffees, brands, reviews, menu_items):
  - rows are matched by `key`; foreign keys travel as the referenced row's key, so ids may differ between DBs
  - only rows whose content differs (md5 of the row, computed in SQL on each side) are written, so a weekly
    publish writes the handful of changed rows instead of rewriting every embedding (Neon storage/egress)
  - a new row keeps its source id when that id is free in the target (the fixed LOO/held-out target ids in
    data/eval stay valid on a clone)
  - target rows whose key is no longer an active source row are deleted, unless a user's tasting (or a kept menu
    item / brand bean) still points at them -- those stay with active = false, the same rule as pipeline/load.py
  - everything happens in one transaction; users, taste_profiles, tastings and profile_history are never written

URLs come from the environment (SYNC_SRC_URL / SYNC_DST_URL), never the command line, so they stay out of logs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

import psycopg

from pipeline.db import apply_schema
from pipeline.load import PROTECTED_BRANDS, PROTECTED_COFFEES, PROTECTED_MENU_ITEMS

# (table, {fk column: referenced table}) in foreign-key order
TABLES: list[tuple[str, dict[str, str]]] = [
    ("flavor_taxonomy", {"parent_id": "flavor_taxonomy"}),
    ("coffees", {}),
    ("brands", {"default_bean_coffee_id": "coffees", "decaf_bean_coffee_id": "coffees"}),
    ("reviews", {"coffee_id": "coffees"}),
    ("menu_items", {"brand_id": "brands", "coffee_id": "coffees"}),
]
PROTECTED = {"coffees": PROTECTED_COFFEES, "brands": PROTECTED_BRANDS, "menu_items": PROTECTED_MENU_ITEMS}
NEVER_RETIRED = {"flavor_taxonomy"}          # the SCA wheel only grows; a vanished node is harmless
OPEN_EXCLUDED_SOURCES = ("coffeereview_kaggle",)
CHUNK = 500


@dataclass
class TablePlan:
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    delete: int = 0
    retire: int = 0                            # kept with active = false (still referenced)
    new_keys: list[str] = field(default_factory=list, repr=False)
    changed_keys: list[str] = field(default_factory=list, repr=False)
    delete_keys: list[str] = field(default_factory=list, repr=False)
    retire_keys: list[str] = field(default_factory=list, repr=False)

    def summary(self) -> dict[str, int]:
        return {"new": self.new, "changed": self.changed, "unchanged": self.unchanged,
                "delete": self.delete, "retire": self.retire}


def _columns(conn, table: str) -> dict[str, str]:
    rows = conn.execute("SELECT column_name, udt_name FROM information_schema.columns"
                        " WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position",
                        (table,)).fetchall()
    return {c: t for c, t in rows}


def _source_filter(table: str, variant: str) -> str:
    if variant != "open":
        return ""
    xs = ", ".join(f"'{s}'" for s in OPEN_EXCLUDED_SOURCES)
    if table == "coffees":
        return f" AND t.source NOT IN ({xs})"
    if table == "reviews":
        return f" AND (SELECT c.source FROM coffees c WHERE c.id = t.coffee_id) NOT IN ({xs})"
    return ""


def _expr(table: str, col: str, fks: dict[str, str], variant: str, types: dict[str, str]) -> str:
    if col in fks:
        return f"(SELECT r.key FROM {fks[col]} r WHERE r.id = t.{col})"
    src_col = col
    if variant == "open" and table == "brands" and col in ("bean", "decaf_bean"):
        src_col = f"{col}_open"                # build_open_db.sh: UPDATE brands SET bean = bean_open, ...
    return f"t.{src_col}::text" if types.get(col) in ("jsonb", "vector") else f"t.{src_col}"


class Side:
    """One database's view of a table: which columns are synced and the SELECT expressions for them."""

    def __init__(self, conn, table: str, fks: dict[str, str], cols: list[str], variant: str = "full"):
        self.conn, self.table, self.fks, self.cols, self.variant = conn, table, fks, cols, variant
        self.types = _columns(conn, table)
        self.has_active = "active" in self.types
        self.exprs = [_expr(table, c, fks, variant, self.types) for c in cols]

    def where(self) -> str:
        return ("WHERE t.active" if self.has_active else "WHERE true") + _source_filter(self.table, self.variant)

    def hashes(self, active_only: bool = True) -> dict[str, str]:
        where = self.where() if active_only else "WHERE true"
        sql = f"SELECT t.key, md5(ROW({', '.join(self.exprs)})::text) FROM {self.table} t {where}"
        return dict(self.conn.execute(sql).fetchall())

    def rows(self, keys: list[str]) -> list[tuple]:
        out = []
        for i in range(0, len(keys), CHUNK):
            out += self.conn.execute(f"SELECT t.id, {', '.join(self.exprs)} FROM {self.table} t WHERE t.key = ANY(%s)"
                                     " ORDER BY t.id", (keys[i:i + CHUNK],)).fetchall()
        return out


def _synced_columns(src, dst, table: str, variant: str) -> list[str]:
    s, d = _columns(src, table), _columns(dst, table)
    if variant == "open" and table == "brands":
        s = {**s, "bean": s.get("bean_open"), "decaf_bean": s.get("decaf_bean_open")}
    return [c for c in d if c in s and c not in ("id", "active")]


def plan_table(src, dst, table: str, fks: dict[str, str], variant: str) -> tuple[TablePlan, Side, Side]:
    cols = _synced_columns(src, dst, table, variant)
    s, d = Side(src, table, fks, cols, variant), Side(dst, table, fks, cols)
    src_h, dst_h = s.hashes(), d.hashes(active_only=False)
    dst_active = d.hashes() if d.has_active else dst_h
    p = TablePlan()
    for k, h in src_h.items():
        if k not in dst_h:
            p.new_keys.append(k)
        elif dst_h[k] != h or k not in dst_active:     # content changed, or a retired row came back
            p.changed_keys.append(k)
        else:
            p.unchanged += 1
    gone = sorted(k for k in dst_active if k not in src_h)
    if table not in NEVER_RETIRED and gone:
        if table in PROTECTED:
            kept = {k for (k,) in dst.execute(f"SELECT key FROM {table} WHERE key = ANY(%s) AND id IN ({PROTECTED[table]})",
                                              (gone,)).fetchall()}
        else:
            kept = set()
        p.retire_keys = sorted(kept)
        p.delete_keys = [k for k in gone if k not in kept]
    p.new, p.changed, p.delete, p.retire = len(p.new_keys), len(p.changed_keys), len(p.delete_keys), len(p.retire_keys)
    return p, s, d


def _write_rows(dst, table: str, s: Side, d: Side, keys: list[str]) -> None:
    if not keys:
        return
    rows = s.rows(keys)
    if table == "flavor_taxonomy":             # parents before children
        level = s.cols.index("level")
        rows.sort(key=lambda r: r[1 + level])
    taken = {i for (i,) in dst.execute(f"SELECT id FROM {table}").fetchall()}

    def value(col: str) -> str:
        if col in s.fks:
            return f"(SELECT id FROM {s.fks[col]} WHERE key = %s)"
        return f"%s::{d.types[col]}" if d.types.get(col) in ("jsonb", "vector") else "%s"

    names = ", ".join(s.cols)
    values = ", ".join(value(c) for c in s.cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in s.cols if c != "key")
    if d.has_active:
        updates += ", active = true"
    conflict = f"ON CONFLICT (key) DO UPDATE SET {updates}"
    with_id = f"INSERT INTO {table} (id, {names}) VALUES (%s, {values}) {conflict}"
    without_id = f"INSERT INTO {table} ({names}) VALUES ({values}) {conflict}"
    cur = dst.cursor()
    if table == "flavor_taxonomy":             # row by row: a child needs its parent inserted first
        for r in rows:
            if r[0] in taken:
                cur.execute(without_id, r[1:])
            else:
                cur.execute(with_id, r)
                taken.add(r[0])
    else:                                      # executemany pipelines the statements: one round trip per batch
        free = [r for r in rows if r[0] not in taken]
        cur.executemany(with_id, free)
        cur.executemany(without_id, [r[1:] for r in rows if r[0] in taken])
    dst.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), greatest((SELECT max(id) FROM {table}), 1))")


def _retire(dst, table: str, p: TablePlan, has_active: bool) -> None:
    if p.delete_keys:
        dst.execute(f"DELETE FROM {table} WHERE key = ANY(%s)", (p.delete_keys,))
    if p.retire_keys and has_active:
        dst.execute(f"UPDATE {table} SET active = false WHERE key = ANY(%s) AND active", (p.retire_keys,))


def sync(src, dst, variant: str = "full", dry_run: bool = False) -> dict[str, dict[str, int]]:
    """Make dst's catalog equal to src's (see module doc). Returns the per-table plan; dry_run writes nothing."""
    plans = {}
    for table, fks in TABLES:
        p, s, d = plan_table(src, dst, table, fks, variant)
        plans[table] = (p, s, d)
        if not dry_run:
            _write_rows(dst, table, s, d, p.new_keys + p.changed_keys)
    if not dry_run:
        for table, _ in reversed(TABLES):
            p, _s, d = plans[table]
            _retire(dst, table, p, d.has_active)
        dst.commit()
    else:
        dst.rollback()
    return {t: p.summary() for t, (p, _s, _d) in plans.items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--variant", choices=("full", "open"), default="full")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    ap.add_argument("--apply-schema", action="store_true", help="run db/schema.sql on the target first (idempotent)")
    a = ap.parse_args(argv)
    src_url, dst_url = os.environ.get("SYNC_SRC_URL"), os.environ.get("SYNC_DST_URL")
    if not src_url or not dst_url:
        print("SYNC_SRC_URL과 SYNC_DST_URL을 환경 변수로 지정하세요", file=sys.stderr)
        return 2
    with psycopg.connect(src_url) as src, psycopg.connect(dst_url) as dst:
        if a.apply_schema and not a.dry_run:
            apply_schema(dst)
        plan = sync(src, dst, variant=a.variant, dry_run=a.dry_run)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
