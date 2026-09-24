import json

from pipeline.enrich import EnrichOutput, needs_llm, rule_tags, run_enrich, tag_vocab
from pipeline.llm import LLMError
from pipeline.records import CoffeeRecord, ReviewRecord, TaxonomyNode, read_jsonl, write_jsonl

VOCAB = ["lemon", "chocolate", "dark chocolate", "black tea", "honey"]


def coffee(key, **kw):
    return CoffeeRecord(key=key, name=kw.pop("name", key), source="t", collected_at="2026-09-24", **kw)


def test_rule_tags_prefers_longest_match():
    # ordered by position in the text; "chocolate" is swallowed by "dark chocolate"
    assert rule_tags("Dark chocolate, lemon zest and black tea.", VOCAB) == ["dark chocolate", "lemon", "black tea"]
    assert rule_tags("lemonade", VOCAB) == []


def test_tag_vocab_skips_level_one():
    tax = [TaxonomyNode(key="sca:fruity", level=1, name_en="fruity"),
           TaxonomyNode(key="sca:fruity>berry", parent_key="sca:fruity", level=2, name_en="Berry")]
    assert tag_vocab(tax) == ["berry"]


def test_needs_llm():
    assert needs_llm(coffee("a"), "text") is True
    assert needs_llm(coffee("a"), "") is False
    assert needs_llm(coffee("a", flavor_tags=["x"], acidity=3, body=3), "text") is False


class FakeClient:
    def __init__(self, fail_keys=()):
        self.fail_keys, self.seen, self.last_model = set(fail_keys), [], "fake"

    def chat_json(self, messages, schema):
        prompt = messages[-1]["content"]
        self.seen.append(prompt)
        if any(k in prompt for k in self.fail_keys):
            raise LLMError("boom")
        return schema(flavor_tags=["honey", "invented tag"], acidity=4, body=2, sweetness=3)


def setup_norm(tmp_path):
    norm = tmp_path / "norm"
    write_jsonl(norm / "coffees.jsonl", [
        coffee("c1", name="Kenya", acidity=5),                       # text has lemon -> rule tags, body missing -> LLM
        coffee("c2", name="FailMe"),                                 # LLM fails
        coffee("c3", name="Decaf Colombia", acidity=2, body=3),      # rule tags cover it -> no LLM
        coffee("c4", name="No text"),                                # no text -> no LLM
    ])
    write_jsonl(norm / "reviews.jsonl", [
        ReviewRecord(key="r1", coffee_key="c1", text="Bright lemon and black tea.", source="t", collected_at="x"),
        ReviewRecord(key="r2", coffee_key="c2", text="FailMe text", source="t", collected_at="x"),
        ReviewRecord(key="r3", coffee_key="c3", text="Swiss Water decaf, dark chocolate.", source="t", collected_at="x"),
    ])
    write_jsonl(norm / "taxonomy.jsonl", [TaxonomyNode(key=f"sca:x>{v}", level=2, name_en=v) for v in VOCAB])
    return norm


def test_run_enrich_rules_llm_failures_and_resume(tmp_path):
    norm, out = setup_norm(tmp_path), tmp_path / "enriched"
    client = FakeClient(fail_keys=["FailMe"])
    stats = run_enrich(norm, out, client)
    assert stats == {"coffees": 4, "llm_calls": 2, "llm_ok": 1, "llm_failed": 1, "cache_torn_lines": 0}
    by = {c.key: c for c in read_jsonl(out / "coffees.jsonl", CoffeeRecord)}
    assert by["c1"].flavor_tags == ["lemon", "black tea"]      # rule tags kept, LLM tags not used
    assert (by["c1"].acidity, by["c1"].body, by["c1"].sweetness) == (5, 2, 3)  # rule value wins, LLM fills gaps
    assert by["c2"].body is None
    assert (by["c3"].is_decaf, by["c3"].decaf_process, by["c3"].flavor_tags) == (True, "swiss-water", ["dark chocolate"])
    assert by["c4"].flavor_tags == []

    # rerun: cached rows are not sent again, failed rows only with retry_failed
    client2 = FakeClient()
    assert run_enrich(norm, out, client2)["llm_calls"] == 0
    assert run_enrich(norm, out, client2, retry_failed=True)["llm_calls"] == 1
    last = [json.loads(l) for l in (out / "cache.jsonl").read_text(encoding="utf-8").splitlines()][-1]
    assert (last["key"], last["status"]) == ("c2", "ok")


def test_llm_only_tags_are_filtered_to_vocab(tmp_path):
    norm, out = tmp_path / "norm", tmp_path / "enriched"
    write_jsonl(norm / "coffees.jsonl", [coffee("c9")])
    write_jsonl(norm / "reviews.jsonl", [ReviewRecord(key="r", coffee_key="c9", text="nice cup", source="t", collected_at="x")])
    write_jsonl(norm / "taxonomy.jsonl", [TaxonomyNode(key="sca:x>honey", level=2, name_en="honey")])
    run_enrich(norm, out, FakeClient())
    assert read_jsonl(out / "coffees.jsonl", CoffeeRecord)[0].flavor_tags == ["honey"]


def test_limit_caps_llm_calls(tmp_path):
    norm = setup_norm(tmp_path)
    assert run_enrich(norm, tmp_path / "e", FakeClient(), limit=1)["llm_calls"] == 1


def test_enrich_output_validates_range():
    assert EnrichOutput(acidity=5).acidity == 5


def test_torn_cache_line_is_skipped_and_resent(tmp_path):
    norm, out = setup_norm(tmp_path), tmp_path / "enriched"
    run_enrich(norm, out, FakeClient())                     # c1, c2 cached ok
    cache_path = out / "cache.jsonl"
    lines = cache_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["key"] for l in lines] == ["c1", "c2"]
    cache_path.write_text(lines[0] + "\n" + lines[1][:25], encoding="utf-8")  # crash mid-write of c2
    client = FakeClient()
    stats = run_enrich(norm, out, client)
    assert stats["llm_calls"] == 1 and "FailMe" in client.seen[0]    # c1 stays cached, torn c2 re-sent
    assert stats["cache_torn_lines"] == 1
    new_lines = cache_path.read_text(encoding="utf-8").split("\n")
    assert json.loads(new_lines[-2])["key"] == "c2" and new_lines[-1] == ""  # appended on its own line
    assert run_enrich(norm, out, FakeClient())["llm_calls"] == 0
