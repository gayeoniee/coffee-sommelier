import psycopg
import pytest

from scripts.refresh.catalog_sync import sync

pytestmark = pytest.mark.db


def keys(conn, table, where="true"):
    return {k for (k,) in conn.execute(f"SELECT key FROM {table} WHERE {where}").fetchall()}


def test_clone_copies_the_catalog_with_ids_and_fks(src_conn, dst_url):
    with psycopg.connect(dst_url) as dst:
        plan = sync(src_conn, dst)
        assert plan["coffees"] == {"new": 3, "changed": 0, "unchanged": 0, "delete": 0, "retire": 0}
        assert dict(dst.execute("SELECT key, id FROM coffees").fetchall()) == {"cr1": 100, "kr1": 101, "kr2": 102}
        assert dst.execute("SELECT nextval('coffees_id_seq')").fetchone()[0] > 102
        assert dst.execute("SELECT p.key FROM flavor_taxonomy c JOIN flavor_taxonomy p ON p.id = c.parent_id"
                           ).fetchone() == ("sca:fruity",)
        assert dst.execute("SELECT b.key FROM menu_items m JOIN brands b ON b.id = m.brand_id WHERE m.key = 'b1'"
                           ).fetchone() == ("brand:b",)
        assert dst.execute("SELECT vector_dims(embedding) FROM coffees WHERE key = 'kr1'").fetchone() == (1024,)
        # a second sync has nothing to do
        again = sync(src_conn, dst)
        assert all(t["new"] == t["changed"] == t["delete"] == 0 for t in again.values())


def test_only_changed_rows_are_written_and_users_are_never_touched(src_conn, dst_url):
    with psycopg.connect(dst_url) as dst:
        sync(src_conn, dst)
        uid = dst.execute("INSERT INTO users DEFAULT VALUES RETURNING id").fetchone()[0]
        dst.execute("INSERT INTO tastings (user_id, menu_item_id, rating) VALUES "
                    "(%s, (SELECT id FROM menu_items WHERE key = 'a2'), 5)", (uid,))
        dst.execute("INSERT INTO tastings (user_id, coffee_id, rating) VALUES "
                    "(%s, (SELECT id FROM coffees WHERE key = 'kr2'), 4)", (uid,))
        dst.commit()
        src_conn.execute("UPDATE menu_items SET caffeine_mg = 160 WHERE key = 'a1'")
        src_conn.execute("DELETE FROM menu_items WHERE key IN ('a2', 'b1')")
        src_conn.execute("DELETE FROM coffees WHERE key IN ('kr1', 'kr2')")
        src_conn.execute("INSERT INTO coffees (key, name, source, collected_at) VALUES ('kr3', 'New', 'roasters_kr', "
                         "'2026-09-28')")
        src_conn.commit()
        plan = sync(src_conn, dst)
        assert plan["menu_items"] == {"new": 0, "changed": 1, "unchanged": 0, "delete": 1, "retire": 1}
        assert plan["coffees"] == {"new": 1, "changed": 0, "unchanged": 1, "delete": 1, "retire": 1}
        assert dst.execute("SELECT caffeine_mg FROM menu_items WHERE key = 'a1'").fetchone() == (160,)
        assert dict(dst.execute("SELECT key, active FROM menu_items").fetchall()) == {"a1": True, "a2": False}
        assert dict(dst.execute("SELECT key, active FROM coffees").fetchall()) == {
            "cr1": True, "kr2": False, "kr3": True}                     # kr2: a tasting points at it
        assert dst.execute("SELECT count(*) FROM tastings").fetchone() == (2,)
        assert dst.execute("SELECT count(*) FROM users").fetchone() == (1,)
        # the retired row comes back when the source has it again
        src_conn.execute("INSERT INTO coffees (key, name, source, collected_at) VALUES ('kr2', 'Bean kr2', "
                         "'roasters_kr', '2026-09-01')")
        src_conn.commit()
        assert sync(src_conn, dst)["coffees"]["changed"] == 1
        assert dst.execute("SELECT active FROM coffees WHERE key = 'kr2'").fetchone() == (True,)


def test_open_variant_drops_coffeereview_and_uses_open_bean_profiles(src_conn, dst_url):
    with psycopg.connect(dst_url) as dst:
        sync(src_conn, dst, variant="open")
        assert keys(dst, "coffees") == {"kr1", "kr2"}
        assert keys(dst, "reviews") == {"r-kr1", "r-kr2"}
        bean, bean_open = dst.execute("SELECT bean, bean_open FROM brands WHERE key = 'brand:a'").fetchone()
        assert bean == bean_open == {"acidity": 3, "body": 3, "sweetness": 3}


def test_dry_run_writes_nothing(src_conn, dst_url):
    with psycopg.connect(dst_url) as dst:
        plan = sync(src_conn, dst, dry_run=True)
        assert plan["menu_items"]["new"] == 3
        assert dst.execute("SELECT count(*) FROM menu_items").fetchone() == (0,)
