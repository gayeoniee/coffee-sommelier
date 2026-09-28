import pytest

from app.core.flavors import build_tag_to_category, category_vector, cosine
from app.core.scoring import attr_fit, drink_family, flavor_fit, is_milk_drink, mmr_top_k, passes, score_item
from app.models import Item, Profile

T2C = {"lemon": "fruity", "jasmine": "floral", "chocolate": "nutty/cocoa", "nutty": "nutty/cocoa"}


def item(**kw):
    base = dict(key="coffee:1", name="x", source="db")
    return Item(**(base | kw))


def test_build_tag_to_category_uses_level_one_ancestor():
    rows = [("sca:fruity", 1, "fruity"), ("sca:fruity>citrus fruit", 2, "citrus fruit"),
            ("sca:fruity>citrus fruit>lemon", 3, "Lemon")]
    assert build_tag_to_category(rows) == {"citrus fruit": "fruity", "lemon": "fruity"}


def test_category_vector_and_cosine():
    v = category_vector(["lemon", "jasmine", "unknown"], T2C)
    assert v == {"fruity": 0.5, "floral": 0.5}
    assert cosine({"fruity": 1}, {"floral": 1}) == 0
    assert cosine({}, {"fruity": 1}) is None


@pytest.mark.parametrize("name,expected", [("카페 라떼", True), ("Iced Mocha", True), ("아메리카노", False),
                                           ("디카페인 콜드브루", False), ("바닐라 크림 콜드브루", True)])
def test_is_milk_drink(name, expected):
    assert is_milk_drink(name) is expected


def test_hard_filters():
    decaf_user = Profile(caffeine_rule="decaf_only")
    assert passes(decaf_user, item(is_decaf=False)) == (False, "디카페인이 아니에요")
    assert passes(decaf_user, item(decaf_option=True))[0] is True
    low = Profile(caffeine_rule="low")
    assert passes(low, item(caffeine_mg=80))[0] is True
    assert passes(low, item(caffeine_mg=150))[0] is False
    assert passes(low, item(caffeine_mg=None))[0] is False      # unknown caffeine is not "low"
    no_milk = Profile(milk_ok=False)
    assert passes(no_milk, item(is_milk=True)) == (False, "우유가 들어가요")


def test_attr_fit_skips_missing_and_halves_acidity_for_milk():
    p = Profile(acidity=5, body=3)
    assert attr_fit(p, item(acidity=5, body=3)) == 1.0
    assert attr_fit(p, item()) is None
    black = attr_fit(p, item(acidity=1, body=3))                 # (0 + 1) / 2
    milk = attr_fit(p, item(acidity=1, body=3, is_milk=True))   # (0.5*0 + 1) / 1.5
    assert black == pytest.approx(0.5) and milk == pytest.approx(2 / 3)


def test_flavor_fit_and_score_mix():
    p = Profile(acidity=4, body=3, sweetness=3, flavor_weights={"fruity": 1.0})
    it = item(acidity=4, body=3, sweetness=3, tags=("lemon",))
    assert flavor_fit(p, it, T2C) == pytest.approx(1.0)
    assert score_item(p, it, T2C) == pytest.approx(1.0)
    assert flavor_fit(Profile(), it, T2C) is None               # no preferences yet
    assert score_item(Profile(), item(), T2C) == 0.5            # nothing known


def test_mmr_prefers_diverse_second_pick():
    a = item(key="a", acidity=4, body=2, sweetness=3, tags=("lemon",))
    a2 = item(key="a2", acidity=4, body=2, sweetness=3, tags=("lemon",))     # near-duplicate of a
    b = item(key="b", acidity=2, body=4, sweetness=3, tags=("chocolate",))
    top = mmr_top_k([(a, 0.90), (a2, 0.89), (b, 0.80)], T2C, k=2)
    assert [i.key for i, _ in top] == ["a", "b"]
    assert mmr_top_k([], T2C) == []


def test_mmr_breaks_shown_score_ties_by_lower_caffeine_then_name():
    # franchise cards with the same shown score (83% vs 83%) and the same bean: lower caffeine first, then name,
    # unknown caffeine last -- whatever order the brand's menu came in
    base = dict(source="brand_bean", acidity=3, body=3, sweetness=3, tags=("chocolate",))
    hi = item(key="hi", name="아메리카노", caffeine_mg=150, **base)
    lo = item(key="lo", name="콜드브루", caffeine_mg=90, **base)
    unk = item(key="unk", name="가나", caffeine_mg=None, **base)
    same = item(key="same", name="나", caffeine_mg=90, **base)
    top = mmr_top_k([(hi, 0.8312), (unk, 0.83), (lo, 0.8301), (same, 0.829)], T2C, k=4)
    assert [i.key for i, _ in top][:2] == ["same", "lo"]        # 90mg tie -> name order ("나" < "콜드브루")
    assert [i.key for i, _ in top][-1] == "unk"
    # a real score gap still wins over caffeine
    assert mmr_top_k([(hi, 0.90), (lo, 0.80)], T2C, k=1)[0][0].key == "hi"


