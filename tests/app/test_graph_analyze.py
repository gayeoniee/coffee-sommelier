from app.core.attrmodel import AttrModel, _Ridge
from app.core.tagmodel import TagModel
from app.graphs.analyze_bean import build_analyze_graph
from app.models import Profile
from tests.app.fakes import fake_deps, run_events


def first_card(events):
    return next(e for e in events if e["type"] == "cards")["cards"][0]


def _fake_attr_model(sweetness=True) -> AttrModel:
    # fake_deps().embed() returns [0.0] * 8, so every input is 0 -- ridge collapses to the intercept.
    models = {"acidity": _Ridge(w=[0.0] * 8, b=4.5), "body": _Ridge(w=[0.0] * 8, b=1.5)}
    if sweetness:
        models["sweetness"] = _Ridge(w=[0.0] * 8, b=3.5)
    return AttrModel(embed_model="test", models=models)


def _fake_tag_model() -> TagModel:
    # fake_deps().embed() returns [0.0] * 8, so every input is 0 -- the hidden layer collapses to relu(b1), and
    # w1 is irrelevant. b1=[1,3] -> hidden=[1,3]; w2 picks each hidden unit straight through to its own tag ->
    # out=[1,3] -> sigmoid(3) > sigmoid(1), so "chocolate" outranks "lemon".
    return TagModel(labels=["lemon", "chocolate"], threshold=0.3, embed_model="test",
                    w1=[[0.0, 0.0] for _ in range(8)], b1=[1.0, 3.0],
                    w2=[[1.0, 0.0], [0.0, 1.0]], b2=[0.0, 0.0])


def test_coffee_id_uses_db_data_without_embedding():
    deps = fake_deps()
    events = run_events(build_analyze_graph(deps), {"coffee_id": 2, "profile": Profile(body=4)})
    c = first_card(events)
    assert (c["source"], c["name"], c["confidence"]) == ("db", "Brazil Cerrado", "high")
    assert deps.calls["embed"] == 0 and deps.calls["json"] == 0
    assert any(e["type"] == "explain_done" for e in events)


def test_exact_name_match_skips_prediction():
    deps = fake_deps()
    c = first_card(run_events(build_analyze_graph(deps), {"text": "  brazil   cerrado ", "profile": Profile()}))
    assert c["source"] == "db" and deps.calls["embed"] == 0


def test_unknown_bean_is_predicted_from_neighbors_with_evidence():
    deps = fake_deps()
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["source"] == "predicted" and c["n_neighbors"] == 10
    assert c["evidence"][0].startswith("유사 원두 10개 중 10개에서 '레몬'")
    assert deps.calls["json"] == 0                     # rules found the origin, no LLM parse


def test_korean_text_without_origin_uses_llm_parse_and_still_answers():
    deps = fake_deps(parse={"origin_country": "Colombia", "is_decaf": True})
    events = run_events(build_analyze_graph(deps), {"text": "동네 로스터리 하우스 블렌드",
                                                     "profile": Profile(caffeine_rule="decaf_only")})
    c = first_card(events)
    assert deps.calls["json"] == 1 and c["is_decaf"] is True and c["violation"] is None
    assert any(e["type"] in ("explain_done", "explain_fallback") for e in events)


def test_parse_failure_and_embedding_failure_degrade_to_low_confidence():
    deps = fake_deps(json_fails=True, embed_fails=True)
    c = first_card(run_events(build_analyze_graph(deps), {"text": "하우스 블렌드", "profile": Profile()}))
    assert c["source"] == "predicted" and c["confidence"] == "low" and c["acidity"] is None
    deps2 = fake_deps(embed_fails=True)
    c2 = first_card(run_events(build_analyze_graph(deps2), {"text": "브라질 내추럴", "profile": Profile()}))
    assert c2["confidence"] == "low" and c2["n_neighbors"] == 4      # origin/process average fallback


def test_unknown_bean_uses_tag_model_when_loaded():
    deps = fake_deps(tag_model=_fake_tag_model())
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["source"] == "predicted"
    assert c["tags"] == ["chocolate", "lemon"]                  # sigmoid(3) > sigmoid(1): model overrides the vote
    assert any(e.startswith("향미 모델: 초콜릿 0.95, 레몬 0.73") for e in c["evidence"])
    assert c["acidity"] is not None and c["confidence"] != "low"  # attributes still come from the neighbour vote


