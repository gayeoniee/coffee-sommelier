from dataclasses import replace

from app.eval import LOO_TARGET_SOURCES, NEVER_LOO_TARGETS, OPEN_LICENSE_EXCLUDE, VARIANTS, independent_ok, rank_decaf, violation_rate
from app.models import Item, Profile
from tests.app.fakes import FakeRepo


def test_independent_check_uses_raw_fields():
    decaf = Profile(caffeine_rule="decaf_only", milk_ok=False)
    it = Item(key="menu:1", name="아메리카노", source="brand_bean", menu_item_id=1, order_decaf=True)
    assert independent_ok(decaf, it, {"name": "아메리카노", "is_decaf": False, "decaf_option": True,
                                      "caffeine_mg": 150}, True)
    assert not independent_ok(decaf, it, {"name": "아메리카노", "is_decaf": False, "decaf_option": False,
                                          "caffeine_mg": 150}, True)
    assert not independent_ok(decaf, it, {"name": "카페 라떼", "is_decaf": True, "decaf_option": False,
                                          "caffeine_mg": 10}, True)
    synthetic = Item(key="brand:x:아메리카노", name="아메리카노", source="brand_bean", order_decaf=True)
    assert independent_ok(decaf, synthetic, None, True) and not independent_ok(decaf, synthetic, None, False)


def test_violation_rate_on_fake_catalog_is_zero():
    class Repo(FakeRepo):
        def raw_menu(self, menu_item_id):
            for items in self.menu.values():
                for i in items:
                    if i.menu_item_id == menu_item_id:
                        return {"name": i.name, "is_decaf": i.is_decaf, "decaf_option": i.decaf_option,
                                "caffeine_mg": i.caffeine_mg}
            return None
    r = violation_rate(Repo())
    assert r["checked"] > 0 and r["violations"] == 0 and r["rate"] == 0.0


def test_independent_milk_check_uses_hand_labels_then_own_markers():
    no_milk = Profile(milk_ok=False)

    def raw(name):
        return {"name": name, "is_decaf": False, "decaf_option": True, "caffeine_mg": 75}
    it = Item(key="menu:1", name="x", source="brand_bean", menu_item_id=1)
    assert not independent_ok(no_milk, it, raw("코르타도"), True)            # hand label (no marker hits it)
    assert not independent_ok(no_milk, it, raw("에스프레소 콘 파나"), True)
    assert independent_ok(no_milk, it, raw("카페 아메리카노"), True)
    assert not independent_ok(no_milk, it, raw("처음 보는 라떼"), True)       # not labelled -> marker fallback
    assert independent_ok(no_milk, it, raw("처음 보는 에이드"), True)


def test_decaf_only_needs_decaf_or_order_decaf_flag():
    decaf = Profile(caffeine_rule="decaf_only")
    raw = {"name": "카페 라떼", "is_decaf": False, "decaf_option": True, "caffeine_mg": 75}
    flagged = Item(key="menu:1", name="카페 라떼", source="brand_bean", menu_item_id=1, decaf_option=True,
                   order_decaf=True)
    unflagged = Item(key="menu:1", name="카페 라떼", source="brand_bean", menu_item_id=1, decaf_option=True)
    assert independent_ok(decaf, flagged, raw, True)
    assert not independent_ok(decaf, unflagged, raw, True)             # would be served caffeinated
    assert independent_ok(decaf, unflagged, raw | {"is_decaf": True, "decaf_option": False}, True)
    synthetic = Item(key="brand:x:아메리카노", name="아메리카노", source="brand_bean", decaf_option=True)
    assert not independent_ok(decaf, synthetic, None, True)
    assert independent_ok(decaf, replace(synthetic, order_decaf=True), None, True)


def test_variants_keep_open_comparable_and_targets_out_of_every_exclusion():
    assert VARIANTS["full"] == ()
    assert set(VARIANTS["open"]) == {"coffeereview_kaggle", "roasters_kr"} and OPEN_LICENSE_EXCLUDE == VARIANTS["open"]
    assert VARIANTS["open_plus"] == ("coffeereview_kaggle",)
    assert not any(set(LOO_TARGET_SOURCES) & set(xs) for xs in VARIANTS.values())   # same targets everywhere
    assert "roasters_kr" not in LOO_TARGET_SOURCES and "roasters_kr" in NEVER_LOO_TARGETS


def test_rank_decaf_counts_candidates_and_ranks_by_fit():
    tag_to_cat = {"lemon": "fruity", "jasmine": "floral", "chocolate": "nutty/cocoa"}
    decaf = Profile(caffeine_rule="decaf_only", acidity=4.5, body=2.5, sweetness=3,
                    flavor_weights={"fruity": 0.6, "floral": 0.5})

    def bean(name, **kw):
        return Item(key=f"coffee:{name}", name=name, source="db", is_decaf=kw.pop("is_decaf", True), **kw)
    beans = [(bean("bright", acidity=5, body=2, tags=("lemon", "jasmine")), "roasters_kr"),
             (bean("dark", acidity=1, body=5, tags=("chocolate",)), "cqi"),
             (bean("bare"), "roasters_kr"),                                # no attrs, no tags
             (bean("caf", acidity=5, is_decaf=False), "cqi")]              # fails the decaf filter
    r = rank_decaf(decaf, beans, tag_to_cat, k=5)
    assert (r["candidates"], r["with_evidence"]) == (3, 2)
    assert r["by_source"] == {"roasters_kr": 2, "cqi": 1}
    assert [t["name"] for t in r["top"]] == ["bright", "dark"] and r["top"][0]["source"] == "roasters_kr"
