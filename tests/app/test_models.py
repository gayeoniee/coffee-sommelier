from app import tracing
from app.models import Item, Profile


def test_profile_roundtrip_ignores_unknown_keys():
    p = Profile(caffeine_rule="decaf_only", milk_ok=False, acidity=4.5, flavor_weights={"fruity": 0.5}, n_updates=2)
    d = p.to_dict() | {"unknown": 1}
    assert Profile.from_dict(d) == p


def test_item_attr_access():
    it = Item(key="coffee:1", name="x", source="db", acidity=4, tags=("lemon",))
    assert it.attr("acidity") == 4 and it.attr("body") is None


def test_tracing_is_noop_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    def f(x):
        return x

    assert tracing.enabled() is False
    assert tracing.traced("n")(f) is f
    with tracing.span("run"):
        pass


def test_traced_does_not_capture_inputs_or_outputs(monkeypatch):
    import langfuse
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    seen = {}

    def fake_observe(**kw):
        seen.update(kw)
        return lambda f: f
    monkeypatch.setattr(langfuse, "observe", fake_observe)
    tracing.traced("recommend.rank")
    assert seen == {"name": "recommend.rank", "capture_input": False, "capture_output": False}


def test_cap_confidence_by_missing_core_attributes():
    from app.models import cap_confidence
    assert cap_confidence("high", (3, 3, 3)) == "high"
    assert cap_confidence("high", (None, 3, 3)) == "medium"      # 모모스 디카페인: acidity null, was "high"
    assert cap_confidence("high", (None, None, 3)) == "low"
    assert cap_confidence("low", (None, 3, 3)) == "low"          # never raises
    assert cap_confidence("medium", (3, 3, 3)) == "medium"


def test_db_coffee_item_caps_confidence_when_an_attribute_is_null():
    from app.repo import _coffee_item
    row = {"id": 7, "name": "모모스 디카페인", "roaster": "모모스커피", "origin_country": "Colombia", "process": None,
           "is_decaf": True, "acidity": None, "body": 3, "sweetness": 4, "flavor_tags": []}
    assert _coffee_item(row).confidence == "medium"
    assert _coffee_item({**row, "acidity": 3}).confidence == "high"
