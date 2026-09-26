import json
from dataclasses import dataclass
from pathlib import Path

from pipeline import settings

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

    def collect(self, out_dir: Path, http) -> list[Path]:
        domains = self.domains or tuple(s["domain"] for s in settings.load_config("sources.yaml")["shopify"])
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
class HollysCollector:
    """The single COFFEE (ESPRESSO) menu page: name, HOT/ICED nutrition table (incl. caffeine) are all inline,
    no pagination and no detail page needed. Other tabs (라떼·초콜릿·티, 할리치노·빙수, 스무디·주스, ...) mix in
    non-coffee drinks, so only this one category page is collected."""

    name: str = "hollys"

    def collect(self, out_dir: Path, http) -> list[Path]:
        p = out_dir / "espresso.html"
        p.write_text(http.get(HOLLYS_URL).text, encoding="utf-8")
        return [p]
