"""End-to-end DRY_RUN of scripts/refresh/refresh.py on a tiny catalog: fake collectors (normalized files written by
a stub), fake LLM/embedder, fake evals. Checks the diff, the staging DBs, the gates, and that nothing is written to
the source/publish DB and no git/gh command runs."""
import json

import psycopg
import pytest

import pipeline.llm as llm
from pipeline.enrich import EnrichOutput
from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, TaxonomyNode, write_jsonl
from scripts.refresh import gates, refresh
from tests.conftest import TEST_URL

from .conftest import vec

pytestmark = pytest.mark.db
BASE = TEST_URL.rsplit("/", 1)[0]


class FakeClient:
    last_model = "fake-llm"

    def chat_json(self, messages, schema):
        return EnrichOutput(flavor_tags=["berry"], acidity=4, body=3, sweetness=3)


class FakeEmbedder:
    requests = 0

    def embed(self, texts):
        self.requests += 1
        return [vec(7) for _ in texts]


def fake_normalize(menus, coffees):
    def normalize(cfg, stage):
        out = cfg.work / "normalized"
        write_jsonl(out / "menu_items.jsonl", menus)
        write_jsonl(out / "coffees.jsonl", coffees)
        write_jsonl(out / "reviews.jsonl", [       # same review text as the DB: not a change
            ReviewRecord(key=f"r-{c.key}", coffee_key=c.key, text="nice", source="roasters_kr",
                         collected_at="2026-09-28") for c in coffees if c.key in ("kr1", "kr2")])
        write_jsonl(out / "brands.jsonl", [BrandRecord(key=k, name=k, decaf_available=True, verified_at="2026-09-01")
                                           for k in ("brand:a", "brand:b")])
        write_jsonl(out / "taxonomy.jsonl", [TaxonomyNode(key="sca:fruity", level=1, name_en="fruity", name_ko="과일"),
                                             TaxonomyNode(key="sca:fruity>berry", parent_key="sca:fruity", level=2,
                                                          name_en="berry")])
        return {"menu_items": len(menus), "coffees": len(coffees)}
    return normalize


def fake_evals(db_url, eval_dir, variant):
    eval_dir.mkdir(parents=True, exist_ok=True)
    for name, doc in {"phase2_violations.json": {"violations": 0, "checked": 10},
                      "phase2_loo.json": {"acidity": {"n": 200, "within1": 0.99}},
                      "phase2_coverage.json": {"total": 3}}.items():
        (eval_dir / name).write_text(json.dumps(doc), encoding="utf-8")
    return 0, "ok"


def no_shell(cmd, *a, **kw):
    raise AssertionError(f"DRY_RUN must not run {cmd[:3]}")


@pytest.fixture
def setup(src_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "client_for", lambda task: FakeClient())
    monkeypatch.setattr(llm, "embedder_for", lambda *a, **k: FakeEmbedder())
    monkeypatch.setattr(llm, "embed_model", lambda *a: "fake-embed")
    monkeypatch.setattr(refresh.settings, "ENRICHED_DIR", tmp_path / "no-local-cache")
    monkeypatch.setattr(refresh, "load_milk_labels", lambda: {"아메리카노": False, "카페 라떼": True})
    monkeypatch.setattr(gates, "run_evals", fake_evals)
    monkeypatch.setattr(gates, "run", no_shell)
    monkeypatch.setattr(refresh, "numbers_gate",
                        lambda cfg, f, o: (gates.Gate("readme_numbers", True, "stub"), tmp_path))
    cfg = refresh.Config(date="2026-09-28", dry_run=True, work=tmp_path / "work", raw_dir=tmp_path / "raw",
                         source_url=TEST_URL, stage_url=f"{BASE}/coffee_test_refresh",
                         stage_open_url=f"{BASE}/coffee_test_refresh_open", publish_full_url=TEST_URL,
                         publish_open_url=None, skip_collect=True, skip_pytest=True)
    return cfg, monkeypatch


def menu(key, brand, name, mg=None):
    return MenuItemRecord(key=key, brand_key=brand, name=name, caffeine_mg=mg, collected_at="2026-09-28")


