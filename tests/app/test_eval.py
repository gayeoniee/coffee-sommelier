from dataclasses import replace

from app.eval import (LOO_TARGET_SOURCES, NEVER_LOO_TARGETS, OPEN_LICENSE_EXCLUDE, PERSONAS, VARIANTS, independent_ok,
                      rank_decaf, summarize_explain_quality, violation_rate)
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


def test_explain_cases_file_is_well_formed():
    from app.eval import load_explain_cases
    cases = load_explain_cases()
    assert len(cases) == 24 and len({c["id"] for c in cases}) == 24
    assert {c["persona"] for c in cases} == {p for p, _ in PERSONAS}
    assert sum(1 for c in cases if c["violation"]) >= 4
    assert all(isinstance(c["item"], Item) for c in cases)
    assert {c["item"].source for c in cases} == {"db", "brand_bean", "predicted"}
    assert sum(1 for c in cases if c["score"] <= 0.35) >= 2 and sum(1 for c in cases if c["score"] >= 0.85) >= 2
    assert all(c["prediction"] is not None for c in cases if c["item"].source == "predicted")


def _row(fallback=False, rules_ok=True, j1=None, j2=None, first=1.0):
    rules = {"foreign_words": True, "length": rules_ok, "numbers_grounded": True, "condition_mentioned": True,
             "polarity": True}
    return {"fallback": fallback, "first_token_s": None if fallback else first, "rules": rules,
            "judges": {"judge": j1, "judge2": j2}}


def _v(c=False, h=False, helpful=4):
    return {"contradiction": c, "hallucination": h, "helpful": helpful}


def test_summarize_explain_quality_counts_generated_only():
    rows = [_row(j1=_v(), j2=_v(helpful=2), first=1.0),
            _row(rules_ok=False, j1=_v(c=True), j2=_v(), first=3.0),
            _row(j1=_v(h=True), j2=None, first=2.0),
            _row(fallback=True, j1=_v(c=True), j2=_v(c=True))]
    s = summarize_explain_quality(rows)
    assert (s["n"], s["generated"], s["fallbacks"]) == (4, 3, 1)
    assert s["rule_pass_rate"] == round(2 / 3, 4) and s["rule_failures"]["length"] == 1
    assert s["judged_both"] == 2                     # the judge2=None row is left out of two-judge rates
    assert s["no_contradiction_rate_both"] == 0.5 and s["no_hallucination_rate_both"] == 1.0
    assert s["judge_agreement"] == {"contradiction": 0.5, "hallucination": 1.0}
    assert s["helpful_mean"] == {"judge": 4.0, "judge2": 3.0}
    assert s["judge_failures"] == {"judge": 0, "judge2": 1}
    assert s["first_token_p50"] == 2.0 and s["first_token_p95"] == 2.9
