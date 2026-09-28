import datetime as dt

from pipeline.records import CoffeeRecord, MenuItemRecord
from scripts.refresh.diff import coffee_changed, diff_coffees, diff_menus, menu_unchanged, size_check


def menu(key, brand, name, mg=None, decaf=False, opt=False):
    return MenuItemRecord(key=key, brand_key=brand, name=name, caffeine_mg=mg, is_decaf=decaf, decaf_option=opt,
                          collected_at="2026-09-28")


def old_menu(brand, name, mg=None, decaf=False, opt=False):
    return {"brand_key": brand, "name": name, "name_en": None, "category": None, "is_decaf": decaf,
            "decaf_option": opt, "caffeine_mg": mg, "source_url": None, "collected_at": dt.date(2026, 9, 1)}


def test_diff_menus_counts_every_kind_of_change():
    old = {"a1": old_menu("brand:a", "아메리카노", 150), "a2": old_menu("brand:a", "카페 라떼", 100),
           "a3": old_menu("brand:a", "단종 메뉴"), "a4": old_menu("brand:a", "디카페인 라떼", 10, opt=False),
           "b1": old_menu("brand:b", "아메리카노", 120)}
    new = [menu("a1", "brand:a", "아메리카노", 160),        # +6.7 %: changed, not flagged
           menu("a2", "brand:a", "카페 라떼", 150),          # +50 %: flagged
           menu("a4", "brand:a", "디카페인 라떼", 10, decaf=True),
           menu("a5", "brand:a", "신메뉴 크림 콜드브루", 200),
           menu("c1", "brand:c", "아메리카노")]
    labels = {"아메리카노": False, "카페 라떼": True, "디카페인 라떼": True}
    d = diff_menus(old, new, labels, brand_scope={"brand:a", "brand:c"})
    c = d["counts"]
    assert (c["new"], c["removed"], c["caffeine_changed"], c["caffeine_flagged"], c["decaf_changed"]) == (2, 1, 2, 1, 1)
    assert d["removed"] == [{"key": "a3", "brand": "brand:a", "name": "단종 메뉴"}]    # b1: brand b not collected -> kept
    assert d["caffeine_flagged"][0]["name"] == "카페 라떼" and d["caffeine_flagged"][0]["change"] == 0.5
    assert d["new_brands"] == ["brand:c"]
    assert d["label_needed"] == ["신메뉴 크림 콜드브루"]
    assert d["brands_kept_not_collected"] == ["brand:b"]


def test_caffeine_appearing_or_vanishing_is_flagged():
    old = {"a1": old_menu("brand:a", "아메리카노", None)}
    d = diff_menus(old, [menu("a1", "brand:a", "아메리카노", 150)], {"아메리카노": False}, {"brand:a"})
    assert d["counts"]["caffeine_flagged"] == 1 and d["caffeine_flagged"][0]["change"] is None


def test_menu_unchanged_ignores_collected_at_and_int_vs_float():
    assert menu_unchanged(old_menu("brand:a", "아메리카노", 150), menu("a1", "brand:a", "아메리카노", 150.0))
    assert not menu_unchanged(old_menu("brand:a", "아메리카노", 150), menu("a1", "brand:a", "아메리카노", 151))


def coffee(key, roaster="A", source="roasters_kr", **kw):
    return CoffeeRecord(key=key, name=kw.pop("name", key), roaster=roaster, source=source, collected_at="2026-09-28", **kw)


def old_coffee(c: CoffeeRecord, **kw):
    d = c.model_dump()
    d.update({"embedding": [0.1], "collected_at": dt.date(2026, 9, 1)})
    d.update(kw)
    return d


def test_coffee_changed_rules():
    c = coffee("k", origin_country="Ethiopia")
    assert not coffee_changed(old_coffee(c), c, None, None)
    # enrich filled these in the DB; the source gives none -> not a change
    assert not coffee_changed(old_coffee(c, acidity=4, flavor_tags=["berry"], is_decaf=False), c, None, None)
    assert coffee_changed(old_coffee(c), c.model_copy(update={"origin_country": "Kenya"}), None, None)
    assert coffee_changed(old_coffee(c, acidity=3), c.model_copy(update={"acidity": 4}), None, None)   # new gauge
    assert coffee_changed(old_coffee(c), c.model_copy(update={"is_decaf": True}), None, None)
    assert coffee_changed(old_coffee(c, embedding=None), c, None, None)
    assert coffee_changed(old_coffee(c), c, "old note", "new note")


def test_diff_coffees_scope_todo_and_kept_groups():
    a1, a2, b1 = coffee("a1"), coffee("a2"), coffee("b1", roaster="B")
    old = {k.key: old_coffee(k) for k in (a1, a2, b1)}
    new = [a1.model_copy(update={"process": "washed"}), coffee("a3", name="New Bean")]
    d = diff_coffees(old, new, {}, {})
    assert d["counts"] == {"current": 3, "collected": 2, "new": 1, "removed": 1, "changed": 1, "unchanged": 0,
                           "new_roasters": 0}
    assert sorted(d["todo"]) == ["a1", "a3"]
    assert d["scope"] == ["roasters_kr:A"] and d["groups_kept_not_collected"] == ["roasters_kr:B"]
    assert d["removed"] == [{"key": "a2", "name": "a2", "roaster": "A"}]


def test_size_check():
    assert size_check({"menu_items": (500, 100)}) == []                     # exactly 20 %: fine
    fails = size_check({"menu_items": (500, 101), "coffees": (9000, 10)})
    assert len(fails) == 1 and fails[0].startswith("menu_items: 101/500")
    assert size_check({}, [("brand:mega", 119, 10)])[0].startswith("brand:mega: 119개 → 10개")
    assert size_check({}, [("roasters_kr:A", 10, 6), ("roasters_kr:B", 4, 0)]) == []   # rotation / tiny group
