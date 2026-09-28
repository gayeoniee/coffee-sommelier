import json
from pathlib import Path

import pytest

from pipeline.collect.roasters_kr import (
    AnthraciteCollector,
    BeanBrothersCollector,
    BeanFactRecord,
    BlueBottleCollector,
    FeltCollector,
    ManufactCollector,
    MomosCollector,
    fetch_cached,
    find_country,
    find_decaf_method,
    find_process,
    find_roast_from_brackets,
    flavor_notes_from_ld_description,
    is_decaf,
    ko_prefix,
    parse_anthracite_product,
    parse_beanbrothers_product,
    parse_bluebottle_product,
    parse_felt_product,
    parse_fritz_product,
    parse_libre_product,
    parse_manufact_product,
    parse_momos_product,
    parse_namusairo_product,
    parse_onekg_product,
    parse_price_krw,
    parse_weight_g,
    run_roasters_kr_collect,
    split_note_list,
)
from pipeline.collect.roasters_kr import (
    ALL_ROASTER_COLLECTORS,
    dot_gauges,
    normalize_gauge,
    number_gauges,
    parse_altitude_m,
    parse_groasting_product,
    parse_naeil_product,
)
from pipeline.http import RobotsDisallowed

FIXTURES = Path(__file__).parent / "fixtures" / "roasters_kr"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Small helper unit tests
# --------------------------------------------------------------------------- #


def test_ko_prefix_stops_at_latin_text():
    assert ko_prefix("콜롬비아 Colombia") == "콜롬비아"
    assert ko_prefix("게뎁 첼베사, Gedeb Chelbesa") == "게뎁 첼베사"


def test_ko_prefix_falls_back_when_no_leading_korean():
    assert ko_prefix("Coffee Producers from Huila") == "Coffee Producers from Huila"


def test_find_country_picks_first_known_name():
    assert find_country("니카라과 CoE 9위 핀카 리브레 에티오피아") == "니카라과"
    assert find_country("아무 상관 없는 문구") is None


def test_find_process_prefers_compound_word():
    assert find_process("무산소 내추럴 Anaerobic Natural") == "무산소 내추럴"
    assert find_process("워시드 디카페인 Washed") == "워시드"
    assert find_process("디카페인 Decaffeinated") is None


def test_find_roast_from_brackets():
    assert find_roast_from_brackets("[골드문트] [디카페인] 에티오피아 [약배전]") == "약배전"
    assert find_roast_from_brackets("[싱글오리진] 온두라스") is None


def test_is_decaf_matches_ko_and_en():
    assert is_decaf("디카페인 콜롬비아")
    assert is_decaf("디카프리오")
    assert is_decaf(None, "Decaf Origin Select")
    assert not is_decaf("워시드 콜롬비아")


def test_find_decaf_method():
    assert find_decaf_method("MWP Decaf Origin Select") == "Mountain Water Process"
    assert find_decaf_method("콜롬비아 슈가케인 E.A 공정") == "Sugarcane Process"
    assert find_decaf_method("스위스 워터 공정") == "Swiss Water Process"
    assert find_decaf_method("그냥 원두") is None


def test_flavor_notes_from_ld_description_splits_korean_line():
    desc = "Flavor Notes : 레몬 그라스, 청사과, 레몬 제스트\r\nLemongrass, Green apple, Lemon zest"
    assert flavor_notes_from_ld_description(desc) == ["레몬 그라스", "청사과", "레몬 제스트"]


def test_flavor_notes_from_ld_description_rejects_roast_only():
    assert flavor_notes_from_ld_description("중강배전") == []
    assert flavor_notes_from_ld_description("") == []


def test_parse_price_krw():
    assert parse_price_krw("₩16,000") == 16000
    assert parse_price_krw("9,800 원") == 9800
    assert parse_price_krw("") is None


def test_parse_weight_g():
    assert parse_weight_g("200g") == 200
    assert parse_weight_g("1kg 대용량") == 1000
    assert parse_weight_g("옵션 없음") is None


# --------------------------------------------------------------------------- #
# Fritz
# --------------------------------------------------------------------------- #


