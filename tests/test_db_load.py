import json

import pytest

from pipeline.load import run_load
from pipeline.query import similar, to_vector_literal
from pipeline.records import (
    BrandRecord,
    CoffeeRecord,
    MenuItemRecord,
    ReviewRecord,
    TaxonomyNode,
    write_jsonl,
)

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

    def embed_query(self, text):
        return self.embed([text])[0]


def test_load_and_query(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    counts = run_load(db_conn, norm, enriched, embedded)
    assert counts == {"coffees": 2, "reviews": 1, "brands": 1, "menu_items": 1, "flavor_taxonomy": 2,
                      "enrich_log": 1, "dropped_reviews": 0, "dropped_menu_items": 0,
                      "deleted_coffees": 0, "deleted_reviews": 0, "deleted_menu_items": 0, "deleted_brands": 0,
                      "kept_referenced_coffees": 0, "kept_referenced_menu_items": 0, "kept_referenced_brands": 0,
                      "needs_review_menu_items": 0}
    for table in ("coffees", "menu_items", "brands"):
        assert db_conn.execute(f"SELECT bool_and(active) FROM {table}").fetchone() == (True,)
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

        def embed_query(self, text):
            return self.embed([text])[0]

    hits = similar(db_conn, RandomEmbedder(), "decaf", k=5, decaf=True)
    assert len(hits) == 5 and all(h["is_decaf"] for h in hits)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def test_similar_excludes_inactive_coffees(db_conn):
    """similar() should not return retired (active=false) coffees."""
    v = vec(0)
    db_conn.execute(
        "INSERT INTO coffees (key, name, is_decaf, embedding, source, collected_at, active) "
        "VALUES (%s, %s, %s, %s::vector, 't', '2026-09-26', %s)",
        ("active-coffee", "Active Coffee", False, to_vector_literal(v), True)
    )
    db_conn.execute(
        "INSERT INTO coffees (key, name, is_decaf, embedding, source, collected_at, active) "
        "VALUES (%s, %s, %s, %s::vector, 't', '2026-09-26', %s)",
        ("inactive-coffee", "Inactive Coffee", False, to_vector_literal(v), False)
    )
    db_conn.commit()

    hits = similar(db_conn, FixedEmbedder(), "test", k=10)
    assert len(hits) == 1
    assert hits[0]["name"] == "Active Coffee"


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


def test_reload_deletes_missing_rows_but_keeps_referenced(db_conn, tmp_path, caplog):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    _add_tasting(db_conn, coffee_key="c1")
    write_jsonl(enriched / "coffees.jsonl", [          # c1 and c2 vanish from the source, c3 is new
        CoffeeRecord(key="c3", name="Kenya AA", origin_country="Kenya", source="t", collected_at="2026-09-26")])
    write_jsonl(norm / "reviews.jsonl", [])
    with caplog.at_level("WARNING"):
        counts = run_load(db_conn, norm, enriched, embedded)
    assert counts["deleted_coffees"] == 1                # c2 deleted
    assert counts["kept_referenced_coffees"] == 1        # c1 kept: a tasting points at it
    assert counts["deleted_reviews"] == 1
    rows = dict(db_conn.execute("SELECT key, active FROM coffees").fetchall())
    assert rows == {"c1": False, "c3": True}             # kept but retired from the catalog
    assert "coffees: kept 1 retired row(s) still referenced: ['c1']" in caplog.text
    write_jsonl(enriched / "coffees.jsonl", [          # c1 comes back -> active again
        CoffeeRecord(key="c1", name="Ethiopia Washed", source="t", collected_at="2026-09-26")])
    run_load(db_conn, norm, enriched, embedded)
    assert db_conn.execute("SELECT active FROM coffees WHERE key = 'c1'").fetchone() == (True,)


def test_reload_retires_referenced_menu_item_and_brand(db_conn, tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    _add_tasting(db_conn, menu_key="m1")
    write_jsonl(norm / "brands.jsonl", [BrandRecord(key="brand:y", name="Y", decaf_available=False,
                                                    verified_at="2026-09-26")])
    write_jsonl(norm / "menu_items.jsonl", [])
    counts = run_load(db_conn, norm, enriched, embedded)
    assert (counts["kept_referenced_menu_items"], counts["kept_referenced_brands"]) == (1, 1)
    assert db_conn.execute("SELECT active FROM menu_items WHERE key = 'm1'").fetchone() == (False,)
    assert dict(db_conn.execute("SELECT key, active FROM brands").fetchall()) == {"brand:x": False, "brand:y": True}


@pytest.mark.parametrize("emptied", ["coffees", "brands"])
def test_empty_source_list_refuses_to_load(db_conn, tmp_path, emptied):
    norm, enriched, embedded = setup_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    if emptied == "coffees":
        write_jsonl(enriched / "coffees.jsonl", [])
    else:
        write_jsonl(norm / "brands.jsonl", [])
    with pytest.raises(ValueError, match=f"{emptied}.*비어"):
        run_load(db_conn, norm, enriched, embedded)
    db_conn.rollback()
    assert db_conn.execute("SELECT count(*) FROM coffees").fetchone() == (2,)
    assert db_conn.execute("SELECT count(*) FROM brands").fetchone() == (1,)


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
        bean=BeanProfile(acidity=2, body=4, sweetness=3, flavor_tags=["nutty"],
                         label_source={"acidity": "official_notes_model", "body": "official_notes_model",
                                       "sweetness": "estimate", "flavor_tags": "official_notes_model"},
                         official_note="X 공식: 묵직한 바디"),
        decaf_bean=BeanProfile(acidity=2, body=3, sweetness=3, flavor_tags=["chocolate"]),
        bean_open=BeanProfile(acidity=2.5, body=4.5, sweetness=3.5, flavor_tags=["nutty"],
                              label_source={"acidity": "open_feature_model", "body": "official_cue",
                                            "sweetness": "open_feature_model", "flavor_tags": "estimate"}))])
    run_load(db_conn, norm, enriched, embedded)
    bean, decaf = db_conn.execute("SELECT bean, decaf_bean FROM brands WHERE key = 'brand:x'").fetchone()
    assert bean == {"acidity": 2.0, "body": 4.0, "sweetness": 3.0, "flavor_tags": ["nutty"],
                    "label_source": {"acidity": "official_notes_model", "body": "official_notes_model",
                                     "sweetness": "estimate", "flavor_tags": "official_notes_model"},
                    "official_note": "X 공식: 묵직한 바디"}          # read by app/repo.py _menu_item → card evidence
    assert decaf["flavor_tags"] == ["chocolate"] and decaf["official_note"] is None
    bean_open, decaf_open = db_conn.execute(
        "SELECT bean_open, decaf_bean_open FROM brands WHERE key = 'brand:x'").fetchone()
    assert (bean_open["acidity"], bean_open["label_source"]["body"]) == (2.5, "official_cue")   # open variant's
    assert decaf_open is None