def test_degraded_embedding_falls_back_to_neighbor_vote_even_with_a_tag_model_loaded():
    deps = fake_deps(embed_fails=True, tag_model=_fake_tag_model())
    c = first_card(run_events(build_analyze_graph(deps), {"text": "브라질 내추럴", "profile": Profile()}))
    assert c["confidence"] == "low"
    assert not any(e.startswith("향미 모델:") for e in c["evidence"])


def test_no_tag_model_loaded_keeps_neighbor_vote_tags():
    deps = fake_deps()                                          # tag_model=None by default
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert not any(e.startswith("향미 모델:") for e in c["evidence"])
    assert c["tags"] == ["lemon"]


def test_unknown_bean_uses_attr_model_when_loaded():
    deps = fake_deps(attr_model=_fake_attr_model())
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["source"] == "predicted"
    assert (c["acidity"], c["body"], c["sweetness"]) == (4.5, 1.5, 3.5)   # model values, not the neighbour average
    assert c["confidence"] != "low" and c["n_neighbors"] == 10             # confidence/n_neighbors untouched


def test_attr_model_missing_attribute_falls_back_to_neighbor_average():
    deps = fake_deps(attr_model=_fake_attr_model(sweetness=False))
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert (c["acidity"], c["body"]) == (4.5, 1.5)      # model values
    assert c["sweetness"] == 3.0                        # model doesn't cover sweetness -> neighbour average kept


def test_degraded_embedding_falls_back_to_neighbor_average_even_with_an_attr_model_loaded():
    deps = fake_deps(embed_fails=True, attr_model=_fake_attr_model())
    c = first_card(run_events(build_analyze_graph(deps), {"text": "브라질 내추럴", "profile": Profile()}))
    assert c["confidence"] == "low" and c["acidity"] == 3.0    # fallback_neighbors' plain average, not the model


def test_no_attr_model_loaded_keeps_neighbor_average_attrs():
    deps = fake_deps()                                          # attr_model=None by default
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert (c["acidity"], c["body"], c["sweetness"]) != (4.5, 1.5, 3.5)


def test_tag_model_and_attr_model_compose_independently():
    deps = fake_deps(tag_model=_fake_tag_model(), attr_model=_fake_attr_model())
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["tags"] == ["chocolate", "lemon"]                          # tag model's ranking
    assert (c["acidity"], c["body"], c["sweetness"]) == (4.5, 1.5, 3.5)  # attr model's values
    assert any(e.startswith("향미 모델:") for e in c["evidence"])


def test_text_cue_overrides_neighbor_average_and_adds_evidence():
    deps = fake_deps()                                          # no learned models loaded
    c = first_card(run_events(build_analyze_graph(deps),
                              {"text": "에티오피아 예가체프 워시드, 묵직한 바디감", "profile": Profile()}))
    assert c["source"] == "predicted"
    assert c["body"] == 4.5                                     # text cue overrides the neighbour average
    assert any("묵직" in e and "바디 4.5" in e for e in c["evidence"])


def test_text_cue_overrides_learned_attr_model_too():
    deps = fake_deps(attr_model=_fake_attr_model())              # model would otherwise say body=1.5
    c = first_card(run_events(build_analyze_graph(deps),
                              {"text": "에티오피아 예가체프 워시드, 묵직한 바디감", "profile": Profile()}))
    assert c["body"] == 4.5                                      # text cue outranks the model value too
    assert c["acidity"] == 4.5                                   # untouched model value (no acidity cue)


def test_text_cue_flavor_tags_are_unioned_ahead_of_predicted_tags():
    deps = fake_deps()
    c = first_card(run_events(build_analyze_graph(deps),
                              {"text": "에티오피아 예가체프 워시드, 레몬 향", "profile": Profile()}))
    assert c["tags"][0] == "lemon"                                # text-extracted tag comes first
    assert c["evidence"][0] == "문구의 향미: 레몬"


