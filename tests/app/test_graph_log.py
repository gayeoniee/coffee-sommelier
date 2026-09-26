import asyncio

from app.graphs.log_tasting import build_log_graph
from app.models import Item, Profile
from tests.app.fakes import FakeRepo, fake_deps


def run(deps, **inputs):
    base = {"user_id": "u1", "profile": Profile(acidity=3), "note": None, "persist_tasting": True,
            "item": Item(key="coffee:1", name="x", source="db", acidity=5, tags=("lemon",), coffee_id=1),
            "rating": 5, "target": {"coffee_id": 1}}
    return asyncio.run(build_log_graph(deps).ainvoke(base | inputs))


def test_like_updates_profile_and_persists_with_history():
    repo = FakeRepo()
    out = run(fake_deps(repo))
    assert out["new_profile"].acidity == 4.0 and out["new_profile"].flavor_weights == {"fruity": 0.5}
    assert out["summary"] == "산미 선호 3.0→4.0 · '과일' 선호 ↑"
    assert repo.tastings[0]["coffee_id"] == 1 and repo.tastings[0]["rating"] == 5
    assert repo.history("u1")[0]["tasting_id"] == out["tasting_id"]


def test_note_signals_are_applied_and_stored():
    repo = FakeRepo()
    out = run(fake_deps(repo, parse={"acidity": "lower"}), rating=3, note="산미가 너무 셌어요")
    assert out["signals"] == {"acidity": "lower", "liked_flavors": [], "disliked_flavors": []}
    assert out["new_profile"].acidity == 2.5
    assert repo.tastings[0]["parsed_signals"] == out["signals"] and repo.tastings[0]["note"] == "산미가 너무 셌어요"


def test_note_parse_failure_keeps_rating_update():
    out = run(fake_deps(json_fails=True), note="음...")
    assert out["signals"] is None and out["new_profile"].acidity == 4.0


def test_onboarding_sample_does_not_create_tasting():
    repo = FakeRepo()
    run(fake_deps(repo), persist_tasting=False)
    assert repo.tastings == [] and repo.get_profile("u1").n_updates == 1


def test_neutral_rating_without_note_reports_no_change():
    out = run(fake_deps(), rating=3)
    assert out["summary"] == "취향 변화 없음"
