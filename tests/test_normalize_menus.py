import json
import shutil
from pathlib import Path

import pytest
import yaml

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
from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord, read_jsonl

MEGA_HTML = """
<ul><li><a class="inner_modal_open"></a>
 <div class="inner_modal"><div class="cont_text_box">
  <div class="cont_text inner_modal_title">
   <div class="cont_text_inner cont_text_title"><b>디카페인 아메리카노</b></div>
   <div class="cont_text_inner cont_text_info">Decaf Americano</div></div>
 </div><div class="cont_list"><ul><li>당류 0g</li><li>카페인 11.4mg</li></ul></div></div>
</li>
<li><a class="inner_modal_open"></a>
 <div class="inner_modal"><div class="cont_text_box">
  <div class="cont_text inner_modal_title">
   <div class="cont_text_inner cont_text_title"><b>레몬에이드</b></div>
   <div class="cont_text_inner cont_text_info">Lemon Ade</div></div>
 </div><div class="cont_list"><ul><li>당류 32g</li><li>카페인 0mg</li></ul></div></div>
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
    # W0000004 (블렌디드 커피) also carries non-coffee cream frappuccinos with 0mg caffeine, e.g. the real
    # "화이트 타이거 프라푸치노": menu_is_decaf's <=15mg rule must not brand these "decaf" (they were never
    # coffee to begin with), so starbucks stays on name-only detect_decaf for is_decaf.
    (tmp_path / "W0000004.json").write_text(json.dumps({"list": [
        {"product_CD": "3", "product_NM": "화이트 타이거 프라푸치노", "cate_NAME": "블렌디드 커피", "caffeine": "0"},
    ]}, ensure_ascii=False), encoding="utf-8")
    items = normalize_starbucks(tmp_path, "2026-09-24").menu_items
    assert [(i.key, i.caffeine_mg, i.is_decaf, i.decaf_option) for i in items] == [
        ("menu:starbucks:1", 150.0, False, True), ("menu:starbucks:2", None, True, True),
        ("menu:starbucks:3", 0.0, False, False)]
    assert items[0].name_en is None


def test_mega(tmp_path):
    (tmp_path / "page_1.html").write_text(MEGA_HTML, encoding="utf-8")
    items = normalize_mega(tmp_path, "2026-09-24").menu_items
    by = {i.name: i for i in items}
    assert (by["디카페인 아메리카노"].name_en, by["디카페인 아메리카노"].caffeine_mg, by["디카페인 아메리카노"].is_decaf) == ("Decaf Americano", 11.4, True)
    # mega's single collected URL (menu_category1=1&menu_category2=1) also lists non-coffee drinks (ades,
    # smoothies, teas) with 0mg caffeine, e.g. the real "레몬에이드": the <=15mg rule must not brand these
    # "decaf" either, so mega also stays on name-only detect_decaf for is_decaf.
    assert by["레몬에이드"].is_decaf is False


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
    assert len(items) == 5       # H-/I-아메리카노 and H-/I-디카페인 아메리카노 each merge into one
    assert by["아메리카노"].caffeine_mg == 185.81      # larger of HOT (150.00) / ICED (185.81)
    assert by["디카페인 아메리카노"].is_decaf and not by["디카페인 아메리카노"].decaf_option
    assert by["아메리카노"].decaf_option is True        # 커피ㆍ콜드브루 is compose's decaf-shot category
    assert by["쫀득카노"].caffeine_mg == 85.0            # no H-/I- prefix: kept as-is, not merged away
    assert by["빅포즈 아메리카노"].caffeine_mg == 371.62  # ICED-only size: no HOT counterpart to merge with
    # not named decaf, but 9.16mg caffeine is <= the 15mg menu_is_decaf threshold
    assert by["올데이 오트"].caffeine_mg == 9.16 and by["올데이 오트"].is_decaf
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


def _sca_tags() -> set[str]:
    """Every node name of the SCA wheel taxonomy (data/curated/sca_ko.yaml keys are 'a>b>c' paths)."""
    from pipeline import settings
    paths = yaml.safe_load((settings.CURATED_DIR / "sca_ko.yaml").read_text(encoding="utf-8"))
    return {node for path in paths for node in path.split(">")}


def test_every_brand_has_bean_profiles():
    from pipeline import settings
    sca = _sca_tags()
    brands = normalize_brands(settings.CURATED_DIR)
    assert len(brands) == 10
    for b in brands:
        assert b.bean is not None, b.key
        assert set(b.bean.flavor_tags) <= sca, b.key
        if b.decaf_available:
            assert b.decaf_bean is not None, b.key
            assert set(b.decaf_bean.flavor_tags) <= sca, b.key


def test_every_brand_bean_value_has_a_label_source_and_official_ones_have_a_note():
    """ADR 0012: each of acidity/body/sweetness/flavor_tags says where it came from; a value derived from the
    brand's official description always carries that description's card line."""
    from pipeline import settings
    for b in normalize_brands(settings.CURATED_DIR):
        for bean in (b.bean, b.decaf_bean):
            if bean is None:
                continue
            assert set(bean.label_source) == {"acidity", "body", "sweetness", "flavor_tags"}, b.key
            if set(bean.label_source.values()) != {"estimate"}:
                assert bean.official_note, b.key


def test_brand_profiles_match_the_derivation_output():
    """brands.yaml is hand-edited from data/eval/brand_beans_derived.json (comments survive); they must agree."""
    import json

    from pipeline import settings
    derived = {r["brand"]: r for r in json.loads((settings.EVAL_DIR / "brand_beans_derived.json")
                                                 .read_text(encoding="utf-8"))}
    for b in normalize_brands(settings.CURATED_DIR):
        for slot, bean in (("house", b.bean), ("decaf", b.decaf_bean)):
            if bean is None:
                continue
            d = derived[b.key][slot]
            assert {"acidity": bean.acidity, "body": bean.body, "sweetness": bean.sweetness,
                    "flavor_tags": bean.flavor_tags} == d["values"], (b.key, slot)
            assert bean.label_source == d["label_source"], (b.key, slot)


OPEN_LABEL_SOURCES = {"official_gauge", "official_cue", "open_feature_model", "estimate"}


def test_open_brand_profiles_are_licence_clean():
    """ADR 0012 오픈판: every brand has an open profile per bean, and no open value comes from the full-variant
    learned models (official_notes_model: attr/tag models trained on coffeereview labels, ADR 0008/0009)."""
    from pipeline import settings
    sca = _sca_tags()
    for b in normalize_brands(settings.CURATED_DIR):
        for full, open_ in ((b.bean, b.bean_open), (b.decaf_bean, b.decaf_bean_open)):
            assert (full is None) == (open_ is None), b.key
            if open_ is None:
                continue
            assert set(open_.label_source) == {"acidity", "body", "sweetness", "flavor_tags"}, b.key
            assert set(open_.label_source.values()) <= OPEN_LABEL_SOURCES, (b.key, open_.label_source)
            assert "official_notes_model" not in open_.label_source.values(), b.key
            assert set(open_.flavor_tags) <= sca, b.key
            # the feature model ships acidity/sweetness only; body is a cue or the hand estimate
            assert open_.label_source["body"] != "open_feature_model", b.key
            if set(open_.label_source.values()) != {"estimate"}:
                assert open_.official_note, b.key


def test_open_brand_profiles_match_the_open_derivation_output():
    """brands.yaml bean_open/decaf_bean_open come from scripts/derive_brand_beans.py --variant open."""
    import json

    from pipeline import settings
    derived = {r["brand"]: r for r in json.loads((settings.EVAL_DIR / "brand_beans_derived_open.json")
                                                 .read_text(encoding="utf-8"))}
    for b in normalize_brands(settings.CURATED_DIR):
        for slot, bean in (("house", b.bean_open), ("decaf", b.decaf_bean_open)):
            if bean is None:
                continue
            d = derived[b.key][slot]
            assert {"acidity": bean.acidity, "body": bean.body, "sweetness": bean.sweetness,
                    "flavor_tags": bean.flavor_tags} == d["values"], (b.key, slot)
            assert bean.label_source == d["label_source"], (b.key, slot)


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


def test_run_normalize_excludes_sources(tmp_path):
    raw = tmp_path / "raw"
    cr = raw / "coffeereview_kaggle" / "2026-09-24"
    (cr / "patkle__x").mkdir(parents=True)
    (cr / "manifest.json").write_text('{"ok": true}', encoding="utf-8")
    (cr / "patkle__x" / "reviews_feb_2023.csv").write_text(
        "title,rating,acidity_structure,aftertaste,aroma,body,flavor,with_milk,agtron,blind_assessment,"
        "bottom_line,coffee_origin,est_price,notes,review_date,roast_level,roaster,roaster_location,url\n"
        'Bolivia Gesha,93,9,8,9,8,9,,60/78,"Floral. Magnolia, cocoa nib.","Great.","Caranavi, Bolivia",$30,'
        '"Washed process.",January 2023,Medium-Light,Red Rooster,Floyd,https://cr.test/review/a/\n',
        encoding="utf-8")
    cqi = raw / "cqi" / "2026-09-24"
    cqi.mkdir(parents=True)
    (cqi / "manifest.json").write_text('{"ok": true}', encoding="utf-8")
    (cqi / "arabica_2018.csv").write_text(
        "Unnamed: 0,Species,Owner,Country.of.Origin,Farm.Name,Company,Region,Variety,Processing.Method,"
        "Acidity,Body,Sweetness\n"
        "1,Arabica,metad,Ethiopia,metad plc,metad co,guji,,Washed / Wet,8.75,8.5,10\n",
        encoding="utf-8")
    curated = tmp_path / "curated"
    curated.mkdir()
    (curated / "brands.yaml").write_text("[]\n", encoding="utf-8")

    out = tmp_path / "norm"
    stats = run_normalize(raw, out, curated, exclude_sources=("coffeereview_kaggle",))
    assert stats["src:coffeereview_kaggle"] == "excluded"
    coffees = read_jsonl(out / "coffees.jsonl", CoffeeRecord)
    assert coffees and not any(c.source == "coffeereview_kaggle" for c in coffees)
    assert not any(r.source == "coffeereview_kaggle" for r in read_jsonl(out / "reviews.jsonl", ReviewRecord))


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


def test_menu_is_decaf_by_caffeine_threshold():
    """A coffee-category drink can be decaf without the word appearing in its name (컴포즈 「올데이 오트」
    9.16 mg): caffeine_mg <= 15 counts as decaf too, on top of the name-word check."""
    from pipeline.normalize.menus import menu_is_decaf
    assert menu_is_decaf("아메리카노", 182.0) is False
    assert menu_is_decaf("디카페인 아메리카노", 182.0) is True    # named decaf regardless of caffeine
    assert menu_is_decaf("올데이 오트", 9.16) is True             # not named decaf, but caffeine <= 15mg
    assert menu_is_decaf("올데이 오트", 15.0) is True             # boundary: <= 15 counts
    assert menu_is_decaf("올데이 오트", 15.01) is False
    assert menu_is_decaf("올데이 오트", None) is False            # no caffeine reading: can't tell from mg


def test_menu_decaf_option_no_shot_words():
    """A decaf espresso shot doesn't make these drinks decaf: they either have no espresso shot to swap
    (말차/큐브/믹스커피 are not espresso-based) or are already a fixed daily-brew blend (데일리커피)."""
    from pipeline.normalize.menus import menu_decaf_option
    from pipeline.records import BrandRecord
    b = BrandRecord(key="brand:x", name="x", decaf_available=True, verified_at="2026-09-27",
                    decaf_option_categories=["에스프레소"])
    for word in ("말차", "큐브", "믹스커피", "데일리커피"):
        assert menu_decaf_option(b, "에스프레소", False, name=word) is False
        assert menu_decaf_option(b, "에스프레소", False, name=f"{word} 라떼") is False
        assert menu_decaf_option(b, "에스프레소", False, name=f"아이스 {word}") is False


def test_brands_yaml_has_decaf_option_categories_for_menu_brands():
    from pipeline.normalize.menus import brands_by_key
    from pipeline import settings
    b = brands_by_key(settings.CURATED_DIR)
    for k in ("brand:hollys", "brand:coffeebean", "brand:ediya", "brand:paulbassett", "brand:compose"):
        assert k in b and isinstance(b[k].decaf_option_categories, list)


def test_menu_normalizers_use_strict_brand_lookup(tmp_path, monkeypatch):
    """brands.yaml always has an entry for every menu brand (guarded by
    test_brands_yaml_has_decaf_option_categories_for_menu_brands above), so all four menu-brand
    normalizers look their brand up the same way, brands_by_key(...)[key] (raises loudly if it's
    ever missing) rather than .get(...) with a silent decaf_option=False fallback."""
    import pipeline.normalize.menus as menus

    monkeypatch.setattr(menus, "brands_by_key", lambda curated_dir: {})

    with pytest.raises(KeyError):
        menus.normalize_hollys(_copy_fixture("menus/hollys", tmp_path), "2026-09-27")
    with pytest.raises(KeyError):
        menus.normalize_coffeebean(_copy_fixture("menus/coffeebean", tmp_path), "2026-09-27")
    with pytest.raises(KeyError):
        menus.normalize_compose(_copy_fixture("menus/compose", tmp_path), "2026-09-27")
    with pytest.raises(KeyError):
        menus.normalize_paulbassett(_copy_fixture("menus/paulbassett", tmp_path), "2026-09-27")


def test_hollys_parses_names_caffeine_decaf(tmp_path):
    snap = _copy_fixture("menus/hollys", tmp_path)
    items = normalize_hollys(snap, "2026-09-27").menu_items
    by = {i.name: i for i in items}
    assert len(items) == 5   # 아메리카노, 카페 라떼, 디카페인 콜드브루, 에스프레소, 콜드브루 (HOT/ICED merged into one each)
    assert by["아메리카노"].caffeine_mg == 114.0
    assert by["카페 라떼"].caffeine_mg == 127.0
    assert by["디카페인 콜드브루"].is_decaf and not by["디카페인 콜드브루"].decaf_option
    # 2026-09-27 공식 페이지 재확인: 할리스의 디카페인은 콜드브루 계열 전용 SKU뿐이고(디카페인 콜드브루/라떼/
    # 아샷추), 아메리카노 등 에스프레소(HOT) 음료에는 디카페인 표기·옵션이 전혀 없다(brands.yaml 참고) — 즉
    # 에스프레소 샷을 디카페인으로 바꿔주는 옵션은 없으므로 decaf_option_categories == [] 이고, 아메리카노도
    # decaf_option=False 이어야 한다.
    assert by["아메리카노"].decaf_option is False
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
    assert len(items) == 7
    # coffeebeankorea.com writes HOT/ICED as different names ("아메리카노" vs "아이스 아메리카노"),
    # never a HOT/ICED suffix on the same name, so they are kept as separate items (not merged).
    assert by["아메리카노"].caffeine_mg == 182.0
    # Regression for the unclosed <div> before </li>: lxml nests each following <li> inside the current
    # one, so a naive `li.select("div.info dl")` walks into every later item's div.info too, and (since
    # the loop never breaks) ends up keeping the LAST matched dl — i.e. every non-last item in a page
    # wrongly reports the page's very last item's caffeine. cat13_page1.html chains three items (아이스
    # 아메리카노, 아이스 헤이즐넛 라떼, 카페수아) with three different caffeine values so both non-last
    # items are wrong (and provably different from their own correct value) under the bug.
    assert by["아이스 아메리카노"].caffeine_mg == 91.0        # would read 182 (카페수아's, the page's last item) if buggy
    assert by["아이스 헤이즐넛 라떼"].caffeine_mg == 130.0    # would also read 182 (카페수아's) if buggy
    assert by["카페수아"].caffeine_mg == 182.0
    assert by["카페라떼"].caffeine_mg == 91.0                 # would read 182 (아메리카노's, the page's last item) if buggy
    assert by["드립커피"].caffeine_mg == 148.0                # would read 190 (콜드브루's, the page's last item) if buggy
    # The site's coffee menu has no item literally named decaf: decaf is a paid shot-swap option on
    # espresso drinks, never a distinct product, so is_decaf is False for every scraped item.
    assert all(not i.is_decaf for i in items)
    brand = brands_by_key(settings.CURATED_DIR)["brand:coffeebean"]
    assert menu_decaf_option(brand, by["아메리카노"].category, by["아메리카노"].is_decaf) is True
    assert menu_decaf_option(brand, by["콜드브루"].category, by["콜드브루"].is_decaf) is False  # brewed coffee, no shot to swap
    assert all(i.brand_key == "brand:coffeebean" and i.key.startswith("menu:coffeebean:") for i in items)
    assert sum(i.caffeine_mg is not None for i in items) / len(items) >= 0.9


def test_ediya_parses_cards_size_temp_caffeine_cup():
    from pipeline.normalize.menus import parse_ediya_cards
    rows = parse_ediya_cards((FIXTURES / "menus/ediya/cat155_q00.html").read_text(encoding="utf-8"))
    first = rows[0]
    assert (first["size"], first["temp"], first["name"], first["caffeine_mg"], first["cup_ml"]) == (
        "L", "HOT", "카페 아메리카노", 7.0, 520)
    assert rows[1]["size"] == "EX" and rows[1]["cup_ml"] == 650
    assert {(r["size"], r["temp"], r["name"]) for r in rows} >= {("L", None, "콜드브루"), (None, "HOT", "에스프레소")}


def test_ediya_merges_cards_keeps_coffee_and_decaf_skus(tmp_path):
    from pipeline.normalize.menus import normalize_ediya
    items = normalize_ediya(_copy_fixture("menus/ediya", tmp_path), "2026-09-28").menu_items
    by = {i.key: i for i in items}
    am = by["menu:ediya:카페 아메리카노"]
    # one item per drink: the standard (L) cup's caffeine, not the EX cup (303 mg)
    assert (am.category, am.caffeine_mg, am.is_decaf, am.decaf_option) == ("COFFEE", 202.0, False, True)
    dam = by["menu:ediya:decaf:카페 아메리카노"]
    assert (dam.name, dam.category, dam.caffeine_mg, dam.is_decaf, dam.decaf_option) == (
        "디카페인 카페 아메리카노", "DECAF", 7.0, True, False)
    assert by["menu:ediya:decaf:에스프레소"].is_decaf
    # cold brew: no shot to swap, the decaf cold brew is its own SKU
    assert by["menu:ediya:콜드브루"].decaf_option is False and by["menu:ediya:decaf:콜드브루"].is_decaf
    # the DECAF tab's tea-with-a-decaf-shot drink is not a coffee drink
    assert not any("아샷추" in i.name for i in items)
    # a "decaf bean" card that still measures 163 mg is never served to a decaf-only guest, nor is its regular twin
    for k in ("menu:ediya:얼박샷추(디카페인 원두)", "menu:ediya:얼박샷추"):
        assert (by[k].is_decaf, by[k].decaf_option) == (False, False)
    assert all(i.brand_key == "brand:ediya" and i.caffeine_mg is not None for i in items)
