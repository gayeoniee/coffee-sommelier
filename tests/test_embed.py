import json

import pytest

from pipeline.embed import embedding_text, run_embed
from pipeline.records import CoffeeRecord, ReviewRecord, write_jsonl


class FakeEmbedder:
    def __init__(self):
        self.batches = []

    def embed(self, texts):
        self.batches.append(list(texts))
        return [[float(len(t))] + [0.0] * 1023 for t in texts]


def coffee(key, **kw):
    return CoffeeRecord(key=key, name=kw.pop("name", key), source="t", collected_at="2026-09-24", **kw)


def test_embedding_text_includes_structure_and_review():
    c = coffee("a", name="Kenya AA", origin_country="Kenya", process="washed", is_decaf=True,
               decaf_process="swiss-water", flavor_tags=["lemon"], flavor_summary="Bright.")
    t = embedding_text(c, "Long review " * 400)
    assert t.startswith("Kenya AA | Kenya | washed | decaf swiss-water | lemon | Bright. | Long review")
    assert len(t) < 2000


def test_run_embed_caches_unchanged_rows(tmp_path):
    enriched, norm, out = tmp_path / "e", tmp_path / "n", tmp_path / "o"
    write_jsonl(enriched / "coffees.jsonl", [coffee("a"), coffee("b")])
    write_jsonl(norm / "reviews.jsonl", [ReviewRecord(key="r", coffee_key="a", text="txt", source="t", collected_at="x")])
    e = FakeEmbedder()
    assert run_embed(enriched, norm, out, e, batch=1) == {"embedded": 2, "cached": 0}
    assert len(e.batches) == 2
    write_jsonl(enriched / "coffees.jsonl", [coffee("a"), coffee("b", name="changed")])
    assert run_embed(enriched, norm, out, e) == {"embedded": 1, "cached": 1}


class CrashingEmbedder(FakeEmbedder):
    def __init__(self, fail_on_batch):
        super().__init__()
        self.fail_on_batch = fail_on_batch

    def embed(self, texts):
        if len(self.batches) == self.fail_on_batch:
            raise RuntimeError("embedder died")
        return super().embed(texts)


def test_run_embed_keeps_completed_batches_after_crash(tmp_path):
    enriched, norm, out = tmp_path / "e", tmp_path / "n", tmp_path / "o"
    write_jsonl(enriched / "coffees.jsonl", [coffee("a"), coffee("b"), coffee("c")])
    write_jsonl(norm / "reviews.jsonl", [])
    with pytest.raises(RuntimeError):
        run_embed(enriched, norm, out, CrashingEmbedder(fail_on_batch=2), batch=1)  # a, b done; c crashes
    e = FakeEmbedder()
    assert run_embed(enriched, norm, out, e, batch=1) == {"embedded": 1, "cached": 2}
    assert e.batches == [["c"]]
    assert not (out / "embeddings.jsonl.partial").exists()
    keys = [json.loads(l)["key"] for l in (out / "embeddings.jsonl").read_text(encoding="utf-8").splitlines()]
    assert keys == ["a", "b", "c"]