def test_parse_fritz_single_origin():
    facts = parse_fritz_product(read_fixture("fritz_product.html"), "https://www.fritz.co.kr/product/x/2195/")
    assert facts["name"] == "[프릳츠] Gedeb Chelbesa Washed"
    assert facts["origin_country"] == "에티오피아"
    assert facts["origin_region"] == "게뎁 첼베사"
    assert facts["origin_farm"] == "첼베사 인근 소농들"
    assert facts["process"] == "워시드"
    assert facts["is_decaf"] is False
    assert facts["flavor_notes"] == ["레몬 그라스", "청사과", "레몬 제스트"]
    assert facts["price_krw"] == 22000


def test_parse_fritz_decaf_has_no_real_process_but_flags_decaf():
    facts = parse_fritz_product(read_fixture("fritz_decaf.html"), "https://www.fritz.co.kr/product/x/982/")
    assert facts["is_decaf"] is True
    assert facts["process"] is None  # Fritz puts "디카페인 Decaffeinated" in the process field, not a real process
    assert facts["origin_country"] == "콜롬비아"
    assert facts["origin_farm"] == "Coffee Producers from Huila"
    assert facts["weight_g"] == 200
    assert facts["flavor_notes"] == ["초콜릿", "건무화과", "호두"]


def test_parse_fritz_excludes_non_bean_products():
    excluded_html = read_fixture("fritz_product.html").replace(
        '"name":"[프릳츠] Gedeb Chelbesa Washed"', '"name":"[프릳츠] 캡슐커피 디카페인 증정"')
    assert parse_fritz_product(excluded_html, "https://x/1") is None


# --------------------------------------------------------------------------- #
# Namusairo
# --------------------------------------------------------------------------- #


def test_parse_namusairo_product():
    facts = parse_namusairo_product(read_fixture("namusairo_product.html"), "https://namusairo.com/product/x/828/")
    assert facts["name"] == "디카프리오"
    assert facts["origin_country"] == "Mexico"
    assert facts["origin_region"] == "베라크루스, 오아하카"
    assert facts["origin_farm"] == "데스카맥스"
    assert facts["process"] == "워시드"
    assert facts["roast_level"] == "Well-done"
    assert facts["is_decaf"] is True
    assert facts["decaf_process"] == "Mountain Water Process"
    assert facts["flavor_notes"] == ["카카오", "카라멜", "꿀"]
    assert facts["price_krw"] == 16000
    assert facts["weight_g"] == 200


# --------------------------------------------------------------------------- #
# Coffee Libre
# --------------------------------------------------------------------------- #


def test_parse_libre_single_origin():
    facts = parse_libre_product(read_fixture("libre_product.html"), "https://coffeelibre.kr/product/x/8153/")
    assert facts["name"] == "[싱글오리진] 온두라스 라스 나랑하스 파라이네마"
    assert facts["origin_country"] == "온두라스"
    assert facts["origin_farm"] == "라스 나랑하스"
    assert facts["origin_region"] == "엘 파라이소, 트로헤스"
    assert facts["process"] == "워시드"
    assert facts["roast_level"] is None
    assert facts["flavor_notes"] == ["플로럴", "복숭아", "오렌지", "자두"]
    assert facts["weight_g"] == 200
    assert facts["price_krw"] == 17000


def test_parse_libre_decaf_with_roast_bracket():
    facts = parse_libre_product(read_fixture("libre_decaf_roast.html"), "https://coffeelibre.kr/product/x/9001/")
    assert facts["is_decaf"] is True
    assert facts["roast_level"] == "약배전"
    assert facts["origin_country"] == "에티오피아"
    assert facts["process"] == "워시드"
    assert facts["flavor_notes"] == ["베리", "자스민", "꿀"]


def test_parse_libre_bulk_blend_has_no_notes_from_roast_only_description():
    facts = parse_libre_product(read_fixture("libre_bulk_blend.html"), "https://coffeelibre.kr/product/x/9002/")
    assert facts["flavor_notes"] == []  # description is just "중강배전" (a roast word), not flavor notes
    assert facts["origin_country"] is None  # blend, no single origin
    assert facts["weight_g"] == 1000


# --------------------------------------------------------------------------- #
# 1kg Coffee
# --------------------------------------------------------------------------- #