@pytest.mark.parametrize("name,expected", [
    ("디카페인 카페 라떼", "latte"), ("Iced Americano", "americano"), ("콜드 브루", "cold_brew"),
    ("블론드 바닐라 더블 샷 마키아또", "other"), ("자바 칩 프라푸치노", "frappuccino"),
    ("바닐라 크림 콜드브루", "cold_brew"), ("플랫 화이트", "flat_white"), ("카페 모카", "mocha"),
    ("Cappuccino", "cappuccino"), ("에스프레소", "espresso")])
def test_drink_family(name, expected):
    assert drink_family(name) == expected


def test_mmr_spreads_drink_families_when_attributes_are_identical():
    # menu items of one brand inherit the same brand-bean attributes; only the drink family tells them apart
    same = dict(acidity=3, body=3, sweetness=3, tags=("chocolate",))
    items = [(item(key="m1", name="카페 라떼", **same), 0.64), (item(key="m2", name="바닐라 라떼", **same), 0.64),
             (item(key="m3", name="카페 아메리카노", **same), 0.63), (item(key="m4", name="콜드 브루", **same), 0.62)]
    top = mmr_top_k(items, T2C, k=3)
    assert len(top) == 3
    assert sum(drink_family(i.name) == "latte" for i, _ in top) <= 1



def test_decaf_twin_key_pairs_real_brand_names():
    from app.core.scoring import decaf_twin_key as k
    pairs = [("카페 라떼", "디카페인 카페 라떼"),                               # 이디야
             ("꿀화이트 아메리카노", "디카페인 꿀화이트 아메리카노"),
             ("카페라떼", "디카페인 카페라떼"),                                  # 메가·컴포즈·폴바셋
             ("아이스 카페 오트", "아이스 디카페인 카페오트"),                  # 폴바셋
             ("아이스 카라멜 마키아토", "아이스 디카페인카라멜 마키아토"),
             ("스페니쉬 카페 라떼 [연유]", "디카페인 스페니쉬 카페라떼"),
             ("아메리카노(HOT)", "디카페인 아메리카노(ICED)"),                  # 빽다방
             ("챔피언스 블랙 벨벳 라떼 HOT", "디카페인 챔피언스 블랙 벨벳 라떼 ICED"),
             ("(ICE)헛개리카노", "(HOT)디카페인 헛개리카노"),                   # 메가
             ("콜드브루", "콜드브루디카페인"),
             ("할리스 데일리 커피", "할리스 데일리 커피 디카페인"),             # 할리스
             ("얼박샷추", "얼박샷추(디카페인 원두)"),
             ("Caffe Latte", "Decaf Caffe Latte")]
    for regular, decaf in pairs:
        assert k(regular) == k(decaf), (regular, decaf)
    assert k("아이스 카페라떼") != k("카페라떼")          # 폴바셋 sells both, each with its own decaf SKU
    assert k("빅포즈 카페라떼") != k("빅포즈 디카페인라떼")
    assert k("화이트 초콜릿 모카") != k("디카페인 카페 모카")


def test_decaf_order_caffeine_prefers_the_twin_then_the_brand_median():
    from app.core.scoring import DECAF_ESTIMATE_NOTE, DECAF_UNKNOWN_NOTE, decaf_order_caffeine, decaf_twins
    menus = [{"name": "카페 라떼", "is_decaf": False, "caffeine_mg": 202},
             {"name": "디카페인 카페 라떼", "is_decaf": True, "caffeine_mg": 7},
             {"name": "디카페인 카페 모카", "is_decaf": True, "caffeine_mg": 50},
             {"name": "디카페인 콜드브루", "is_decaf": True, "caffeine_mg": 12},
             {"name": "디카페인 원액", "is_decaf": True, "caffeine_mg": None}]
    twins = decaf_twins(menus)
    assert decaf_order_caffeine("카페 라떼", twins) == (7.0, DECAF_ESTIMATE_NOTE)
    assert decaf_order_caffeine("달달커피", twins) == (12.0, DECAF_ESTIMATE_NOTE)     # median of 7/12/50
    assert decaf_order_caffeine("카페 아메리카노", {}) == (None, DECAF_UNKNOWN_NOTE)   # brand sells no decaf SKU


def test_decaf_only_rejects_high_caffeine_decaf_sku():
    from app.core.scoring import passes
    from app.models import Item, Profile
    p = Profile(caffeine_rule="decaf_only")
    mocha = Item(key="menu:1", name="디카페인 카페모카", source="brand_bean", is_decaf=True, caffeine_mg=136.7)
    ok, why = passes(p, mocha)
    assert not ok and "136.7mg" in why
    assert passes(p, Item(key="menu:2", name="디카페인 아메리카노", source="brand_bean", is_decaf=True, caffeine_mg=7))[0]
    assert passes(p, Item(key="menu:3", name="디카페인 라떼", source="brand_bean", is_decaf=True, caffeine_mg=None))[0]
    # low-caffeine guests (≤100 mg): a 136.7 mg "decaf" mocha fails too; a 50 mg one is fine
    assert not passes(Profile(caffeine_rule="low"), mocha)[0]
    assert passes(Profile(caffeine_rule="low"), Item(key="menu:4", name="디카페인 카페 모카", source="brand_bean", is_decaf=True, caffeine_mg=50))[0]
