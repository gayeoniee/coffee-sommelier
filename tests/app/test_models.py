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