def test_parse_onekg_product():
    facts = parse_onekg_product(read_fixture("onekg_product.html"),
                                 "https://www.1kgcoffee.co.kr/goods/goods_view.php?goodsNo=1000000688")
    assert facts["name"] == "싱글오리진 디카페인 콜롬비아 슈가케인 원두"
    assert facts["origin_country"] == "콜롬비아"
    assert facts["roast_level"] == "시티(중간볶음)"
    assert facts["is_decaf"] is True
    assert facts["decaf_process"] == "Sugarcane Process"
    assert facts["process"] is None  # only in marketing prose on this site -- not scraped
    assert facts["flavor_notes"] == []  # ditto
    assert facts["price_krw"] == 9800
    assert facts["weight_g"] == 100


# --------------------------------------------------------------------------- #
# Blue Bottle Korea (Shopify)
# --------------------------------------------------------------------------- #


def _bb_products():
    return json.loads(read_fixture("bluebottle_products.json"))["products"]


def test_parse_bluebottle_single_origin():
    facts = parse_bluebottle_product(_bb_products()[0], "https://kr.bluebottlecoffee.com")
    assert facts["name"] == "르완다 냐마셰케 냐부메라 내추럴"
    assert facts["origin_country"] == "르완다"
    assert facts["process"] == "내추럴"
    assert facts["is_decaf"] is False
    assert set(facts["flavor_notes"]) <= {"딸기", "플로럴", "장미"} and facts["flavor_notes"]
    assert facts["price_krw"] == 29500
    assert facts["weight_g"] == 250
    assert facts["product_url"] == "https://kr.bluebottlecoffee.com/products/rwanda-nyamasheke-nyabumera-natural"


def test_parse_bluebottle_decaf_regex_extracts_method_from_prose():
    facts = parse_bluebottle_product(_bb_products()[1], "https://kr.bluebottlecoffee.com")
    assert facts["is_decaf"] is True
    assert facts["decaf_process"] == "Swiss Water Process"


def test_parse_bluebottle_skips_non_bean_product_type():
    assert parse_bluebottle_product(_bb_products()[2], "https://kr.bluebottlecoffee.com") is None


def test_parse_bluebottle_skips_gift_set():
    assert parse_bluebottle_product(_bb_products()[3], "https://kr.bluebottlecoffee.com") is None


def test_bluebottle_collector_paginates_and_dedupes(tmp_path):
    pages = {1: _bb_products(), 2: []}

    class FakeResp:
        def __init__(self, text):
            self.text = text

    class FakeHttp:
        def get(self, url, params=None):
            page = int(url.split("page=")[1])
            return FakeResp(json.dumps({"products": pages.get(page, [])}))

    c = BlueBottleCollector()
    records = c.collect(FakeHttp(), tmp_path, "2026-09-26")
    assert {r.site for r in records} == {"bluebottle"}
    assert len(records) == 2  # the 텀블러 and 세트 products are filtered out
    assert (tmp_path / "products_page1.json").exists()


def test_split_note_list_handles_ko_en_and_alt_dot():
    assert split_note_list("건포도 · 허브 · 마카다미아") == ["건포도", "허브", "마카다미아"]
    assert split_note_list("밀크초콜릿ㆍ콘 시럽ㆍ땅콩") == ["밀크초콜릿", "콘 시럽", "땅콩"]
    assert split_note_list("Floral, Raspberry, Rosemary") == ["Floral", "Raspberry", "Rosemary"]
    assert split_note_list(None) == []
    assert split_note_list("") == []


# --------------------------------------------------------------------------- #
# Anthracite
# --------------------------------------------------------------------------- #


def test_parse_anthracite_single_origin():
    facts = parse_anthracite_product(read_fixture("anthracite_product.html"),
                                      "https://anthracitecoffee.com/shop_view/?idx=193")
    assert facts["origin_country"] == "콜롬비아"
    assert facts["origin_region"] == "Piendamo, Cauca"
    assert facts["process"] == "Double Anaerobic Fermentation/Thermel Shock"
    assert facts["roast_level"] == "Medium Light"
    assert facts["is_decaf"] is False
    assert facts["flavor_notes"] == []  # no "노트" row on this product
    assert facts["price_krw"] == 30000
    assert facts["weight_g"] == 200


