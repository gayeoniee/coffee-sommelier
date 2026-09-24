import json

from pipeline.collect import run_collect
from pipeline.normalize import run_normalize
from pipeline.normalize.menus import (
    normalize_brands, normalize_mega, normalize_paik, normalize_shopify, normalize_starbucks,
)
from pipeline.records import BrandRecord, MenuItemRecord, read_jsonl

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
