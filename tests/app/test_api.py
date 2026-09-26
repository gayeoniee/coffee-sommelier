import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.api import RecommendIn, create_app
from app.models import Profile
from tests.app.fakes import FakeRepo, fake_deps


def sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def client():
    repo = FakeRepo()
    c = TestClient(create_app(repo=repo, deps=fake_deps(repo), cookie_secure=False))
    c.repo = repo
    return c


def onboard(client, **profile):
    client.post("/session")
    body = {"caffeine_rule": "any", "milk_ok": True, "acidity": 4, "body": 2, "sweetness": 3,
            "flavor_likes": ["fruity"]} | profile
    assert client.put("/me/profile", json=body).status_code == 200


def test_session_creates_guest_and_reuses_cookie(client):
    r1 = client.post("/session")
    assert r1.json()["new"] is True and r1.json()["has_profile"] is False
    assert "httponly" in r1.headers["set-cookie"].lower()
    r2 = client.post("/session")
    assert r2.json() == {"user_id": r1.json()["user_id"], "new": False, "has_profile": False}


def test_garbage_cookie_is_401_then_session_recovers(client):
    client.cookies.set("cs_uid", "not-a-uuid")
    assert client.get("/me").status_code == 401
    assert client.post("/session").json()["new"] is True
    assert client.get("/me").status_code == 200


def test_profile_validation_and_me(client):
    client.post("/session")
    bad = client.put("/me/profile", json={"caffeine_rule": "none", "milk_ok": True, "acidity": 9, "body": 3,
                                          "sweetness": 3, "flavor_likes": ["bogus"]})
    assert bad.status_code == 422
    onboard(client, nickname="가연")
    me = client.get("/me").json()
    assert me["profile"]["flavor_weights"] == {"fruity": 0.5} and me["nickname"] == "가연"
    assert len(me["history"]) == 1


