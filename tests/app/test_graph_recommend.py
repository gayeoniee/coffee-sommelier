from app.graphs.recommend import build_recommend_graph, empty_reason
from app.models import Profile
from tests.app.fakes import FakeRepo, fake_deps, run_events


def test_recommend_streams_cards_then_three_explanations():
    deps = fake_deps()
    events = run_events(build_recommend_graph(deps),
                        {"brand_key": "brand:sb", "profile": Profile(acidity=4, body=2, flavor_weights={"fruity": 0.8})})
    cards = next(e for e in events if e["type"] == "cards")["cards"]
    assert len(cards) == 3 and cards[0]["name"] == "블론드 아메리카노"
    assert all("template" in c and "text" not in c for c in cards)
    done = [e for e in events if e["type"] == "explain_done"]
    assert sorted(e["key"] for e in done) == sorted(c["key"] for c in cards)
    assert all(e["text"] == "잘 맞아요" for e in done)
    assert events.index(next(e for e in events if e["type"] == "cards")) < events.index(done[0])


def test_decaf_user_gets_only_decaf_capable_drinks_with_order_flag():
    deps = fake_deps()
    events = run_events(build_recommend_graph(deps), {"brand_key": "brand:sb",
                                                       "profile": Profile(caffeine_rule="decaf_only")})
    cards = next(e for e in events if e["type"] == "cards")["cards"]
    assert {c["name"] for c in cards} == {"아메리카노", "카페 라떼"}
    americano = next(c for c in cards if c["name"] == "아메리카노")
    assert americano["order_decaf"] is True and americano["decaf_surcharge_krw"] == 300
    assert "디카페인으로 바꿔 주문하세요 (+300원)" in americano["template"]


def test_one_failed_explanation_falls_back_others_stream():
    deps = fake_deps(fail_keys=("블론드",))
    events = run_events(build_recommend_graph(deps), {"brand_key": "brand:sb",
                                                       "profile": Profile(acidity=4, body=2)})
    fb = [e for e in events if e["type"] == "explain_fallback"]
    assert len(fb) == 1 and fb[0]["text"].startswith("취향 적합도")
    assert len([e for e in events if e["type"] == "explain_done"]) == 2


def test_empty_when_nothing_passes():
    deps = fake_deps()
    events = run_events(build_recommend_graph(deps), {"brand_key": "brand:nodecaf",
                                                       "profile": Profile(caffeine_rule="decaf_only")})
    assert events == [{"type": "empty", "reason": "이 브랜드는 디카페인 메뉴가 없어요"}]
    assert deps.calls["stream"] == 0


def test_empty_reasons():
    assert empty_reason(Profile(), []) == "이 브랜드의 메뉴 정보가 없어요"
    items = FakeRepo().menu["brand:sb"]
    assert empty_reason(Profile(milk_ok=False, caffeine_rule="decaf_only"), items[1:2]) == "조건에 맞는 메뉴가 없어요"


def test_decaf_only_cards_are_all_decaf_or_ordered_decaf():
    deps = fake_deps()
    events = run_events(build_recommend_graph(deps), {"brand_key": "brand:sb",
                                                       "profile": Profile(caffeine_rule="decaf_only")})
    cards = next(e for e in events if e["type"] == "cards")["cards"]
    latte = next(c for c in cards if c["name"] == "카페 라떼")        # 75mg: low, but not decaf
    assert latte["order_decaf"] is True
    assert cards and all(c["is_decaf"] or c["order_decaf"] for c in cards)


def test_cards_carry_korean_tags():
    deps = fake_deps()
    events = run_events(build_recommend_graph(deps), {"brand_key": "brand:sb", "profile": Profile()})
    cards = next(e for e in events if e["type"] == "cards")["cards"]
    assert all("tags_ko" in c for c in cards)
    assert any("초콜릿" in c["tags_ko"] for c in cards)      # FakeRepo TAG_KO maps chocolate → 초콜릿
