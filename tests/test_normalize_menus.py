import json
import shutil
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _copy_fixture(name: str, tmp_path: Path) -> Path:
    """Copy tests/fixtures/<name>/ into tmp_path and return it, as a fake raw snapshot directory."""
    dst = tmp_path / name.replace("/", "_")
    shutil.copytree(FIXTURES / name, dst)
    return dst

from pipeline.collect import run_collect
from pipeline.normalize import run_normalize
from pipeline.normalize.menus import (
    normalize_brands, normalize_hollys, normalize_mega, normalize_paik, normalize_shopify, normalize_starbucks,
    normalize_brands, normalize_compose, normalize_mega, normalize_paik, normalize_shopify, normalize_starbucks,
    normalize_brands, normalize_mega, normalize_paik, normalize_paulbassett, normalize_shopify, normalize_starbucks,
)
from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, read_jsonl

MEGA_HTML = """
<ul><li><a class="inner_modal_open"></a>
 <div class="inner_modal"><div class="cont_text_box">
  <div class="cont_text inner_modal_title">
   <div class="cont_text_inner cont_text_title"><b>디카페인 아메리카노</b></div>
   <div class="cont_text_inner cont_text_info">Decaf Americano</div></div>
 </div><div class="cont_list"><ul><li>당류 0g</li><li>카페인 11.4mg</li></ul></div></div>
</li></ul>
"""
PAIK_HTML = """
<div class="hover"><h3 class="font-bl">원조커피(ICED)</h3><div class="menu_tit2">ORIGINAL</div>
 <ul class="ingredient_table"><li><div>칼로리 (kcal)</div><div>633.8</div></li>
 <li><div>카페인 (mg)</div><div>346</div></li></ul></div>
<div class="hover"><h3 class="font-bl">원조커피(ICED)</h3><div class="menu_tit2">ORIGINAL</div></div>
<div class="hover"><h3 class="font-bl">디카페인 아메리카노</h3>
 <ul class="ingredient_table"><li><div>카페인 (mg)</div><div>1.9</div></li></ul></div>
"""


def test_starbucks(tmp_path):
    (tmp_path / "W0000003.json").write_text(json.dumps({"list": [
        {"product_CD": "1", "product_NM": "아메리카노", "product_ENGNM": "", "cate_NAME": "아메리카노", "caffeine": "150"},
        {"product_CD": "2", "product_NM": "디카페인 카페 라떼", "cate_NAME": "라떼", "caffeine": ""},
    ]}, ensure_ascii=False), encoding="utf-8")
    items = normalize_starbucks(tmp_path, "2026-09-24").menu_items
    assert [(i.key, i.caffeine_mg, i.is_decaf, i.decaf_option) for i in items] == [
        ("menu:starbucks:1", 150.0, False, True), ("menu:starbucks:2", None, True, True)]
    assert items[0].name_en is None


def test_mega(tmp_path):
    (tmp_path / "page_1.html").write_text(MEGA_HTML, encoding="utf-8")
    [item] = normalize_mega(tmp_path, "2026-09-24").menu_items
    assert (item.name, item.name_en, item.caffeine_mg, item.is_decaf) == ("디카페인 아메리카노", "Decaf Americano", 11.4, True)


def test_paik_dedupes_and_reads_caffeine(tmp_path):
    (tmp_path / "coffee.html").write_text(PAIK_HTML, encoding="utf-8")
    items = {i.name: i for i in normalize_paik(tmp_path, "2026-09-24").menu_items}
    assert set(items) == {"원조커피(ICED)", "디카페인 아메리카노"}
    assert items["원조커피(ICED)"].caffeine_mg == 346.0
    assert items["디카페인 아메리카노"].is_decaf is True