def test_parse_anthracite_decaf_has_notes_and_method():
    facts = parse_anthracite_product(read_fixture("anthracite_decaf.html"),
                                      "https://anthracitecoffee.com/shop_view/?idx=135")
    assert facts["is_decaf"] is True
    assert facts["decaf_process"] == "Mountain Water Process"
    assert facts["origin_country"] == "에티오피아"
    assert facts["flavor_notes"] == ["Pumpkin Yeot", "Green Tangerine", "Maplesyrup", "Long Aftertaste"]


def test_parse_anthracite_blend_prefers_flavor_list_over_prose_note_tagline():
    # This site reuses one "노트" label for two very different things: a
    # clean word list on single origins, and a marketing tagline sentence on
    # blends (which also embeds the bare word "노트" earlier in a sentence,
    # e.g. "견과류의 노트와 단맛이 ..." -- a trap for naive label scanning).
    facts = parse_anthracite_product(read_fixture("anthracite_blend.html"),
                                      "https://anthracitecoffee.com/shop_view/?idx=71")
    assert facts["flavor_notes"] == ["볶은 견과", "스파이시", "다크 초콜렛"]
    assert facts["roast_level"] == "미디움 다크 Medium Dark"
    assert facts["origin_country"] is None  # blend, no single origin


def test_parse_anthracite_excludes_non_bean_products():
    excluded_html = read_fixture("anthracite_product.html").replace(
        '"name":"콜롬비아 엘 파라이소 더블 무산소 리치피치 Colombia El paraiso Castillo Double Anaerobic Fermentation - Lychee Peach"',
        '"name":"앤트러사이트 원두 선물세트(3종)"')
    assert parse_anthracite_product(excluded_html, "https://x/1") is None


# --------------------------------------------------------------------------- #
# Felt
# --------------------------------------------------------------------------- #


def test_parse_felt_single_origin():
    facts = parse_felt_product(read_fixture("felt_product.html"), "https://feltcoffee.com/product/x/488/")
    assert facts["name"] == "온두라스 쿠쿠루초 파카마라 워시드"
    assert facts["origin_country"] == "온두라스"
    assert facts["process"] == "워시드"
    assert facts["roast_level"] == "MEDIUM LIGHT"
    assert facts["is_decaf"] is False
    assert facts["flavor_notes"] == ["Floral", "Raspberry", "Rosemary"]
    assert facts["price_krw"] == 25000
    assert facts["weight_g"] == 200


def test_parse_felt_decaf_extracts_method_from_coffee_line():
    facts = parse_felt_product(read_fixture("felt_decaf.html"), "https://feltcoffee.com/product/x/513/")
    assert facts["is_decaf"] is True
    assert facts["decaf_process"] == "Mountain Water Process"
    assert facts["process"] == "허니"
    assert facts["flavor_notes"] == ["Peach", "Sweet Potato", "Molasess", "Silky"]


def test_parse_felt_excludes_non_bean_products():
    excluded_html = read_fixture("felt_product.html").replace(
        "온두라스 쿠쿠루초 파카마라 워시드", "펠트 드립백")
    assert parse_felt_product(excluded_html, "https://x/1") is None


# --------------------------------------------------------------------------- #
# Bean Brothers
# --------------------------------------------------------------------------- #


def test_parse_beanbrothers_single_origin():
    facts = parse_beanbrothers_product(read_fixture("beanbrothers_product.html"),
                                        "https://beanbrothers.co.kr/goods/goods_view.php?goodsNo=1000001418")
    assert facts["name"] == "에티오피아 반코 타라투 워시드"
    assert facts["origin_country"] == "에티오피아"
    assert facts["origin_region"] == "반코 타라투(Banqo Taratu), 게뎁, 예가체프"
    assert facts["origin_farm"] == "타라투"
    assert facts["process"] == "워시드"
    assert facts["roast_level"] == "라이트"
    assert facts["flavor_notes"] == ["모란", "신비 복숭아", "얼그레이", "생기 있는"]
    assert facts["price_krw"] == 21000
    assert facts["weight_g"] == 200


