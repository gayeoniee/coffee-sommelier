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