def test_compose_parses_names_caffeine_decaf(tmp_path):
    snap = _copy_fixture("menus/compose", tmp_path)
    items = normalize_compose(snap, "2026-09-27").menu_items
    by = {i.name: i for i in items}
    assert len(items) == 4       # H-/I-아메리카노 and H-/I-디카페인 아메리카노 each merge into one
    assert by["아메리카노"].caffeine_mg == 185.81      # larger of HOT (150.00) / ICED (185.81)
    assert by["디카페인 아메리카노"].is_decaf and not by["디카페인 아메리카노"].decaf_option
    assert by["아메리카노"].decaf_option is True        # 커피ㆍ콜드브루 is compose's decaf-shot category
    assert by["쫀득카노"].caffeine_mg == 85.0            # no H-/I- prefix: kept as-is, not merged away
    assert by["빅포즈 아메리카노"].caffeine_mg == 371.62  # ICED-only size: no HOT counterpart to merge with
    assert all(i.brand_key == "brand:compose" and i.key.startswith("menu:compose:") for i in items)
    assert all(i.category == "커피ㆍ콜드브루" for i in items)
def test_paulbassett_parses_names_caffeine_decaf(tmp_path):
    snap = _copy_fixture("menus/paulbassett", tmp_path)
    items = normalize_paulbassett(snap, "2026-09-27").menu_items
    by = {i.name: i for i in items}
    assert len(items) == 5
    assert by["아메리카노"].caffeine_mg == 160.0
    assert by["아이스 아메리카노"].caffeine_mg == 160.0  # regular ("Standard") size, not the larger Venti option
    assert by["디카페인 아메리카노"].is_decaf and not by["디카페인 아메리카노"].decaf_option
    assert by["아메리카노"].decaf_option is True
    assert all(i.brand_key == "brand:paulbassett" and i.key.startswith("menu:paulbassett:") for i in items)
    assert sum(i.caffeine_mg is not None for i in items) / len(items) >= 0.9


def test_shopify_keeps_bean_products(tmp_path):
    (tmp_path / "shop.test.json").write_text(json.dumps({"products": [
        {"handle": "night-light", "title": "나이트 라이트 디카페인 원두", "product_type": "원두",
         "body_html": "<p>콜롬비아 디카페인. 키라임, 바닐라.</p>"},
        {"handle": "mug", "title": "머그", "product_type": "컵과 머그", "body_html": ""},
    ]}, ensure_ascii=False), encoding="utf-8")
    n = normalize_shopify(tmp_path, "2026-09-24",
                          shops=[{"domain": "shop.test", "roaster": "Shop", "product_types": ["원두"]}])
    [c] = n.coffees
    assert (c.key, c.roaster, c.is_decaf, c.origin_country) == ("shopify:shop.test:night-light", "Shop", True, "Colombia")
    assert n.reviews[0].text == "콜롬비아 디카페인. 키라임, 바닐라."


def test_normalize_brands_reads_curated_file():
    from pipeline import settings
    brands = normalize_brands(settings.CURATED_DIR)
    assert {b.key for b in brands} >= {"brand:starbucks", "brand:mega", "brand:paik"}


def test_run_normalize_writes_all_files(tmp_path):
    from dataclasses import dataclass

    @dataclass
    class FakeStarbucks:
        name: str = "starbucks"

        def collect(self, out_dir, http):
            p = out_dir / "W0000003.json"
            p.write_text('{"list": [{"product_CD": "1", "product_NM": "아메리카노", "caffeine": "150"}]}', encoding="utf-8")
            return [p]

    raw = tmp_path / "raw"
    run_collect([FakeStarbucks()], raw, None, "2026-09-24")
    curated = tmp_path / "curated"
    curated.mkdir()
    (curated / "brands.yaml").write_text(
        "- {key: 'brand:starbucks', name: 스타벅스, decaf_available: true, verified_at: '2026-09-24'}\n", encoding="utf-8")
    counts = run_normalize(raw, tmp_path / "norm", curated)
    assert counts["menu_items"] == 1 and counts["brands"] == 1 and counts["src:starbucks"] == 1
    assert read_jsonl(tmp_path / "norm" / "menu_items.jsonl", MenuItemRecord)[0].brand_key == "brand:starbucks"
    assert read_jsonl(tmp_path / "norm" / "brands.jsonl", BrandRecord)[0].name == "스타벅스"
    assert (tmp_path / "norm" / "coffees.jsonl").exists()