def test_parse_beanbrothers_decaf_picks_ea_process_from_korean_phrase():
    facts = parse_beanbrothers_product(read_fixture("beanbrothers_decaf.html"),
                                        "https://beanbrothers.co.kr/goods/goods_view.php?goodsNo=1000001504")
    assert facts["is_decaf"] is True
    assert facts["decaf_process"] == "EA Process"  # "천연 에틸 아세테이트(사탕수수) 프로세스"
    assert facts["origin_country"] == "콜롬비아"  # only in the "지역" fact-sheet row, not the name


def test_parse_beanbrothers_blend_has_no_origin_but_keeps_roast():
    facts = parse_beanbrothers_product(read_fixture("beanbrothers_blend.html"),
                                        "https://beanbrothers.co.kr/goods/goods_view.php?goodsNo=1000000025")
    assert facts["origin_country"] is None  # blend of two Ethiopia lots, no single origin
    assert facts["roast_level"] == "Medium Light"
    assert facts["flavor_notes"] == []  # no "테이스팅 노트" row on a blend


def test_parse_beanbrothers_excludes_gift_bundle():
    excluded_html = read_fixture("beanbrothers_product.html").replace(
        "에티오피아 반코 타라투 워시드", "Bb 샘플 세트")
    assert parse_beanbrothers_product(excluded_html, "https://x/1") is None


# --------------------------------------------------------------------------- #
# Momos
# --------------------------------------------------------------------------- #


def test_parse_momos_single_origin():
    facts = parse_momos_product(read_fixture("momos_product.html"), "https://momos.co.kr/shop_view/?idx=7375")
    assert facts["name"] == "원두 에티오피아 사포 모스토 무산소 내추럴"
    assert facts["origin_country"] == "에티오피아"
    assert facts["process"] == "무산소 내추럴"
    assert facts["is_decaf"] is False
    assert facts["flavor_notes"] == ["허니듀", "포도", "마시멜로우", "바닐라 캔디", "정제된"]
    assert facts["price_krw"] == 17000
    assert facts["weight_g"] == 100


def test_parse_momos_decaf():
    facts = parse_momos_product(read_fixture("momos_decaf.html"), "https://momos.co.kr/shop_view/?idx=9001")
    assert facts["is_decaf"] is True
    assert facts["decaf_process"] == "Swiss Water Process"
    assert facts["flavor_notes"] == ["초콜릿", "캐러멜"]


def test_parse_momos_blend_prefers_summary_card_over_prose_disclosure_note():
    # The legal disclosure table's "노트" row is rewritten into a marketing
    # sentence for some blends; the short "향미노트" summary card above it
    # keeps the clean word list and should win.
    facts = parse_momos_product(read_fixture("momos_blend.html"), "https://momos.co.kr/shop_view/?idx=2486")
    assert facts["flavor_notes"] == ["다크 초콜릿", "묵직한", "깨끗한", "크림같은"]


def test_parse_momos_excludes_non_bean_products():
    excluded_html = read_fixture("momos_product.html").replace(
        "원두 에티오피아 사포 모스토 무산소 내추럴", "보자기 드립백 버라이어티 15개입")
    assert parse_momos_product(excluded_html, "https://x/1") is None


# --------------------------------------------------------------------------- #
# Manufact
# --------------------------------------------------------------------------- #


def test_parse_manufact_single_origin_splits_bean_info_lines():
    facts = parse_manufact_product(read_fixture("manufact_product.html"),
                                    "https://manufactcoffee.com/product/x/426/")
    assert facts["name"] == "Guatemala Waykan | 과테말라 와이칸"
    assert facts["origin_country"] == "과테말라"
    assert facts["origin_region"] == "우에우에테낭고"
    assert facts["roast_level"] == "미디엄다크"
    assert facts["flavor_notes"] == ["건포도", "허브", "마카다미아", "사탕수수", "초콜렛"]
    assert facts["process"] is None  # folded into the free-form cell, not its own row
    assert facts["price_krw"] == 18000


def test_parse_manufact_decaf_blend_has_two_line_bean_info():
    facts = parse_manufact_product(read_fixture("manufact_decaf.html"),
                                    "https://manufactcoffee.com/product/x/106/")
    assert facts["is_decaf"] is True
    assert facts["origin_country"] is None  # blend, no origin line
    assert facts["roast_level"] == "미디엄다크"
    assert facts["flavor_notes"] == ["밀크초콜릿", "콘 시럽", "땅콩", "복숭아", "오트밀"]