def test_recommend_requires_profile_then_streams(client):
    client.post("/session")
    assert client.post("/recommend", json={"brand_key": "brand:sb"}).status_code == 409
    onboard(client)
    r = client.post("/recommend", json={"brand_key": "brand:sb"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    types = [t for t, _ in events]
    assert types[0] == "cards" and types[-1] == "done" and types.count("explain_done") == 3


def test_analyze_validates_and_streams_without_review_text(client):
    onboard(client)
    assert client.post("/analyze", json={}).status_code == 422
    events = sse_events(client.post("/analyze", json={"text": "에티오피아 예가체프 워시드"}).text)
    card = events[0][1]["cards"][0]
    assert card["source"] == "predicted" and "evidence" in card
    assert "review" not in json.dumps(card) and "text" not in card


def _last_telemetry_record(caplog):
    return json.loads(next(r for r in reversed(caplog.records) if r.name == "telemetry").getMessage())


def test_recommend_logs_telemetry_with_cards_and_fallback_count(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    repo = FakeRepo()
    c = TestClient(create_app(repo=repo, deps=fake_deps(repo, fail_keys=("블론드",)), cookie_secure=False))
    onboard(c)
    events = sse_events(c.post("/recommend", json={"brand_key": "brand:sb"}).text)
    assert events[-1][0] == "done"
    rec = _last_telemetry_record(caplog)
    assert rec["evt"] == "recommend" and rec["brand"] == "brand:sb"
    assert rec["cards"] == 3
    assert isinstance(rec["fallback"], int) and rec["fallback"] == 1
    assert rec["ms_total"] >= 0 and "error" not in rec


def test_analyze_telemetry_records_input_kind(caplog, client):
    caplog.set_level(logging.INFO, logger="telemetry")
    onboard(client)
    client.post("/analyze", json={"text": "에티오피아 예가체프 워시드"})
    assert _last_telemetry_record(caplog)["input"] == "text"
    client.post("/analyze", json={"coffee_id": 1})
    assert _last_telemetry_record(caplog)["input"] == "coffee_id"


def test_telemetry_logging_failure_does_not_break_the_stream(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("log boom")
    monkeypatch.setattr(logging.getLogger("telemetry"), "info", boom)
    onboard(client)
    r = client.post("/recommend", json={"brand_key": "brand:sb"})
    assert r.status_code == 200
    events = sse_events(r.text)
    assert events[0][0] == "cards" and events[-1][0] == "done"


def test_create_app_enables_telemetry_logger_without_caplog():
    from app import telemetry
    repo = FakeRepo()
    create_app(repo=repo, deps=fake_deps(repo), cookie_secure=False)
    create_app(repo=repo, deps=fake_deps(repo), cookie_secure=False)       # a second app adds no second handler
    lg = logging.getLogger("telemetry")
    assert lg.isEnabledFor(logging.INFO)
    assert len([h for h in lg.handlers if h.get_name() == telemetry.HANDLER_NAME]) == 1


def test_each_stream_request_writes_one_json_line_to_the_telemetry_handler(client):
    import io
    from app import telemetry
    handler = next(h for h in logging.getLogger("telemetry").handlers if h.get_name() == telemetry.HANDLER_NAME)
    buf = io.StringIO()
    handler.setStream(buf)
    try:
        onboard(client)
        client.post("/recommend", json={"brand_key": "brand:sb"})
        client.post("/analyze", json={"text": "에티오피아 예가체프 워시드"})
    finally:
        handler.setStream(None)          # back to the live sys.stdout
    lines = buf.getvalue().splitlines()
    assert [json.loads(x)["evt"] for x in lines] == ["recommend", "analyze"]
    assert all(json.loads(x)["aborted"] is False for x in lines)


def test_client_disconnect_mid_stream_still_logs_an_aborted_line(caplog, client):
    caplog.set_level(logging.INFO, logger="telemetry")
    onboard(client)
    uid = next(iter(client.repo.users))
    route = next(r for r in client.app.routes if getattr(r, "path", None) == "/recommend")
    resp = route.endpoint(RecommendIn(brand_key="brand:sb"), uid)

    async def disconnect():
        gen = resp.body_iterator
        assert (await gen.__anext__()).startswith("event: cards")
        with pytest.raises(asyncio.CancelledError):
            await gen.athrow(asyncio.CancelledError())        # what Starlette does when the client goes away

    asyncio.run(disconnect())
    rec = _last_telemetry_record(caplog)
    assert rec["evt"] == "recommend" and rec["aborted"] is True and rec["cards"] == 3


def test_tastings_flow_updates_profile(client):
    onboard(client)
    r = client.post("/tastings", json={"coffee_id": 1, "rating": 5, "note": ""})
    assert r.status_code == 200 and "산미 선호" in r.json()["summary"]
    assert client.get("/me").json()["tastings"][0]["rating"] == 5
    pred = client.post("/tastings", json={"input_text": "동네 블렌드", "rating": 2,
                                          "predicted": {"acidity": 3, "body": 3, "sweetness": 3, "tags": []}})
    assert pred.status_code == 200
    assert client.post("/tastings", json={"coffee_id": 999, "rating": 5}).status_code == 404
    assert client.post("/tastings", json={"coffee_id": 1, "menu_item_id": 10, "rating": 5}).status_code == 422
    assert client.post("/tastings", json={"coffee_id": 1, "rating": 6}).status_code == 422


def test_onboarding_samples_roundtrip(client):
    onboard(client)
    samples = client.get("/onboarding/samples").json()
    assert [s["coffee_id"] for s in samples] == [1, 2]
    assert samples[0]["tags_ko"] == ["레몬", "jasmine"] and samples[0]["description"] == "레몬·jasmine 향이 나는 원두"
    body = json.dumps(samples, ensure_ascii=False)
    assert "summary" not in body
    assert not any(s in body for s in client.repo.flavor_summaries.values())     # review-derived text never leaks
    r = client.post("/onboarding/samples", json=[{"coffee_id": 1, "liked": True}, {"coffee_id": 2, "liked": False}])
    assert r.status_code == 200 and r.json()["profile"]["n_updates"] == 2
    assert client.repo.tastings == []                  # samples don't create tastings


def test_catalog_endpoints(client):
    client.post("/session")
    assert client.get("/brands").json()[0]["key"] == "brand:sb"
    assert client.get("/coffees/search", params={"q": "brazil"}).json()[0]["name"] == "Brazil Cerrado"
    assert client.get("/health").json() == {"ok": True}


def test_tastings_menu_item_uses_order_decaf_or_profile_rule(client):
    onboard(client, caffeine_rule="low")
    seen = []
    real = client.repo.get_menu_item
    client.repo.get_menu_item = lambda mid, rule: seen.append(rule) or real(mid, rule)
    assert client.post("/tastings", json={"menu_item_id": 11, "rating": 4}).status_code == 200
    assert client.post("/tastings", json={"menu_item_id": 11, "order_decaf": True, "rating": 4}).status_code == 200
    assert seen == ["low", "decaf_only"]


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def test_async_endpoints_run_repo_calls_off_the_event_loop(client):
    onboard(client)
    calls = []
    for name in ("get_profile", "get_coffee", "get_menu_item"):
        real = getattr(client.repo, name)

        def spy(*a, _real=real, _name=name):
            calls.append((_name, _on_event_loop()))
            return _real(*a)
        setattr(client.repo, name, spy)
    assert client.post("/onboarding/samples", json=[{"coffee_id": 1, "liked": True}]).status_code == 200
    assert client.post("/tastings", json={"coffee_id": 1, "rating": 4}).status_code == 200
    assert client.post("/tastings", json={"menu_item_id": 10, "rating": 4}).status_code == 200
    assert client.post("/analyze", json={"coffee_id": 1}).status_code == 200
    assert {n for n, _ in calls} == {"get_profile", "get_coffee", "get_menu_item"}
    assert [c for c in calls if c[1]] == []


def test_post_samples_limits_body_and_runs_only_once(client):
    onboard(client)
    four = [{"coffee_id": 1, "liked": True}] * 4
    assert client.post("/onboarding/samples", json=four).status_code == 422
    assert client.post("/onboarding/samples", json=[{"coffee_id": 1, "liked": True}]).status_code == 200
    again = client.post("/onboarding/samples", json=[{"coffee_id": 2, "liked": True}])
    assert again.status_code == 409 and again.json()["detail"] == "이미 온보딩을 마쳤어요"


def test_synthetic_brand_card_can_be_logged_as_input_text(client):
    """Contract for menu-less brands: input_text = f"{brand} {name}", predicted = the card's attributes."""
    onboard(client)
    events = sse_events(client.post("/recommend", json={"brand_key": "brand:tw"}).text)
    card = events[0][1]["cards"][0]
    assert card["menu_item_id"] is None and card["coffee_id"] is None
    r = client.post("/tastings", json={
        "input_text": f"{card['brand']} {card['name']}", "rating": 4,
        "predicted": {k: card[k] for k in ("acidity", "body", "sweetness", "tags", "is_decaf")}})
    assert r.status_code == 200
    saved = client.repo.tastings[-1]
    assert saved["input_text"] == f"투썸 {card['name']}"
    assert saved["predicted"]["tags"] == card["tags"] and saved["predicted"]["acidity"] == card["acidity"]


def test_request_size_limits(client):
    onboard(client)
    assert client.get("/coffees/search", params={"q": "x" * 101}).status_code == 422
    too_many = {"input_text": "블렌드", "rating": 3, "predicted": {"tags": ["lemon"] * 11}}
    too_long = {"input_text": "블렌드", "rating": 3, "predicted": {"tags": ["x" * 41]}}
    assert client.post("/tastings", json=too_many).status_code == 422
    assert client.post("/tastings", json=too_long).status_code == 422


def test_analyze_unknown_coffee_is_404_before_streaming(client):
    onboard(client)
    r = client.post("/analyze", json={"coffee_id": 999})
    assert r.status_code == 404 and not r.headers["content-type"].startswith("text/event-stream")


def test_put_profile_unlikes_chips_but_keeps_learned_dislikes(client):
    onboard(client, flavor_likes=["fruity", "floral"])
    client.repo.profiles[next(iter(client.repo.profiles))].flavor_weights.update({"roasted": -0.4, "sweet": 0.1})
    body = {"caffeine_rule": "any", "milk_ok": True, "acidity": 4, "body": 2, "sweetness": 3,
            "flavor_likes": ["floral"]}
    weights = client.put("/me/profile", json=body).json()["profile"]["flavor_weights"]
    assert weights == {"fruity": 0.0, "floral": 0.5, "roasted": -0.4, "sweet": 0.0}


def test_allowed_origins_rejects_wildcard(monkeypatch):
    from app import config
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://a.test, http://b.test")
    assert config.allowed_origins() == ["http://a.test", "http://b.test"]
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://a.test,*")
    with pytest.raises(ValueError):
        config.allowed_origins()


def test_nickname_only_update_keeps_learned_weights(client):
    onboard(client)
    client.repo.save_profile(client.get("/me").json()["user_id"],
                             Profile(acidity=4, body=2, flavor_weights={"fruity": 0.5, "floral": 0.12}, n_updates=2))
    r = client.put("/me/nickname", json={"nickname": " 가연 "})
    assert r.status_code == 200 and r.json() == {"nickname": "가연"}
    me = client.get("/me").json()
    assert me["nickname"] == "가연" and me["profile"]["flavor_weights"] == {"fruity": 0.5, "floral": 0.12}
    assert client.put("/me/nickname", json={"nickname": "  "}).json() == {"nickname": None}
    assert client.put("/me/nickname", json={"nickname": "x" * 31}).status_code == 422
