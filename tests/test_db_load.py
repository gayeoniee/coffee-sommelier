import pytest

from pipeline.load import run_load
from pipeline.query import similar, to_vector_literal
from pipeline.records import (
    BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, TaxonomyNode, write_jsonl,
)
import json

pytestmark = pytest.mark.db


def vec(i):
    v = [0.0] * 1024
    v[i] = 1.0
    return v


def setup_files(tmp_path):
    norm, enriched, embedded = tmp_path / "n", tmp_path / "e", tmp_path / "m"
    coffees = [
        CoffeeRecord(key="c1", name="Ethiopia Washed", origin_country="Ethiopia", is_decaf=False,
                     acidity=5, flavor_tags=["lemon"], source="t", collected_at="2026-09-24"),
        CoffeeRecord(key="c2", name="Ethiopia Decaf", origin_country="Ethiopia", is_decaf=True,
                     decaf_process="swiss-water", source="t", collected_at="2026-09-24"),
    ]
    write_jsonl(enriched / "coffees.jsonl", coffees)
    (enriched / "cache.jsonl").write_text(
        json.dumps({"key": "c1", "hash": "h", "status": "failed", "error": "x", "model": None}) + "\n", encoding="utf-8")
    embedded.mkdir(parents=True)
    (embedded / "embeddings.jsonl").write_text(
        "\n".join(json.dumps({"key": k, "hash": "h", "vector": vec(i)}) for i, k in enumerate(["c1", "c2"])) + "\n",
        encoding="utf-8")
    write_jsonl(norm / "reviews.jsonl", [ReviewRecord(key="r1", coffee_key="c1", text="t", source="t", collected_at="2026-09-24")])
    write_jsonl(norm / "brands.jsonl", [BrandRecord(key="brand:x", name="X", decaf_available=True, verified_at="2026-09-24")])
    write_jsonl(norm / "menu_items.jsonl", [MenuItemRecord(key="m1", brand_key="brand:x", name="아메리카노",
                                                           caffeine_mg=150, collected_at="2026-09-24")])
    write_jsonl(norm / "taxonomy.jsonl", [
        TaxonomyNode(key="sca:fruity", level=1, name_en="fruity", name_ko="과일"),
        TaxonomyNode(key="sca:fruity>berry", parent_key="sca:fruity", level=2, name_en="berry"),
    ])
    return norm, enriched, embedded


class FixedEmbedder:
    def embed(self, texts):
        return [vec(1) for _ in texts]


