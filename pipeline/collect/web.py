import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from pipeline import settings
from pipeline.http import PoliteClient, RobotsDisallowed

log = logging.getLogger("pipeline.collect.web")

STARBUCKS_URL = "https://www.starbucks.co.kr/upload/json/menu/{code}.js"
MEGA_URL = "https://www.mega-mgccoffee.com/menu/menu.php"
PAIK_URL = "https://paikdabang.com/menu/menu_coffee/"
HOLLYS_URL = "https://www.hollys.co.kr/menu/espresso.do"
COMPOSE_URL = "https://composecoffee.com/compose"
COMPOSE_COFFEE_TAG = "02. 커피ㆍ콜드브루"  # site's own category tag; filters out tea/ade/food server-side
COFFEEBEAN_URL = "https://www.coffeebeankorea.com/menu/app.asp"
# menu/list.asp coffee nav (2026-09-26 확인): 13=에스프레소 음료, 14=브루드 커피, 12=아이스 블렌디드 (COFFEE).
# 티(18)·티 라떼(17)·아이스 블렌디드 (NON-COFFEE)(11)·커피빈 주스(26)·기타 제조 음료(24)는 커피 음료가 아니라 제외.
COFFEEBEAN_CATEGORIES = (13, 14, 12)
PAULBASSETT_LIST_URL = "https://www.baristapaulbassett.co.kr/menu/List.pb"
PAULBASSETT_VIEW_URL = "https://www.baristapaulbassett.co.kr/menu/View.pb"
PAULBASSETT_DPID_RE = re.compile(r"goView\('(\w+)'\)")


@dataclass
class StarbucksCollector:
    name: str = "starbucks"
    codes: tuple[str, ...] = ("W0000003", "W0000004")  # 에스프레소(아메리카노 등), 블렌디드 커피

    def collect(self, out_dir: Path, http) -> list[Path]:
        files = []
        for code in self.codes:
            text = http.get(STARBUCKS_URL.format(code=code)).text
            try:
                json.loads(text)
            except ValueError:
                continue  # unknown category code returns an HTML page
            p = out_dir / f"{code}.json"
            p.write_text(text, encoding="utf-8")
            files.append(p)
        return files


@dataclass
class MegaCollector:
    name: str = "mega"
    max_pages: int = 30

    def collect(self, out_dir: Path, http) -> list[Path]:
        files = []
        for page in range(1, self.max_pages + 1):
            params = {"menu_category1": 1, "menu_category2": 1, "category": "",
                      "list_checkbox_all": "all", "page": page}
            html = http.get(MEGA_URL, params=params).text
            if "inner_modal_open" not in html:
                break
            p = out_dir / f"page_{page}.html"
            p.write_text(html, encoding="utf-8")
            files.append(p)
        return files


@dataclass
class PaikCollector:
    name: str = "paik"

    def collect(self, out_dir: Path, http) -> list[Path]:
        p = out_dir / "coffee.html"
        p.write_text(http.get(PAIK_URL).text, encoding="utf-8")
        return [p]


@dataclass
class ComposeCollector:
    name: str = "compose"
    max_pages: int = 15

    def collect(self, out_dir: Path, http) -> list[Path]:
        files = []
        for page in range(1, self.max_pages + 1):
            params = {"search_tag": COMPOSE_COFFEE_TAG, "tab": "nutrition", "page": page}
            html = http.get(COMPOSE_URL, params=params).text
            if 'data-label="품목명"' not in html:
                break  # past the last page: only the "no results" placeholder row remains
            p = out_dir / f"page_{page}.html"
            p.write_text(html, encoding="utf-8")
            files.append(p)
        return files


@dataclass
class CoffeebeanCollector:
    """커피빈코리아: classic ASP menu, served as UTF-8 (checked 2026-09-26; no EUC-KR re-decode needed).
    Each coffee category paginates via ?category=<id>&page=<n>; stop once a page has no menu items."""

    name: str = "coffeebean"
    categories: tuple[int, ...] = COFFEEBEAN_CATEGORIES
    max_pages: int = 20

    def collect(self, out_dir: Path, http) -> list[Path]:
        files = []
        for cid in self.categories:
            for page in range(1, self.max_pages + 1):
                params = {"category": cid} if page == 1 else {"category": cid, "page": page}
                html = http.get(COFFEEBEAN_URL, params=params).text
                if 'class="kor"' not in html:
                    break
                p = out_dir / f"cat{cid}_page{page}.html"
                p.write_text(html, encoding="utf-8")
                files.append(p)
        return files


@dataclass
class ShopifyCollector:
    name: str = "shopify"
    domains: tuple[str, ...] | None = None
    max_pages: int = 40
    config_key: str = "shopify"     # config/sources.yaml list of stores to collect

    def collect(self, out_dir: Path, http) -> list[Path]:
        domains = self.domains or tuple(s["domain"] for s in settings.load_config("sources.yaml")[self.config_key])
        files = []
        for domain in domains:
            products = []
            for page in range(1, self.max_pages + 1):
                batch = http.get(f"https://{domain}/products.json", params={"limit": 250, "page": page}).json().get("products", [])
                if not batch:
                    break
                products += batch
            p = out_dir / f"{domain}.json"
            p.write_text(json.dumps({"products": products}, ensure_ascii=False), encoding="utf-8")
            files.append(p)
        return files