def bean(key, **kw):
    return CoffeeRecord(key=key, name=f"Bean {key}", roaster="A", source="roasters_kr", collected_at="2026-09-28", **kw)


def test_dry_run_refresh_stages_everything_and_writes_nothing(setup, src_conn):
    cfg, mp = setup
    mp.setattr(refresh, "normalize", fake_normalize(
        [menu("a1", "brand:a", "아메리카노", 200), menu("a2", "brand:a", "카페 라떼", 75),
         menu("a3", "brand:a", "신메뉴 크림 라떼", 90)],            # brand:b not collected this time
        [bean("kr1", flavor_tags=["berry"]), bean("kr2", origin_country="Kenya"),
         bean("kr3", flavor_summary="berry, jam")]))
    rep = refresh.refresh(cfg)
    assert rep.gates_ok, rep.to_dict()
    d = json.loads((cfg.work / "report.json").read_text(encoding="utf-8"))
    menus, coffees = d["stages"]["diff"]["menus"], d["stages"]["diff"]["coffees"]
    assert (menus["counts"]["new"], menus["counts"]["caffeine_flagged"], menus["counts"]["removed"]) == (1, 1, 0)
    assert menus["label_needed"] == ["신메뉴 크림 라떼"] and menus["brands_kept_not_collected"] == ["brand:b"]
    assert (coffees["counts"]["new"], coffees["counts"]["changed"], coffees["counts"]["unchanged"]) == (1, 1, 1)
    assert d["stages"]["enrich_embed"]["enrich"]["llm_calls"] >= 1 and d["stages"]["enrich_embed"]["embed"]["embedded"] == 2
    assert "라벨 필요 1개" in (cfg.work / "report.md").read_text(encoding="utf-8")

    with psycopg.connect(cfg.stage_url) as stage:
        assert dict(stage.execute("SELECT key, needs_review FROM menu_items").fetchall()) == {
            "a1": False, "a2": False, "a3": True, "b1": False}
        assert stage.execute("SELECT caffeine_mg FROM menu_items WHERE key = 'a1'").fetchone() == (200,)
        assert {k for (k,) in stage.execute("SELECT key FROM coffees")} == {"cr1", "kr1", "kr2", "kr3"}
        assert stage.execute("SELECT flavor_tags, embedding IS NOT NULL FROM coffees WHERE key = 'kr3'"
                             ).fetchone() == (["berry"], True)
        # unchanged kr1 kept its DB row as is (collected_at not bumped)
        assert str(stage.execute("SELECT collected_at FROM coffees WHERE key = 'kr1'").fetchone()[0]) == "2026-09-01"
    with psycopg.connect(cfg.stage_open_url) as stage_open:
        assert {k for (k,) in stage_open.execute("SELECT key FROM coffees")} == {"kr1", "kr2", "kr3"}

    # the source/publish DB is untouched; the publish step only planned
    assert src_conn.execute("SELECT caffeine_mg FROM menu_items WHERE key = 'a1'").fetchone() == (150,)
    assert src_conn.execute("SELECT count(*) FROM coffees WHERE key = 'kr3'").fetchone() == (0,)
    pub = d["stages"]["publish"]["full"]
    assert pub["written"] is False and pub["plan"]["menu_items"]["new"] == 1 and pub["plan"]["coffees"]["new"] == 1
    assert d["stages"]["publish"]["open"]["target"] is None
    assert d["stages"]["pr"] == {"skipped": "DRY_RUN — PR·운영 DB 쓰기 없음"}


def test_broken_scraper_fails_the_size_gate(setup, src_conn):
    cfg, mp = setup
    mp.setattr(refresh, "normalize", fake_normalize([menu("a9", "brand:a", "아메리카노", 150)],
                                                    [bean("kr1", flavor_tags=["berry"])]))
    rep = refresh.refresh(cfg)
    assert not rep.gates_ok, rep.to_dict()
    size = next(g for g in rep.gates if g["name"] == "size_check")
    assert not size["ok"] and "menu_items: 2/3" in size["detail"]
    assert rep.stages["issue"] == {"skipped": "DRY_RUN"}
    assert src_conn.execute("SELECT count(*) FROM menu_items").fetchone() == (3,)
