from app.eval import independent_ok, violation_rate
from app.models import Item, Profile
from tests.app.fakes import FakeRepo


def test_independent_check_uses_raw_fields():
    decaf = Profile(caffeine_rule="decaf_only", milk_ok=False)
    it = Item(key="menu:1", name="아메리카노", source="brand_bean", menu_item_id=1)
    assert independent_ok(decaf, it, {"name": "아메리카노", "is_decaf": False, "decaf_option": True,
                                      "caffeine_mg": 150}, True)
    assert not independent_ok(decaf, it, {"name": "아메리카노", "is_decaf": False, "decaf_option": False,
                                          "caffeine_mg": 150}, True)
    assert not independent_ok(decaf, it, {"name": "카페 라떼", "is_decaf": True, "decaf_option": False,
                                          "caffeine_mg": 10}, True)
    synthetic = Item(key="brand:x:아메리카노", name="아메리카노", source="brand_bean")
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
