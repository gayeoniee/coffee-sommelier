from app.graphs.analyze_bean import build_analyze_graph
from app.models import Profile
from tests.app.fakes import fake_deps, run_events


def first_card(events):
    return next(e for e in events if e["type"] == "cards")["cards"][0]


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
