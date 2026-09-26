import json
from dataclasses import dataclass
from pathlib import Path

from pipeline import settings

STARBUCKS_URL = "https://www.starbucks.co.kr/upload/json/menu/{code}.js"
MEGA_URL = "https://www.mega-mgccoffee.com/menu/menu.php"
PAIK_URL = "https://paikdabang.com/menu/menu_coffee/"
COMPOSE_URL = "https://composecoffee.com/compose"
COMPOSE_COFFEE_TAG = "02. 커피ㆍ콜드브루"  # site's own category tag; filters out tea/ade/food server-side


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