def test_no_text_cue_leaves_prediction_unchanged():
    deps = fake_deps()
    c = first_card(run_events(build_analyze_graph(deps),
                              {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["body"] != 4.5 and not any("문구에" in e for e in c["evidence"])


def test_violation_is_reported_not_hidden():
    deps = fake_deps()
    c = first_card(run_events(build_analyze_graph(deps), {"coffee_id": 1,
                                                          "profile": Profile(caffeine_rule="decaf_only")}))
    assert c["violation"] == "디카페인이 아니에요"
    assert c["template"].startswith("주의: 디카페인이 아니에요 — ")


def test_violation_reaches_the_llm_prompt():
    deps = fake_deps()
    seen = []
    real = deps.stream_text

    def spy(task, messages):
        seen.append(messages)
        return real(task, messages)
    deps.stream_text = spy
    run_events(build_analyze_graph(deps), {"coffee_id": 2, "profile": Profile(caffeine_rule="decaf_only")})
    assert '"조건 위반": "디카페인이 아니에요"' in seen[0][1]["content"]
    assert "첫 문장에서 그 위반" in seen[0][0]["content"]


def test_explanation_past_deadline_falls_back_to_template(monkeypatch):
    import asyncio

    from app import config
    monkeypatch.setattr(config, "EXPLAIN_DEADLINE_S", 0.05)
    deps = fake_deps()

    async def slow(task, messages):
        yield "느린 "
        await asyncio.sleep(1)
        yield "답변"
    deps.stream_text = slow
    events = run_events(build_analyze_graph(deps), {"coffee_id": 2, "profile": Profile()})
    fb = [e for e in events if e["type"] == "explain_fallback"]
    assert len(fb) == 1 and fb[0]["text"].startswith("취향 적합도")
    assert not any(e["type"] == "explain_done" for e in events)


# --- open-variant feature model (docs/adr/0011-roaster-gauges-feature-model.md) --------------------------
def _fake_feature_model():
    from app.core.featuremodel import FeatureModel
    return FeatureModel.from_doc({"attrs": {"acidity": {"intercept": 3.0, "weights": {
        "process_washed": 0.4, "region_east_africa": 0.3, "roast_dark": -1.0, "nbr": 0.0}}}})


def test_feature_model_replaces_shipped_attribute_and_explains_it():
    base = first_card(run_events(build_analyze_graph(fake_deps()),
                                 {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    deps = fake_deps(feature_model=_fake_feature_model())
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["acidity"] == 3.7                                   # 3.0 + washed 0.4 + east africa 0.3
    assert c["body"] == base["body"] and c["sweetness"] == base["sweetness"]   # not shipped -> neighbour avg
    assert "특징 모델 산미 3.7/5: 워시드·동아프리카 ↑ (기준값 3.0)" in c["evidence"]
    assert not any(e.startswith("유사 원두 산미 평균") for e in c["evidence"])   # replaced value, line dropped


def test_feature_model_applies_in_degraded_mode_too():
    deps = fake_deps(embed_fails=True, feature_model=_fake_feature_model())
    c = first_card(run_events(build_analyze_graph(deps), {"text": "브라질 강배전", "profile": Profile()}))
    assert c["confidence"] == "low" and c["acidity"] == 2.0      # 3.0 - dark 1.0; no embedding needed


def test_text_cue_overrides_feature_model():
    deps = fake_deps(feature_model=_fake_feature_model())
    c = first_card(run_events(build_analyze_graph(deps),
                              {"text": "에티오피아 예가체프 워시드, 산미 약한", "profile": Profile()}))
    assert c["acidity"] == 1.5                                   # cue value, not the model's 3.7
    assert any(e.startswith("문구에") for e in c["evidence"])


def test_feature_model_only_enabled_for_open_variant(monkeypatch):
    from app import config
    from app.graphs import _feature_model_enabled
    monkeypatch.setattr(config, "DATA_VARIANT", "full")
    assert _feature_model_enabled() is False
    monkeypatch.setattr(config, "DATA_VARIANT", "open")
    assert _feature_model_enabled() is True
    monkeypatch.setenv("FEATURE_MODEL", "off")
    assert _feature_model_enabled() is False


def test_regression_light_roast_free_text_value_matches_evidence_and_keeps_note_words():
    """Live open-variant report: "에티오피아 … 라이트 로스트" showed acidity 2.94 next to "유사 원두 산미 평균 4.1/5" and
    "특징 모델: … → 산미↑", "라이트 로스트" set body 1.5 as if it said light body, and the guest's own 딸기/자스민
    were lost (peach, chocolate shown). The feature model (gauge-scale intercept below the neighbour average) made
    the value; now the evidence states that value, the roast is not a body cue, and the note words come first."""
    from app.core.featuremodel import FeatureModel
    from tests.app.fakes import TAG_BASE_RATES, TAG_KO, TAG_TO_CAT, FakeRepo

    class Repo(FakeRepo):
        def taxonomy(self):
            return {**TAG_TO_CAT, "strawberry": "fruity"}, {**TAG_KO, "strawberry": "딸기", "jasmine": "재스민"}

        def tag_base_rates(self):
            return {**TAG_BASE_RATES, "strawberry": 0.1}

    fm = FeatureModel.from_doc({"attrs": {"acidity": {"intercept": 2.2, "weights": {
        "roast_light": 0.3, "country_Ethiopia": 0.2, "region_east_africa": 0.2, "nbr": 0.1}}}})
    deps = fake_deps(repo=Repo(), feature_model=fm)
    c = first_card(run_events(build_analyze_graph(deps),
                              {"text": "에티오피아 구지 내추럴 딸기 자스민 라이트 로스트", "profile": Profile()}))
    shown = f"{c['acidity']:.1f}"
    assert c["acidity"] < 4.0                                    # the gauge-scale model value, below the nbr mean
    model_lines = [e for e in c["evidence"] if e.startswith("특징 모델 산미")]
    assert len(model_lines) == 1 and model_lines[0].startswith(f"특징 모델 산미 {shown}/5: ")
    assert "(기준값 2.2)" in model_lines[0]
    assert not any(e.startswith("유사 원두 산미 평균") for e in c["evidence"])
    assert c["body"] == 2                                        # neighbour body, not "라이트" -> 1.5
    assert c["tags"][:2] == ["strawberry", "jasmine"]            # the guest's own words first
    assert c["evidence"][0] == "문구의 향미: 딸기, 재스민"


def test_text_cue_drops_the_replaced_attributes_model_line():
    deps = fake_deps(feature_model=_fake_feature_model())
    c = first_card(run_events(build_analyze_graph(deps),
                              {"text": "에티오피아 예가체프 워시드, 산미 약한", "profile": Profile()}))
    assert c["acidity"] == 1.5
    assert not any(e.startswith(("특징 모델 산미", "유사 원두 산미 평균")) for e in c["evidence"])


def test_db_coffee_with_a_missing_attribute_is_not_high_confidence():
    from dataclasses import replace as dc_replace

    from tests.app.fakes import FakeRepo
    repo = FakeRepo()
    repo.coffees[2] = dc_replace(repo.coffees[2], acidity=None,
                                 confidence="medium")   # Repo._coffee_item caps it (see test_repo / test_models)
    c = first_card(run_events(build_analyze_graph(fake_deps(repo=repo)), {"coffee_id": 2, "profile": Profile()}))
    assert c["acidity"] is None and c["confidence"] != "high"


def test_predicted_card_confidence_capped_when_an_attribute_is_missing():
    # neighbour vote gives all three; a model returning None for sweetness must not leave the card "high"
    from app.core.predict import item_from_prediction
    from app.models import ParsedBean, Prediction
    pred = Prediction(acidity=3.0, body=3.0, sweetness=None, confidence="high", tags=[], evidence=[], n_neighbors=10)
    assert item_from_prediction(ParsedBean(text="x"), pred).confidence == "medium"
    pred2 = Prediction(acidity=None, body=3.0, sweetness=None, confidence="high", tags=[], evidence=[], n_neighbors=10)
    assert item_from_prediction(ParsedBean(text="x"), pred2).confidence == "low"


# --- open variant v3 (docs/adr/0016-open-variant-v3.md) -------------------------------------------------------
def _v3_model(**extra):
    from app.core.featuremodel import FeatureModel
    doc = {"attrs": {"acidity": {"intercept": 3.0, "weights": {"process_washed": 0.4, "nbr": 0.0}},
                     "sweetness": {"intercept": 3.8, "weights": {"roast_dark": 0.5}}}, **extra}
    return FeatureModel.from_doc(doc)


class _NoSweetRepo:
    """FakeRepo whose neighbours carry no sweetness labels (the open pool often has none)."""
    def __new__(cls):
        from app.models import Neighbor
        from tests.app.fakes import FakeRepo

        class R(FakeRepo):
            def neighbors(self, vec, k=10, origin=None, process=None, exclude_id=None, exclude_sources=(), tagged_only=False):
                return [Neighbor(100 + i, f"n{i}", 0.9, 4.0, 2, None, ("lemon",)) for i in range(k)]
        return R()


def test_open_sweetness_abstains_without_support_and_says_so():
    deps = fake_deps(repo=_NoSweetRepo(), feature_model=_v3_model(abstain={"attrs": ["sweetness"]}))
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["sweetness"] is None and "단맛: 근거 부족" in c["evidence"]
    assert not any(e.startswith("특징 모델 단맛") for e in c["evidence"])
    assert c["acidity"] == 3.4 and c["confidence"] != "high"           # capped: one attribute missing


def test_open_sweetness_answers_with_neighbour_support_or_a_cue():
    deps = fake_deps(feature_model=_v3_model(abstain={"attrs": ["sweetness"]}))   # fake neighbours carry sweetness 3
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["sweetness"] == 3.8 and "단맛: 근거 부족" not in c["evidence"]
    deps = fake_deps(repo=_NoSweetRepo(), feature_model=_v3_model(abstain={"attrs": ["sweetness"]}))
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드, 달콤한", "profile": Profile()}))
    assert c["sweetness"] == 4.0 and "단맛: 근거 부족" not in c["evidence"]      # the guest's own cue answers it


def test_open_calibrated_confidence_per_attribute_on_the_card():
    cal = {"levels": {"acidity": {"*": "medium", "facts": "medium"}, "body": {"*": "high"},
                      "sweetness": {"*": "low"}}}
    deps = fake_deps(feature_model=_v3_model(calibration=cal))
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["attr_confidence"] == {"acidity": "medium", "body": "high", "sweetness": "low"}
    assert c["confidence"] == "low"                                    # the lowest shown attribute
    c = first_card(run_events(build_analyze_graph(deps),
                              {"text": "에티오피아 예가체프 워시드, 달콤한", "profile": Profile()}))
    assert c["attr_confidence"]["sweetness"] == "high"                 # a cue is the guest's own statement
    base = first_card(run_events(build_analyze_graph(fake_deps()), {"text": "에티오피아 예가체프 워시드",
                                                                     "profile": Profile()}))
    assert "attr_confidence" not in base                               # full variant: unchanged


def test_open_note_free_tag_model_replaces_the_vote_only_without_note_words():
    tags = {"features": ["region_east_africa", "process_washed"], "threshold": 0.3, "max_tags": 5,
            "tags": {"jasmine": {"intercept": -1.0, "weights": {"region_east_africa": 2.0}},
                     "chocolate": {"intercept": -3.0, "weights": {}}}}
    deps = fake_deps(feature_model=_v3_model(tags=tags))
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["tags"] == ["jasmine"]                                     # sigmoid(1) >= 0.3; chocolate 0.05 < 0.3
    assert c["evidence"][0].startswith("향미 모델(산지·가공·로스팅): ")
    assert not any("언급" in e for e in c["evidence"])                   # the vote's tag lines are gone
    c = first_card(run_events(build_analyze_graph(deps),
                              {"text": "에티오피아 예가체프 워시드, 레몬", "profile": Profile()}))
    assert c["tags"][0] == "lemon" and "jasmine" not in c["tags"]       # note words present: vote + own words


# --- open tag fill (docs/adr/0017-open-tag-fill.md) ---------------------------------------------------------------
class _MostlyUntaggedRepo:
    """FakeRepo whose plain neighbours carry no tags (coffee_open's CQI rows) while the tagged-only query finds
    jasmine/lemon beans; records the tagged-only calls."""
    def __new__(cls):
        from app.models import Neighbor
        from tests.app.fakes import FakeRepo

        class R(FakeRepo):
            tagged_calls = []

            def neighbors(self, vec, k=10, origin=None, process=None, exclude_id=None, exclude_sources=(),
                          tagged_only=False):
                if tagged_only:
                    self.tagged_calls.append(exclude_sources)
                    return [Neighbor(300 + i, f"t{i}", 0.8, 4, 2, 3, ("jasmine", "lemon") if i < 5 else ("lemon",))
                            for i in range(k)]
                return [Neighbor(100 + i, f"n{i}", 0.9, 4, 2, 3, ()) for i in range(k)]
        return R()


def test_open_thin_vote_is_topped_up_from_tagged_neighbours():
    repo = _MostlyUntaggedRepo()
    c = first_card(run_events(build_analyze_graph(fake_deps(repo=repo, tag_fill=True)),
                              {"text": "케냐 AA 워시드", "profile": Profile()}))
    assert c["tags"] == ["lemon", "jasmine"]
    assert repo.tagged_calls == [("roasterdb",)]
    assert c["evidence"][:2] == ["유사 원두(향미 표기 있는 것) 10개 중 10개에서 '레몬' 언급",
                                 "유사 원두(향미 표기 있는 것) 10개 중 5개에서 'jasmine' 언급"]
    # the guest's own note words still lead, the fill follows
    c = first_card(run_events(build_analyze_graph(fake_deps(repo=_MostlyUntaggedRepo(), tag_fill=True)),
                              {"text": "케냐 AA 초콜릿", "profile": Profile()}))
    assert c["tags"][0] == "chocolate" and "jasmine" in c["tags"]


def test_tag_fill_off_and_full_vote_skip_the_tagged_query():
    repo = _MostlyUntaggedRepo()
    c = first_card(run_events(build_analyze_graph(fake_deps(repo=repo)), {"text": "케냐 AA 워시드", "profile": Profile()}))
    assert c["tags"] == [] and repo.tagged_calls == []
    deps = fake_deps(tag_fill=True)          # the default fake neighbours all say lemon + (none) -> 1 tag -> fill runs
    c = first_card(run_events(build_analyze_graph(deps), {"text": "에티오피아 예가체프 워시드", "profile": Profile()}))
    assert c["tags"][0] == "lemon"


def test_tag_fill_skipped_in_degraded_mode():
    repo = _MostlyUntaggedRepo()
    run_events(build_analyze_graph(fake_deps(repo=repo, tag_fill=True, embed_fails=True)),
               {"text": "에티오피아 예가체프", "profile": Profile()})
    assert repo.tagged_calls == []


# --- open tag co-occurrence (docs/adr/0018-open-tag-cooccurrence.md) ----------------------------------------------
class _UntaggedRepo:
    """FakeRepo whose neighbours (plain and tagged-only) share no tag: the guest's note word stays alone."""
    def __new__(cls):
        from app.models import Neighbor
        from tests.app.fakes import FakeRepo

        class R(FakeRepo):
            def neighbors(self, vec, k=10, origin=None, process=None, exclude_id=None, exclude_sources=(),
                          tagged_only=False):
                return [Neighbor(100 + i, f"n{i}", 0.9, 4, 2, 3, ()) for i in range(k)]
        return R()


def _cooc():
    from app.core.tagcooc import TagCooc
    return TagCooc.from_beans([{"caramelized", "chocolate"}] * 4 + [{"caramelized"}] * 2 + [{"jasmine"}] * 4)


def test_open_lone_note_word_is_topped_up_from_cooccurrence():
    deps = fake_deps(repo=_UntaggedRepo(), tag_fill=True, tag_cooc=_cooc())
    c = first_card(run_events(build_analyze_graph(deps), {"text": "온두라스 디카페인 카라멜", "profile": Profile()}))
    assert c["tags"] == ["caramelized", "chocolate"]            # the guest's word first, then the co-occurring one
    # (the fake taxonomy has no Korean name for caramelized)
    assert c["evidence"][:2] == ["문구의 향미: caramelized", "'caramelized' 표기 원두 6개 중 4개가 '초콜릿'도 언급"]


def test_cooccurrence_needs_a_guest_word_and_is_off_without_the_table():
    c = first_card(run_events(build_analyze_graph(fake_deps(repo=_UntaggedRepo(), tag_cooc=_cooc())),
                              {"text": "온두라스 워시드", "profile": Profile()}))
    assert c["tags"] == []                                     # no note word typed: nothing to co-occur with
    c = first_card(run_events(build_analyze_graph(fake_deps(repo=_UntaggedRepo())),
                              {"text": "온두라스 디카페인 카라멜", "profile": Profile()}))
    assert c["tags"] == ["caramelized"]                        # full variant / TAG_COOC=off: no table, unchanged