def test_parse_manufact_bare_weight_line_does_not_leak_into_notes():
    facts = parse_manufact_product(read_fixture("manufact_product_with_weight_line.html"),
                                    "https://manufactcoffee.com/product/x/439/")
    assert facts["weight_g"] == 100
    assert facts["flavor_notes"] == ["베르가못", "라임", "밀크티"]
    assert facts["origin_region"] == "후일라 팔레스티나"


def test_parse_manufact_excludes_non_bean_products():
    excluded_html = read_fixture("manufact_product.html").replace(
        "Guatemala Waykan | 과테말라 와이칸", "머신용품 청소 세트")
    assert parse_manufact_product(excluded_html, "https://x/1") is None


# --------------------------------------------------------------------------- #
# New-site collectors: listing regex + caching wiring
# --------------------------------------------------------------------------- #


def test_anthracite_collector_extracts_ids_from_sitemap(tmp_path):
    sitemap = ('<?xml version="1.0"?><urlset>'
               '<url><loc>https://anthracitecoffee.com/shop_view/193</loc></url>'
               '<url><loc>https://anthracitecoffee.com/shop_view/193</loc></url>'
               '<url><loc>https://anthracitecoffee.com/home</loc></url>'
               '</urlset>')
    product_html = read_fixture("anthracite_product.html")

    class FakeResp:
        def __init__(self, text):
            self.text = text

    class FakeHttp:
        def get(self, url):
            if url.endswith("sitemap.xml"):
                return FakeResp(sitemap)
            return FakeResp(product_html)

    c = AnthraciteCollector()
    records = c.collect(FakeHttp(), tmp_path, "2026-09-27")
    assert len(records) == 1  # the duplicate idx=193 is deduped
    assert records[0].key == "anthracite:193"


def test_momos_collector_extracts_ids_from_shop_listing(tmp_path):
    listing = '<a href="/shop/?idx=7375">a</a><a href="/shop/?idx=7375">dup</a>'
    product_html = read_fixture("momos_product.html")

    class FakeResp:
        def __init__(self, text):
            self.text = text

    class FakeHttp:
        def get(self, url):
            if url.endswith("/shop"):
                return FakeResp(listing)
            return FakeResp(product_html)

    c = MomosCollector()
    records = c.collect(FakeHttp(), tmp_path, "2026-09-27")
    assert len(records) == 1
    assert records[0].key == "momos:7375"


def test_felt_collector_extracts_ids_from_category_listing(tmp_path):
    listing = ('<a href="/product/온두라스-쿠쿠루초-파카마라-워시드/488/category/30/display/1/">a</a>'
               '<a href="/product/온두라스-쿠쿠루초-파카마라-워시드/488/category/30/display/1/">dup</a>')
    product_html = read_fixture("felt_product.html")

    class FakeResp:
        def __init__(self, text):
            self.text = text

    class FakeHttp:
        def get(self, url):
            if "list.html" in url:
                return FakeResp(listing)
            return FakeResp(product_html)

    c = FeltCollector()
    records = c.collect(FakeHttp(), tmp_path, "2026-09-27")
    assert len(records) == 1
    assert records[0].key == "felt:488"


def test_beanbrothers_collector_merges_both_categories_and_dedupes(tmp_path):
    listing_blend = '<a href="/goods/goods_view.php?goodsNo=1000001418">a</a>'
    listing_single = '<a href="/goods/goods_view.php?goodsNo=1000001418">dup</a>'
    product_html = read_fixture("beanbrothers_product.html")

    class FakeResp:
        def __init__(self, text):
            self.text = text

    class FakeHttp:
        def get(self, url):
            if "007001001" in url:
                return FakeResp(listing_blend)
            if "007001002" in url:
                return FakeResp(listing_single)
            return FakeResp(product_html)

    c = BeanBrothersCollector()
    records = c.collect(FakeHttp(), tmp_path, "2026-09-27")
    assert len(records) == 1
    assert records[0].key == "beanbrothers:1000001418"


