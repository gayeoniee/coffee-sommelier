import itertools

import pytest

from app.core.learning import update_profile
from app.core.predict import item_from_prediction, predict_from_neighbors
from app.core.simulate import simulate_convergence
from app.models import Item, Neighbor, ParsedBean, Profile

T2C = {"lemon": "fruity", "floral": "floral", "cocoa": "nutty/cocoa"}


def nb(i, sim, acidity, body, tags):
    return Neighbor(coffee_id=i, name=f"n{i}", similarity=sim, acidity=acidity, body=body, sweetness=None, tags=tags)


def test_prediction_weighted_mean_confidence_tags_and_evidence():
    neigh = [nb(1, 0.9, 4, 2, ("lemon",)), nb(2, 0.8, 4, 2, ("lemon", "floral")),
             nb(3, 0.7, 5, 2, ("cocoa",)), nb(4, 0.6, 4, 3, ("cocoa", "lemon"))]
    p = predict_from_neighbors(neigh, {"lemon": "레몬"})
    assert p.acidity == pytest.approx(4.23, abs=0.01)
    assert p.body == pytest.approx(2.2, abs=0.01)
    assert p.sweetness is None                      # no neighbor has sweetness
    assert p.confidence == "high"                   # stds ~0.42 / 0.40
    assert p.tags == ["lemon", "cocoa"]             # floral share 0.27 < 0.3
    assert p.evidence[0] == "유사 원두 4개 중 3개에서 '레몬' 언급"
    assert p.evidence[1] == "유사 원두 4개 중 2개에서 'cocoa' 언급"
    assert p.n_neighbors == 4


def test_tag_inclusion_is_count_based_not_weighted_share():
    neigh = [nb(0, 0.99, 4, 2, ("rose",))] + [nb(i, 0.1, 4, 2, ("lemon",)) for i in range(1, 10)]
    p = predict_from_neighbors(neigh)
    assert p.tags == ["lemon"]                      # rose is 1/10 by count, despite ~0.52 weighted share


def test_too_few_neighbors_is_low_confidence():
    p = predict_from_neighbors([nb(1, 0.9, 4, 2, ()), nb(2, 0.8, 1, 5, ())])
    assert (p.acidity, p.confidence, p.tags) == (None, "low", [])
    assert predict_from_neighbors([]).confidence == "low"


def test_item_from_prediction():
    parsed = ParsedBean(text="예가체프 디카페인", origin_country="Ethiopia", is_decaf=True)
    pred = predict_from_neighbors([nb(i, 0.9, 4, 2, ("lemon",)) for i in range(3)])
    it = item_from_prediction(parsed, pred)
    assert (it.key, it.source, it.name, it.is_decaf, it.acidity, it.tags) == (
        "input", "predicted", "예가체프 디카페인", True, 4.0, ("lemon",))


def item(**kw):
    return Item(**({"key": "coffee:1", "name": "x", "source": "db"} | kw))


def test_like_pulls_preference_toward_item():
    new, changes = update_profile(Profile(acidity=3), item(acidity=5), 5, T2C)
    assert new.acidity == pytest.approx(4.0)       # eta 1/2 * s 1 * d 2
    assert new.body == 3.0 and new.n_updates == 1
    assert "산미 선호 3.0→4.0" in changes


def test_dislike_pushes_only_when_close():
    near, _ = update_profile(Profile(acidity=3), item(acidity=4), 1, T2C)
    assert near.acidity == pytest.approx(2.75)
    far, _ = update_profile(Profile(acidity=2), item(acidity=5), 1, T2C)
    assert far.acidity == 2.0


def test_signals_and_clamp_and_flavors():
    p = Profile(acidity=3, sweetness=4.8)
    new, _ = update_profile(p, item(), 3, T2C, {"acidity": "lower", "sweetness": "higher",
                                                  "liked_flavors": ["floral", "bogus"]})
    assert (new.acidity, new.sweetness) == (2.5, 5.0)
    assert new.flavor_weights == {"floral": 0.3}
    liked, _ = update_profile(Profile(), item(tags=("lemon",)), 5, T2C)
    assert liked.flavor_weights == {"fruity": 0.5}
    assert p.acidity == 3                          # input profile untouched


def test_simulated_users_converge():
    grid = [Item(key=f"g{i}", name="g", source="db", acidity=a, body=b, sweetness=s)
            for i, (a, b, s) in enumerate(itertools.product(range(1, 6), repeat=3))]
    r = simulate_convergence(grid, {}, users=100, steps=10, seed=1)
    assert len(r["mae_by_step"]) == 11
    assert r["mae_by_step"][-1] < r["mae_by_step"][0] - 0.05
