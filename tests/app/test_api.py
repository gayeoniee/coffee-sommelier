import json

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
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
    r = client.post("/onboarding/samples", json=[{"coffee_id": 1, "liked": True}, {"coffee_id": 2, "liked": False}])
    assert r.status_code == 200 and r.json()["profile"]["n_updates"] == 2
    assert client.repo.tastings == []                  # samples don't create tastings


def test_catalog_endpoints(client):
    client.post("/session")
    assert client.get("/brands").json()[0]["key"] == "brand:sb"
    assert client.get("/coffees/search", params={"q": "brazil"}).json()[0]["name"] == "Brazil Cerrado"
    assert client.get("/health").json() == {"ok": True}