def test_shopify_roast_only_from_roast_sentences(tmp_path):
    (tmp_path / "shop.test.json").write_text(json.dumps({"products": [
        {"handle": "a", "title": "A", "product_type": "원두", "body_html": "<p>다크 초콜릿과 체리</p>"},
        {"handle": "b", "title": "B", "product_type": "원두", "body_html": "<p>다크 초콜릿과 체리</p><p>미디엄 로스트</p>"},
        {"handle": "c", "title": "C", "product_type": "원두", "body_html": "<p>Dark chocolate. Light Roast.</p>"},
    ]}, ensure_ascii=False), encoding="utf-8")
    n = normalize_shopify(tmp_path, "2026-09-24",
                          shops=[{"domain": "shop.test", "roaster": "Shop", "product_types": ["원두"]}])
    assert [c.roast_level for c in n.coffees] == [None, "medium", "light"]


SCA_TAGS_USED = {"chocolate", "dark chocolate", "cocoa", "nutty", "almonds", "hazelnut", "caramelized",
                 "brown sugar", "vanilla", "honey", "citrus fruit", "lemon", "lime", "orange", "berry",
                 "floral", "black tea", "brown roast", "smoky"}


def test_every_brand_has_bean_profiles():
    from pipeline import settings
    brands = normalize_brands(settings.CURATED_DIR)
    assert len(brands) == 10
    for b in brands:
        assert b.bean is not None, b.key
        assert set(b.bean.flavor_tags) <= SCA_TAGS_USED, b.key
        if b.decaf_available:
            assert b.decaf_bean is not None, b.key
            assert set(b.decaf_bean.flavor_tags) <= SCA_TAGS_USED, b.key


def test_bean_profile_range_is_validated():
    import pytest
    from pydantic import ValidationError
    from pipeline.records import BeanProfile
    with pytest.raises(ValidationError):
        BeanProfile(acidity=6, body=3, sweetness=3, flavor_tags=[])


