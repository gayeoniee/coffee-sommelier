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
    assert counts == {"coffees": 2, "reviews": 1, "brands": 1, "menu_items": 1, "flavor_taxonomy": 2, "enrich_log": 1,
                      "dropped_reviews": 0, "dropped_menu_items": 0}
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