def test_load_refuses_missing_embeddings_file(db_conn, tmp_path):
    norm, enriched, _ = setup_files(tmp_path)
    with pytest.raises(ValueError, match="embed"):
        run_load(db_conn, norm, enriched, tmp_path / "no-such-model")


def test_unlabelled_menu_name_is_loaded_but_needs_review(db_conn, tmp_path):
    """ADR 0015: a menu name with no hand milk label fails closed -- loaded, flagged, kept out of recommendations."""
    norm, enriched, embedded = setup_files(tmp_path)
    write_jsonl(norm / "menu_items.jsonl", [
        MenuItemRecord(key="m1", brand_key="brand:x", name="아메리카노", caffeine_mg=150, collected_at="2026-09-24"),
        MenuItemRecord(key="m2", brand_key="brand:x", name="신메뉴 크림 라떼", collected_at="2026-09-24")])
    counts = run_load(db_conn, norm, enriched, embedded, milk_labels={"아메리카노": False})
    assert counts["needs_review_menu_items"] == 1
    assert dict(db_conn.execute("SELECT key, needs_review FROM menu_items").fetchall()) == {"m1": False, "m2": True}
    # labelled later -> the next load clears the flag
    run_load(db_conn, norm, enriched, embedded, milk_labels={"아메리카노": False, "신메뉴 크림 라떼": True})
    assert db_conn.execute("SELECT bool_or(needs_review) FROM menu_items").fetchone() == (False,)


def _scoped_files(tmp_path):
    norm, enriched, embedded = setup_files(tmp_path)
    coffees = [CoffeeRecord(key=k, name=k, roaster=r, source=s, collected_at="2026-09-24")
               for k, r, s in [("kr-a1", "A", "roasters_kr"), ("kr-a2", "A", "roasters_kr"),
                               ("kr-b1", "B", "roasters_kr"), ("static1", None, "cqi")]]
    write_jsonl(enriched / "coffees.jsonl", coffees)
    (embedded / "embeddings.jsonl").write_text(
        "".join(json.dumps({"key": c.key, "hash": "h", "vector": vec(i)}) + "\n" for i, c in enumerate(coffees)),
        encoding="utf-8")
    write_jsonl(norm / "reviews.jsonl", [])
    write_jsonl(norm / "brands.jsonl", [BrandRecord(key=k, name=k, decaf_available=True, verified_at="2026-09-24")
                                        for k in ("brand:x", "brand:y")])
    write_jsonl(norm / "menu_items.jsonl", [
        MenuItemRecord(key="x1", brand_key="brand:x", name="아메리카노", collected_at="2026-09-24"),
        MenuItemRecord(key="y1", brand_key="brand:y", name="아메리카노", collected_at="2026-09-24")])
    return norm, enriched, embedded, coffees


def test_scoped_load_only_retires_rows_of_groups_that_were_refreshed(db_conn, tmp_path):
    """Refresh load: roastery B's scraper failed (no rows) and cqi is static -> their rows stay; within the
    refreshed roastery A a vanished bean is retired; brand y's menu scraper failed -> its menu stays."""
    from pipeline.load import coffee_group

    norm, enriched, embedded, coffees = _scoped_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    write_jsonl(enriched / "coffees.jsonl", [c for c in coffees if c.key == "kr-a1"])
    write_jsonl(norm / "menu_items.jsonl", [])
    counts = run_load(db_conn, norm, enriched, embedded,
                      coffee_scope={coffee_group("roasters_kr", "A")}, brand_scope={"brand:x"})
    assert counts["deleted_coffees"] == 1 and counts["deleted_menu_items"] == 1
    assert {k for (k,) in db_conn.execute("SELECT key FROM coffees")} == {"kr-a1", "kr-b1", "static1"}
    assert {k for (k,) in db_conn.execute("SELECT key FROM menu_items")} == {"y1"}
    assert coffee_group("cqi", "x") == "cqi" and coffee_group("shopify_gauged", "S") == "shopify_gauged:S"


def test_scoped_load_leaves_enrich_log_alone(db_conn, tmp_path):
    norm, enriched, embedded, coffees = _scoped_files(tmp_path)
    run_load(db_conn, norm, enriched, embedded)
    assert db_conn.execute("SELECT count(*) FROM enrich_log").fetchone() == (1,)
    (enriched / "cache.jsonl").write_text("", encoding="utf-8")
    run_load(db_conn, norm, enriched, embedded, coffee_scope={"roasters_kr:A"})
    assert db_conn.execute("SELECT count(*) FROM enrich_log").fetchone() == (1,)
