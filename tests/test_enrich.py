import json

from pipeline.enrich import (
    EnrichOutput, apply_body_heaviness, is_note_list, ko_body_cue, ko_rule_tags, ko_tag_vocab, needs_llm,
    rule_tags, run_enrich, tag_vocab,
)
from pipeline.llm import LLMError
from pipeline.records import CoffeeRecord, ReviewRecord, TaxonomyNode, read_jsonl, write_jsonl

VOCAB = ["lemon", "chocolate", "dark chocolate", "black tea", "honey"]
NO_HEAVINESS = {"body_heaviness_applied": 0, "body_heaviness_nulled": 0, "cqi_body_nulled": 0}


def coffee(key, **kw):
    return CoffeeRecord(key=key, name=kw.pop("name", key), source=kw.pop("source", "t"),
                        collected_at="2026-09-24", **kw)


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
    assert stats == {"coffees": 4, "llm_calls": 2, "llm_ok": 1, "llm_failed": 1, "cache_torn_lines": 0,
                      **NO_HEAVINESS}
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


KO_TAX = [TaxonomyNode(key=f"sca:x>{en}", level=lvl, name_en=en, name_ko=ko) for lvl, en, ko in [
    (1, "fruity", "과일"), (2, "berry", "베리"), (3, "blueberry", "블루베리"), (3, "pear", "배"),
    (3, "dark chocolate", "다크 초콜릿"), (3, "chocolate", "초콜릿"), (2, "floral", "꽃향"), (3, "honey", "꿀"),
]]


def test_ko_tag_vocab_uses_korean_names_and_known_aliases():
    ko = ko_tag_vocab(KO_TAX)
    assert ko["다크초콜릿"] == "dark chocolate" and ko["블루베리"] == "blueberry"   # spaces removed
    assert "과일" not in ko                                                       # level 1 skipped like tag_vocab
    assert ko["플로럴"] == "floral" and "카라멜" not in ko                        # alias only if its tag exists


def test_ko_rule_tags_on_note_lists():
    ko = ko_tag_vocab(KO_TAX)
    # longest match wins inside a note, space-insensitive; order follows the notes
    assert ko_rule_tags("블루 베리, 밀크초콜릿, 다크초콜릿", ko) == ["blueberry", "chocolate", "dark chocolate"]
    assert ko_rule_tags("배, 배전도, 꿀", ko) == ["pear", "honey"]      # one-letter terms only as a whole note
    assert rule_tags("블루베리, 베리, 꽃향기 가득", [], ko_vocab=ko) == ["blueberry", "floral"]   # berry ⊂ blueberry
    assert rule_tags("Lemon, 블루베리", ["lemon"], ko_vocab=ko) == ["lemon", "blueberry"]
    assert rule_tags("블루베리", ["lemon"]) == []                        # no Korean vocab -> English only


def test_korean_matching_skips_prose():
    assert is_note_list("초콜릿, 건무화과, 당밀의 긴 여운") and not is_note_list("")
    assert not is_note_list("에티오피아 커피나무에서 자란 원두로 발효 공정을 거쳐 블루베리 향이 납니다.")
    ko = ko_tag_vocab(KO_TAX)
    assert rule_tags("콜롬비아 디카페인. 블루베리와 꿀.", [], ko_vocab=ko) == []


def test_ko_body_cue():
    assert ko_body_cue("묵직하고 진한 바디") == 4
    assert ko_body_cue("무거운 텍스처") == 4
    assert ko_body_cue("풀바디, 카카오") == 4
    assert ko_body_cue("가벼운 산미와 깔끔한 마무리") == 2
    assert ko_body_cue("라이트한 바디감") == 2
    assert ko_body_cue("깔끔한 바디") == 2
    assert ko_body_cue("체리, 자스민") is None
    assert ko_body_cue("") is None
    assert ko_body_cue(None) is None


def test_apply_rules_sets_body_from_korean_cue_for_roasters_kr_only():
    c = coffee("rk1", source="roasters_kr")
    from pipeline.enrich import _apply_rules
    out = _apply_rules(c, "묵직한 바디, 다크초콜릿", [], ko_vocab=None)
    assert out.body == 4

    other = coffee("cr1", source="coffeereview_kaggle")
    out2 = _apply_rules(other, "묵직한 바디", [], ko_vocab=None)
    assert out2.body is None                              # cue only fires for roasters_kr

    already_set = coffee("rk2", source="roasters_kr", body=3)
    out3 = _apply_rules(already_set, "묵직한 바디", [], ko_vocab=None)
    assert out3.body == 3                                 # existing value wins