def test_load_and_query(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    counts = run_load(db_conn, norm, enriched, embedded)
    assert counts == {"coffees": 2, "reviews": 1, "brands": 1, "menu_items": 1, "flavor_taxonomy": 2,
                      "enrich_log": 1, "dropped_reviews": 0, "dropped_menu_items": 0,
                      "deleted_coffees": 0, "deleted_reviews": 0, "deleted_menu_items": 0, "deleted_brands": 0,
                      "kept_referenced_coffees": 0}
    parent = db_conn.execute("SELECT p.key FROM flavor_taxonomy c JOIN flavor_taxonomy p ON c.parent_id = p.id").fetchone()
    assert parent == ("sca:fruity",)
    hits = similar(db_conn, FixedEmbedder(), "decaf ethiopia", k=2)
    assert [h["name"] for h in hits] == ["Ethiopia Decaf", "Ethiopia Washed"]
    assert [h["name"] for h in similar(db_conn, FixedEmbedder(), "x", decaf=False)] == ["Ethiopia Washed"]
    # reload is idempotent
    assert run_load(db_conn, norm, enriched, embedded)["coffees"] == 2


def test_to_vector_literal():
    assert to_vector_literal([1, 0.5]) == "[1.0,0.5]"


from pipeline.report import build_report


def test_report_counts_and_missing_rates(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    md = build_report(db_conn, {"enrich": {"llm_calls": 1}})
    assert "| coffees | 2 |" in md
    assert "| decaf coffees | 1 |" in md
    assert "| body | 100.0% |" in md          # both coffees lack body
    assert "| acidity | 50.0% |" in md
    assert "| t | 2 |" in md                  # per-source count
    assert "enrich failed: 1" in md
    assert "llm_calls" in md


def test_load_tolerates_torn_cache_line(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    with (enriched / "cache.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"key": "c2", "hash": "h", "sta')
    assert run_load(db_conn, norm, enriched, embedded)["enrich_log"] == 1


def test_failed_load_keeps_previous_data(db_conn, tmp_path, monkeypatch):
    import pipeline.load as load_mod

    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    real = load_mod.read_jsonl

    def boom(path, model):
        if path.name == "brands.jsonl":
            raise RuntimeError("disk gone")
        return real(path, model)

    monkeypatch.setattr(load_mod, "read_jsonl", boom)
    with pytest.raises(RuntimeError):
        run_load(db_conn, norm, enriched, embedded)
    db_conn.rollback()
    assert db_conn.execute("SELECT count(*) FROM coffees").fetchone() == (2,)
    assert db_conn.execute("SELECT count(*) FROM brands").fetchone() == (1,)


def test_filtered_query_returns_k_rows_through_hnsw(db_conn):
    import random

    rng = random.Random(0)
    rows = []
    for i in range(300):
        v = [rng.uniform(-1, 1) for _ in range(1024)]
        rows.append((f"k{i}", f"coffee {i}", i < 5, to_vector_literal(v)))
    db_conn.cursor().executemany(
        "INSERT INTO coffees (key, name, is_decaf, embedding, source, collected_at)"
        " VALUES (%s, %s, %s, %s::vector, 't', '2026-09-24')", rows)
    db_conn.commit()
    db_conn.execute("SET enable_seqscan = off")  # force the HNSW index (not the is_decaf btree + sort)
    db_conn.execute("SET enable_sort = off")

    class RandomEmbedder:
        def embed(self, texts):
            return [[rng.uniform(-1, 1) for _ in range(1024)] for _ in texts]

    hits = similar(db_conn, RandomEmbedder(), "decaf", k=5, decaf=True)
    assert len(hits) == 5 and all(h["is_decaf"] for h in hits)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def _add_tasting(conn, coffee_key=None, menu_key=None):
    uid = conn.execute("INSERT INTO users DEFAULT VALUES RETURNING id").fetchone()[0]
    cid = conn.execute("SELECT id FROM coffees WHERE key = %s", (coffee_key,)).fetchone()[0] if coffee_key else None
    mid = conn.execute("SELECT id FROM menu_items WHERE key = %s", (menu_key,)).fetchone()[0] if menu_key else None
    conn.execute("INSERT INTO tastings (user_id, coffee_id, menu_item_id, rating) VALUES (%s, %s, %s, 4)", (uid, cid, mid))
    conn.commit()
    return uid


def test_reload_keeps_ids_and_user_data(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    ids_before = dict(db_conn.execute("SELECT key, id FROM coffees").fetchall())
    uid = _add_tasting(db_conn, coffee_key="c1")
    _add_tasting(db_conn, menu_key="m1")
    run_load(db_conn, norm, enriched, embedded)
    assert dict(db_conn.execute("SELECT key, id FROM coffees").fetchall()) == ids_before
    assert db_conn.execute("SELECT count(*) FROM tastings").fetchone()[0] == 2
    assert db_conn.execute("SELECT count(*) FROM users WHERE id = %s", (uid,)).fetchone()[0] == 1


def test_reload_deletes_missing_rows_but_keeps_referenced(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    _add_tasting(db_conn, coffee_key="c1")
    write_jsonl(enriched / "coffees.jsonl", [])          # both coffees vanish from the source
    write_jsonl(norm / "reviews.jsonl", [])
    counts = run_load(db_conn, norm, enriched, embedded)
    assert counts["deleted_coffees"] == 1                # c2 deleted
    assert counts["kept_referenced_coffees"] == 1        # c1 kept: a tasting points at it
    assert counts["deleted_reviews"] == 1
    keys = {k for (k,) in db_conn.execute("SELECT key FROM coffees").fetchall()}
    assert keys == {"c1"}


def test_reload_protects_coffee_referenced_by_kept_menu_item(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    write_jsonl(norm / "menu_items.jsonl", [MenuItemRecord(key="m1", brand_key="brand:x", name="아메리카노",
                                                           coffee_key="c1", caffeine_mg=150, collected_at="2026-09-24")])
    run_load(db_conn, norm, enriched, embedded)
    write_jsonl(enriched / "coffees.jsonl", [
        CoffeeRecord(key="c2", name="Ethiopia Decaf", origin_country="Ethiopia", is_decaf=True,
                     decaf_process="swiss-water", source="t", collected_at="2026-09-24")])   # c1 vanishes from source
    counts = run_load(db_conn, norm, enriched, embedded)                                     # must not raise
    assert counts["kept_referenced_coffees"] == 1        # c1 kept: m1.coffee_id still points at it
    assert counts["deleted_coffees"] == 0
    keys = {k for (k,) in db_conn.execute("SELECT key FROM coffees").fetchall()}
    assert keys == {"c1", "c2"}


def test_reload_drops_review_for_vanished_unreferenced_coffee(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    write_jsonl(enriched / "coffees.jsonl", [
        CoffeeRecord(key="c2", name="Ethiopia Decaf", origin_country="Ethiopia", is_decaf=True,
                     decaf_process="swiss-water", source="t", collected_at="2026-09-24")])   # c1 vanishes, unreferenced
    counts = run_load(db_conn, norm, enriched, embedded)                                    # reviews.jsonl still has r1 (coffee_key c1)
    assert counts["dropped_reviews"] == 1
    assert counts["reviews"] == 0
    assert db_conn.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0


def test_updated_values_are_written(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    write_jsonl(enriched / "coffees.jsonl", [
        CoffeeRecord(key="c1", name="Ethiopia Washed v2", origin_country="Ethiopia", acidity=3,
                     source="t", collected_at="2026-09-26"),
        CoffeeRecord(key="c2", name="Ethiopia Decaf", origin_country="Ethiopia", is_decaf=True,
                     source="t", collected_at="2026-09-24")])
    run_load(db_conn, norm, enriched, embedded)
    assert db_conn.execute("SELECT name, acidity FROM coffees WHERE key = 'c1'").fetchone() == ("Ethiopia Washed v2", 3)


def test_brand_bean_is_loaded(db_conn, tmp_path):
    from pipeline.records import BeanProfile
    norm, enriched, embedded = setup_files(tmp_path)
    write_jsonl(norm / "brands.jsonl", [BrandRecord(
        key="brand:x", name="X", decaf_available=True, verified_at="2026-09-24",
        bean=BeanProfile(acidity=2, body=4, sweetness=3, flavor_tags=["nutty"]),
        decaf_bean=BeanProfile(acidity=2, body=3, sweetness=3, flavor_tags=["chocolate"]))])
    run_load(db_conn, norm, enriched, embedded)
    bean, decaf = db_conn.execute("SELECT bean, decaf_bean FROM brands WHERE key = 'brand:x'").fetchone()
    assert bean == {"acidity": 2.0, "body": 4.0, "sweetness": 3.0, "flavor_tags": ["nutty"]}
    assert decaf["flavor_tags"] == ["chocolate"]