@dataclass
class ShopifyGaugedCollector(ShopifyCollector):
    """Shopify roasters whose products.json carries the roaster's own intensity profile (tags / labelled lines;
    pipeline/normalize/shopify_gauged.py, docs/adr/0013-open-labels-weak-supervision.md). Same /products.json
    pagination as ShopifyCollector; PoliteClient checks robots.txt before every request."""

    name: str = "shopify_gauged"
    config_key: str = "shopify_gauged"

    def collect(self, out_dir: Path, http) -> list[Path]:
        """Per store: one store that disallows /products.json in robots.txt or errors is skipped, not fatal."""
        domains = self.domains or tuple(s["domain"] for s in settings.load_config("sources.yaml")[self.config_key])
        files = []
        for domain in domains:
            try:
                files += ShopifyCollector(domains=(domain,), max_pages=self.max_pages).collect(out_dir, http)
            except (RobotsDisallowed, httpx.HTTPError, ValueError) as e:
                log.warning("shopify_gauged: %s skipped (%s)", domain, e)
        return files


@dataclass
class HollysCollector:
    """The single COFFEE (ESPRESSO) menu page: name, HOT/ICED nutrition table (incl. caffeine) are all inline,
    no pagination and no detail page needed. Other tabs (라떼·초콜릿·티, 할리치노·빙수, 스무디·주스, ...) mix in
    non-coffee drinks, so only this one category page is collected."""

    name: str = "hollys"

    def collect(self, out_dir: Path, http) -> list[Path]:
        p = out_dir / "espresso.html"
        p.write_text(http.get(HOLLYS_URL).text, encoding="utf-8")
        return [p]


@dataclass
class PaulbassettCollector:
    """The site's TLS chain is self-signed, so this collector builds its own client with
    verify=False (the only place this project does that) instead of using the passed-in http
    (only its per-host delay is reused)."""

    name: str = "paulbassett"

    def collect(self, out_dir: Path, http) -> list[Path]:
        files = []
        with PoliteClient(delay=http.delay, verify=False) as pb_http:
            list_html = pb_http.get(PAULBASSETT_LIST_URL, params={"cid1": "A"}).text
            p = out_dir / "list.html"
            p.write_text(list_html, encoding="utf-8")
            files.append(p)
            for dpid in dict.fromkeys(PAULBASSETT_DPID_RE.findall(list_html)):
                detail = pb_http.get(PAULBASSETT_VIEW_URL, params={"dpid": dpid}).text
                dp = out_dir / f"{dpid}.html"
                dp.write_text(detail, encoding="utf-8")
                files.append(dp)
        return files


EDIYA_URL = "https://ediya.com/contents/drink.html"
# 이디야 음료 페이지(서버 렌더링)는 카테고리 체크(chked_val)와 검색어(skeyword)를 함께 받아 첫 8개 카드만 그린다.
# "더보기"는 /inc/ajax_brand.php 인데 robots.txt가 /inc/ 를 막으므로 절대 부르지 않는다 — 대신 대표 커피 음료
# 검색어를 고정해 카테고리마다 한 번씩 조회한다. 12=COFFEE, 155=DECAF(같은 음료의 디카페인 SKU, 카페인 7~18mg).
EDIYA_CATEGORIES = {12: "COFFEE", 155: "DECAF"}
# 검색은 메뉴 이름의 띄어쓰기 그대로 맞춰야 한다("카페라떼" 0건, "카페 라떼" 8건; 2026-09-28 확인). 돌체·토피넛 라떼는
# 현재 메뉴에 없음. 8개 제한 때문에 넓은 단어(라떼·커피·카페·모카)와 개별 이름을 함께 둔다.
EDIYA_KEYWORDS = ("아메리카노", "카페 라떼", "바닐라 라떼", "카푸치노", "카페 모카", "카라멜 마끼아또", "콜드브루",
                  "디카페인", "에스프레소", "연유", "헤이즐넛", "모카", "라떼", "커피", "카페", "아포가토", "코코넛",
                  "시그니처", "흑당", "샷", "꿀", "아이스크림")


@dataclass
class EdiyaCollector:
    """이디야커피 공식 음료 페이지: 카테고리(COFFEE/DECAF) × (검색어 없음 + 고정 검색어) 조회 결과를 그대로 저장.
    카드마다 이름·(L)/(EX) 사이즈·HOT/ICED·카페인 mg·컵용량이 서버 HTML에 들어 있다. 파일명 cat<id>_q<nn>.html 의
    <id>가 카테고리(정규화 단계가 읽는다)."""

    name: str = "ediya"
    categories: tuple[int, ...] = tuple(EDIYA_CATEGORIES)
    keywords: tuple[str, ...] = EDIYA_KEYWORDS

    def collect(self, out_dir: Path, http) -> list[Path]:
        files = []
        for cid in self.categories:
            for i, kw in enumerate(("", *self.keywords)):
                html = http.get(EDIYA_URL, params={"chked_val": f"{cid},", "skeyword": kw}).text
                if "pro_detail" not in html:
                    continue  # no card for this keyword in this category
                p = out_dir / f"cat{cid}_q{i:02d}.html"
                p.write_text(html, encoding="utf-8")
                files.append(p)
        return files
