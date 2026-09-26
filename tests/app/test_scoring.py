import pytest

from app.core.flavors import build_tag_to_category, category_vector, cosine
from app.core.scoring import attr_fit, flavor_fit, is_milk_drink, mmr_top_k, passes, score_item
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