def test_apply_body_heaviness_merges_relabel_and_nulls_cqi(tmp_path):
    out_dir = tmp_path / "enriched"
    out_dir.mkdir(parents=True)
    (out_dir / "body_heaviness.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [
            {"key": "cr1", "hash": "h1", "status": "ok", "body": 5},
            {"key": "cr2", "hash": "h2", "status": "ok", "body": None},   # judged, but text said nothing
            {"key": "cr3", "hash": "h3", "status": "failed", "error": "boom"},
        ]) + "\n", encoding="utf-8")

    coffees = [
        coffee("cr1", source="coffeereview_kaggle", body=2),       # old wrong quintile value -> heaviness 5
        coffee("cr2", source="coffeereview_kaggle", body=3),       # judged null -> None
        coffee("cr3", source="coffeereview_kaggle", body=4),       # failed -> None
        coffee("cr4", source="coffeereview_kaggle", body=1),       # never attempted (no cache entry) -> untouched
        coffee("cq1", source="cqi", body=4),                       # CQI body is a quality score -> always None
        coffee("rk1", source="roasters_kr", body=2),               # untouched by this step
    ]
    out, stats = apply_body_heaviness(coffees, out_dir)
    by = {c.key: c for c in out}
    assert by["cr1"].body == 5
    assert by["cr2"].body is None
    assert by["cr3"].body is None
    assert by["cr4"].body == 1
    assert by["cq1"].body is None
    assert by["rk1"].body == 2
    assert stats == {"body_heaviness_applied": 1, "body_heaviness_nulled": 2, "cqi_body_nulled": 1}


def test_run_enrich_applies_body_heaviness_and_nulls_cqi(tmp_path):
    norm, out = tmp_path / "norm", tmp_path / "enriched"
    write_jsonl(norm / "coffees.jsonl", [
        coffee("cr1", source="coffeereview_kaggle", flavor_tags=["x"], acidity=3, body=2),
        coffee("cq1", source="cqi", flavor_tags=["x"], acidity=3, body=4),
    ])
    write_jsonl(norm / "reviews.jsonl", [])
    write_jsonl(norm / "taxonomy.jsonl", [])
    out.mkdir(parents=True)
    (out / "body_heaviness.jsonl").write_text(
        json.dumps({"key": "cr1", "hash": "h", "status": "ok", "body": 5}) + "\n", encoding="utf-8")
    stats = run_enrich(norm, out, FakeClient())
    by = {c.key: c for c in read_jsonl(out / "coffees.jsonl", CoffeeRecord)}
    assert by["cr1"].body == 5
    assert by["cq1"].body is None
    assert stats["body_heaviness_applied"] == 1 and stats["cqi_body_nulled"] == 1


def test_run_enrich_tags_korean_notes_and_llm_fills_attrs(tmp_path):
    norm, out = tmp_path / "norm", tmp_path / "enriched"
    write_jsonl(norm / "coffees.jsonl", [coffee("k1", flavor_summary="블루베리, 다크 초콜릿")])
    write_jsonl(norm / "reviews.jsonl", [])
    write_jsonl(norm / "taxonomy.jsonl", KO_TAX)
    client = FakeClient()
    run_enrich(norm, out, client)
    [c] = read_jsonl(out / "coffees.jsonl", CoffeeRecord)
    assert c.flavor_tags == ["blueberry", "dark chocolate"]     # rule tags; the LLM's tags are not used
    assert (c.acidity, c.body, c.sweetness) == (4, 2, 3)        # attrs come from the LLM, as for other sources
    assert "블루베리, 다크 초콜릿" in client.seen[0]


def test_label_source_tracks_korean_cue_llm_and_heaviness(tmp_path):
    from pipeline.enrich import _apply_rules, _merge_llm
    rk = coffee("rk1", source="roasters_kr", acidity=4, attr_label_source={"acidity": "gauge"})
    cued = _apply_rules(rk, "묵직한 바디", [], ko_vocab=None)
    assert cued.attr_label_source == {"acidity": "gauge", "body": "korean_cue"}
    merged = _merge_llm(cued, EnrichOutput(acidity=2, body=2, sweetness=3), set())
    assert (merged.acidity, merged.body, merged.sweetness) == (4, 4, 3)          # labels already set win
    assert merged.attr_label_source == {"acidity": "gauge", "body": "korean_cue", "sweetness": "llm_review"}

    out_dir = tmp_path / "e"
    out_dir.mkdir()
    (out_dir / "body_heaviness.jsonl").write_text(
        json.dumps({"key": "cr1", "hash": "h", "status": "ok", "body": 5}) + "\n", encoding="utf-8")
    out, _ = apply_body_heaviness([
        coffee("cr1", source="coffeereview_kaggle", body=2, attr_label_source={"body": "review_score"}),
        coffee("cq1", source="cqi", body=4, attr_label_source={"acidity": "cqi_quality", "body": "cqi_quality"}),
    ], out_dir)
    by = {c.key: c for c in out}
    assert by["cr1"].attr_label_source == {"body": "llm_review"}
    assert by["cq1"].attr_label_source == {"acidity": "cqi_quality"}