def test_manufact_collector_extracts_ids_from_category_listing(tmp_path):
    listing = '<a href="/product/guatemala-waykan-과테말라-와이칸/426/category/66/display/1/">a</a>'
    product_html = read_fixture("manufact_product.html")

    class FakeResp:
        def __init__(self, text):
            self.text = text

    class FakeHttp:
        def get(self, url):
            if "list.html" in url:
                return FakeResp(listing)
            return FakeResp(product_html)

    c = ManufactCollector()
    records = c.collect(FakeHttp(), tmp_path, "2026-09-27")
    assert len(records) == 1
    assert records[0].key == "manufact:426"


# --------------------------------------------------------------------------- #
# Caching / orchestration
# --------------------------------------------------------------------------- #


def test_fetch_cached_writes_once_and_reuses_file(tmp_path):
    calls = []

    class FakeResp:
        text = "hello"

    class FakeHttp:
        def get(self, url):
            calls.append(url)
            return FakeResp()

    http = FakeHttp()
    assert fetch_cached(http, "https://x/a", tmp_path, "a.html") == "hello"
    assert fetch_cached(http, "https://x/a", tmp_path, "a.html") == "hello"
    assert calls == ["https://x/a"]  # second call served from disk
    assert (tmp_path / "a.html").read_text(encoding="utf-8") == "hello"


def test_fetch_cached_returns_none_when_robots_disallow(tmp_path):
    class FakeHttp:
        def get(self, url):
            raise RobotsDisallowed(url)

    assert fetch_cached(FakeHttp(), "https://x/a", tmp_path, "a.html") is None
    assert not (tmp_path / "a.html").exists()


class _OkSiteCollector:
    name = "ok_site"

    def collect(self, http, cache_dir, collected_at):
        return [BeanFactRecord(
            key="ok_site:1", site="ok_site", roaster="테스트로스터리", name="테스트 원두",
            origin_country="에티오피아", process="워시드", is_decaf=False,
            flavor_notes=["베리"], price_krw=20000, weight_g=200,
            product_url="https://x/1", collected_at=collected_at,
        )]


class _BoomSiteCollector:
    name = "boom_site"

    def collect(self, http, cache_dir, collected_at):
        raise RuntimeError("site down")


def test_run_roasters_kr_collect_merges_and_isolates_failures(tmp_path):
    stats, out_path = run_roasters_kr_collect(
        http=None, raw_root=tmp_path, collected_at="2026-09-26",
        collectors=[_BoomSiteCollector(), _OkSiteCollector()],
    )
    assert out_path == tmp_path / "roasters_kr" / "beans.jsonl"
    assert "site down" in stats["boom_site"]["error"]
    assert stats["ok_site"]["count"] == 1
    assert stats["total"]["count"] == 1
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["key"] == "ok_site:1"


# --------------------------------------------------------------------------- #
# Taste-intensity gauges (docs/adr/0011-roaster-gauges-feature-model.md)
# --------------------------------------------------------------------------- #


def test_normalize_gauge_maps_five_step_scale_onto_1_to_5():
    assert normalize_gauge(1) == 1.0
    assert normalize_gauge(3.5) == 3.5
    assert normalize_gauge(5) == 5.0
    assert normalize_gauge(0.5) == 1.0          # a lone half dot is still the floor of our scale
    assert normalize_gauge(0) == 1.0
    assert normalize_gauge(8, scale_max=10) == 4.0


def test_dot_gauges_reads_korean_english_and_half_dots():
    assert dot_gauges("산미 Acidity ●●●◐○ 단맛 Sweetness ●●●○○") == {"acidity": 3.5, "sweetness": 3.0}
    assert dot_gauges("신맛 ●◐○○○ 단맛 ●●●●○") == {"acidity": 1.5, "sweetness": 4.0}
    assert dot_gauges("Acidity ●●●●○ / Body ●●○○○") == {"acidity": 4.0, "body": 2.0}


def test_dot_gauges_rejects_runs_that_are_not_five_dots():
    assert dot_gauges("Acidity ●●○○○ / Body ●●●●◐○") == {"acidity": 2.0}


def test_number_gauges_reads_pipe_separated_summary():
    assert number_gauges("후미 산미 4.5│향미 5│균형 2│바디감 2│단맛 3") == {
        "acidity": 4.5, "body": 2.0, "sweetness": 3.0}


