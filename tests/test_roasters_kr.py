import json
from pathlib import Path

import pytest

from pipeline.collect.roasters_kr import (
    BeanFactRecord,
    BlueBottleCollector,
    fetch_cached,
    find_country,
    find_decaf_method,
    find_process,
    find_roast_from_brackets,
    flavor_notes_from_ld_description,
    is_decaf,
    ko_prefix,
    parse_bluebottle_product,
    parse_fritz_product,
    parse_libre_product,
    parse_namusairo_product,
    parse_onekg_product,
    parse_price_krw,
    parse_weight_g,
    run_roasters_kr_collect,
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
