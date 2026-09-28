import psycopg
import pytest
from psycopg.types.json import Jsonb

from pipeline.db import apply_schema
from pipeline.query import to_vector_literal
from tests.conftest import TEST_URL


def vec(i):
    v = [0.0] * 1024
    v[i] = 1.0
    return v


def fresh_db(name: str) -> str:
    """An empty database next to coffee_test with the schema applied; returns its URL."""
    assert "test" in name
    base = TEST_URL.rsplit("/", 1)[0]
    with psycopg.connect(base + "/coffee", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")
    url = f"{base}/{name}"
    with psycopg.connect(url) as c:
        apply_schema(c)
    return url


def seed_catalog(c) -> None:
    """A tiny catalog: taxonomy (parent+child), 3 coffees (one coffeereview), a review each, 2 brands, 3 menus."""
    c.execute("INSERT INTO flavor_taxonomy (key, level, name_en, name_ko) VALUES ('sca:fruity', 1, 'fruity', '과일')")
    c.execute("INSERT INTO flavor_taxonomy (key, parent_id, level, name_en) VALUES "
              "('sca:fruity>berry', (SELECT id FROM flavor_taxonomy WHERE key = 'sca:fruity'), 2, 'berry')")
    for i, (key, src, roaster) in enumerate([("cr1", "coffeereview_kaggle", "R"), ("kr1", "roasters_kr", "A"),
                                             ("kr2", "roasters_kr", "A")]):
        c.execute("INSERT INTO coffees (id, key, name, roaster, acidity, flavor_tags, embedding, source, collected_at,"
                  " attr_label_source) VALUES (%s, %s, %s, %s, 3, %s, %s::vector, %s, '2026-09-01', %s)",
                  (100 + i, key, f"Bean {key}", roaster, ["berry"], to_vector_literal(vec(i)), src,
                   Jsonb({"acidity": "gauge"})))
        c.execute("INSERT INTO reviews (key, coffee_id, text, source, collected_at) VALUES "
                  "(%s, (SELECT id FROM coffees WHERE key = %s), 'nice', %s, '2026-09-01')", (f"r-{key}", key, src))
    c.execute("SELECT setval('coffees_id_seq', 200)")
    bean, bean_open = Jsonb({"acidity": 2, "body": 4, "sweetness": 3}), Jsonb({"acidity": 3, "body": 3, "sweetness": 3})
    c.execute("INSERT INTO brands (key, name, decaf_available, verified_at, bean, bean_open) VALUES "
              "('brand:a', 'A', true, '2026-09-01', %s, %s), ('brand:b', 'B', false, '2026-09-01', %s, %s)",
              (bean, bean_open, bean, bean_open))
    for key, brand, name, mg in [("a1", "brand:a", "아메리카노", 150), ("a2", "brand:a", "카페 라떼", 75),
                                 ("b1", "brand:b", "아메리카노", 120)]:
        c.execute("INSERT INTO menu_items (key, brand_id, name, caffeine_mg, collected_at) VALUES "
                  "(%s, (SELECT id FROM brands WHERE key = %s), %s, %s, '2026-09-01')", (key, brand, name, mg))
    c.commit()


@pytest.fixture
def src_conn(db_conn):
    seed_catalog(db_conn)
    return db_conn


@pytest.fixture
def dst_url(db_conn):
    return fresh_db("coffee_test_sync")
