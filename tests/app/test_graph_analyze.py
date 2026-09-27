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