def test_run_normalize_reads_flat_roasters_kr_and_drops_url_duplicates(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    shop = raw / "shopify" / "2026-09-24"
    shop.mkdir(parents=True)
    (shop / "manifest.json").write_text('{"ok": true}', encoding="utf-8")
    (shop / "shop.test.json").write_text(json.dumps({"products": [
        {"handle": "night", "title": "나이트", "product_type": "원두", "body_html": "<p>당밀</p>"}]}), encoding="utf-8")
    (raw / "roasters_kr").mkdir()
    beans = [{"key": "bb:night", "name": "나이트", "product_url": "https://shop.test/products/night",
              "flavor_notes": ["당밀"], "is_decaf": False, "collected_at": "2026-09-26"},
             {"key": "fritz:1", "name": "첼베사", "product_url": "https://fritz.test/1",
              "flavor_notes": ["레몬"], "is_decaf": False, "collected_at": "2026-09-26"}]
    (raw / "roasters_kr" / "beans.jsonl").write_text(
        "\n".join(json.dumps(b, ensure_ascii=False) for b in beans), encoding="utf-8")
    curated = tmp_path / "curated"
    curated.mkdir()
    (curated / "brands.yaml").write_text("[]\n", encoding="utf-8")
    import pipeline.normalize.menus as menus
    shops = [{"domain": "shop.test", "roaster": "Shop", "product_types": ["원두"]}]
    monkeypatch.setattr(menus.settings, "load_config", lambda name: {"shopify": shops})
    counts = run_normalize(raw, tmp_path / "norm", curated)
    keys = [c.key for c in read_jsonl(tmp_path / "norm" / "coffees.jsonl", CoffeeRecord)]
    assert keys == ["shopify:shop.test:night", "roasters_kr:fritz:1"]      # same product URL: first source wins
    assert counts["src:roasters_kr"] == 2 and counts["dropped_url_duplicates"] == 1


def test_menu_decaf_option_rule():
    from pipeline.normalize.menus import menu_decaf_option
    from pipeline.records import BrandRecord
    b = BrandRecord(key="brand:x", name="x", decaf_available=True, verified_at="2026-09-27",
                    decaf_option_categories=["에스프레소"])
    assert menu_decaf_option(b, "에스프레소", is_decaf=False) is True
    assert menu_decaf_option(b, "에스프레소", is_decaf=True) is False      # already decaf
    assert menu_decaf_option(b, "콜드브루", is_decaf=False) is False       # no decaf shot for cold brew
    assert menu_decaf_option(b, "에스프레소", False, name="콜드브루 라떼") is False   # brewed drink in an espresso category
    assert menu_decaf_option(b, "에스프레소", False, name="카페 라떼") is True
    assert menu_decaf_option(b.model_copy(update={"decaf_available": False}), "에스프레소", False) is False


def test_brands_yaml_has_decaf_option_categories_for_menu_brands():
    from pipeline.normalize.menus import brands_by_key
    from pipeline import settings
    b = brands_by_key(settings.CURATED_DIR)
    for k in ("brand:hollys", "brand:coffeebean", "brand:ediya", "brand:paulbassett", "brand:compose"):
        assert k in b and isinstance(b[k].decaf_option_categories, list)


def test_hollys_parses_names_caffeine_decaf(tmp_path):
    snap = _copy_fixture("menus/hollys", tmp_path)
    items = normalize_hollys(snap, "2026-09-27").menu_items
    by = {i.name: i for i in items}
    assert len(items) == 5   # 아메리카노, 카페 라떼, 디카페인 콜드브루, 에스프레소, 콜드브루 (HOT/ICED merged into one each)
    assert by["아메리카노"].caffeine_mg == 114.0
    assert by["카페 라떼"].caffeine_mg == 127.0
    assert by["디카페인 콜드브루"].is_decaf and not by["디카페인 콜드브루"].decaf_option
    assert by["아메리카노"].decaf_option is True                # 에스프레소 카테고리는 디카페인 샷 변경 가능
    assert by["에스프레소"].caffeine_mg == 61.0                 # HOT만 있는 항목
    assert by["콜드브루"].caffeine_mg == 195.0                  # ICED만 있는 항목
    assert all(i.brand_key == "brand:hollys" and i.key.startswith("menu:hollys:") for i in items)
    assert all(i.category == "에스프레소" for i in items)
def test_coffeebean_parses_names_caffeine_and_decaf_option(tmp_path):
    from pipeline import settings
    from pipeline.normalize.menus import brands_by_key, menu_decaf_option, normalize_coffeebean

    snap = _copy_fixture("menus/coffeebean", tmp_path)
    items = normalize_coffeebean(snap, "2026-09-27").menu_items
    by = {i.name: i for i in items}
    assert len(items) == 6
    # coffeebeankorea.com writes HOT/ICED as different names ("아메리카노" vs "아이스 아메리카노"),
    # never a HOT/ICED suffix on the same name, so they are kept as separate items (not merged).
    assert by["아메리카노"].caffeine_mg == 182.0
    assert by["아이스 아메리카노"].caffeine_mg == 91.0
    # The site's coffee menu has no item literally named decaf: decaf is a paid shot-swap option on
    # espresso drinks, never a distinct product, so is_decaf is False for every scraped item.
    assert all(not i.is_decaf for i in items)
    brand = brands_by_key(settings.CURATED_DIR)["brand:coffeebean"]
    assert menu_decaf_option(brand, by["아메리카노"].category, by["아메리카노"].is_decaf) is True
    assert menu_decaf_option(brand, by["콜드브루"].category, by["콜드브루"].is_decaf) is False  # brewed coffee, no shot to swap
    assert all(i.brand_key == "brand:coffeebean" and i.key.startswith("menu:coffeebean:") for i in items)
    assert sum(i.caffeine_mg is not None for i in items) / len(items) >= 0.9