def test_parse_altitude_m_midpoint_and_bounds():
    assert parse_altitude_m("1,950-2,050m") == 2000
    assert parse_altitude_m("1,066m") == 1066
    assert parse_altitude_m("1600~2000 masl") == 1800
    assert parse_altitude_m("50m") is None
    assert parse_altitude_m(None) is None


def test_parse_libre_reads_dot_gauges_altitude_and_variety():
    facts = parse_libre_product(read_fixture("libre_product.html"), "https://coffeelibre.kr/product/x/8153/")
    assert facts["gauge_acidity"] == 3.5
    assert facts["gauge_sweetness"] == 3.0
    assert "gauge_body" not in facts            # this site shows no body gauge
    assert facts["gauge_scale"].startswith("coffeelibre")
    assert facts["altitude_m"] == 1066
    assert facts["variety"] == "파라이네마"


def test_parse_libre_without_gauge_leaves_gauge_fields_unset():
    facts = parse_libre_product(read_fixture("libre_decaf_roast.html"), "https://coffeelibre.kr/product/x/9001/")
    assert not any(k.startswith("gauge_") for k in facts)
    BeanFactRecord(key="coffeelibre:9001", site="coffeelibre", roaster="커피 리브레", collected_at="2026-09-28",
                   **facts)                      # optional fields default to None


def test_parse_onekg_reads_sensory_chart_bars():
    facts = parse_onekg_product(read_fixture("onekg_product.html"),
                                 "https://www.1kgcoffee.co.kr/goods/goods_view.php?goodsNo=1000000688")
    assert facts["gauge_acidity"] == 1.0         # half segment -> floor of 1
    assert facts["gauge_sweetness"] == 3.0
    assert facts["gauge_bitterness"] == 1.0
    assert facts["gauge_body"] == 2.5
    assert facts["gauge_scale"].startswith("onekgcoffee")


def test_parse_groasting_reads_displayed_summary_not_commented_table():
    facts = parse_groasting_product(read_fixture("groasting_product.html"),
                                     "https://groasting.com/product/detail.html?product_no=91")
    assert facts["name"].startswith("약배전 산미높은 에티오피아")
    assert facts["origin_country"] == "에티오피아"
    assert facts["process"] == "내추럴"
    assert facts["roast_level"] == "약배전"
    assert facts["flavor_notes"] == ["장미", "살구", "망고", "건자두", "초콜릿", "감귤류", "베리"]
    assert facts["price_krw"] == 13500
    assert facts["weight_g"] == 200
    # 바디감 2 from the live og:description, not the stale 2.5 inside the HTML comment
    assert (facts["gauge_acidity"], facts["gauge_body"], facts["gauge_sweetness"]) == (4.5, 2.0, 3.0)


def test_parse_naeil_single_origin_reads_summary_gauges_and_fact_line():
    facts = parse_naeil_product(read_fixture("naeil_product.html"), "https://www.naeilcoffee.co.kr/shop_view/?idx=117")
    assert facts["name"] == "HUNKUTE 200g"
    assert facts["origin_country"] == "Ethiopia"
    assert facts["process"] == "Washed"
    assert facts["altitude_m"] == 2000
    assert facts["variety"] == "Heirloom"
    assert facts["flavor_notes"] == ["Black Tea", "Lemongrass", "Almond"]
    # the product's own summary block, not the "related" widget's gauges further down the page
    assert (facts["gauge_acidity"], facts["gauge_body"]) == (3.5, 3.5)
    assert "gauge_sweetness" not in facts


def test_parse_naeil_drops_truncated_variety():
    facts = parse_naeil_product(read_fixture("naeil_blend_truncated_variety.html"), "u")
    assert facts["variety"] is None             # "Arusha bl..." was cut off by the JSON-LD description
    assert facts["altitude_m"] == 1800
    assert facts["origin_country"] == "Papua New Guinea"


def test_parse_naeil_skips_coffee_bags():
    assert parse_naeil_product(read_fixture("naeil_coffeebag.html"), "u") is None


def test_new_gauge_collectors_are_registered():
    names = {c.name for c in ALL_ROASTER_COLLECTORS}
    assert {"groasting", "naeilcoffee"} <= names
