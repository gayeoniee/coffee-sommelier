import json
import re
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from pipeline import settings
from pipeline.normalize import Normalized
from pipeline.records import BrandRecord, CoffeeRecord, MenuItemRecord, ReviewRecord
from pipeline.rules import clean, detect_decaf, normalize_country, normalize_roast, num, process_from_text

STARBUCKS_DECAF_OPTION_CODES = {"W0000003"}  # espresso drinks accept a decaf shot


def normalize_starbucks(snap: Path, collected_at: str) -> Normalized:
    items: dict[str, MenuItemRecord] = {}
    for p in sorted(snap.glob("W*.json")):
        for it in json.loads(p.read_text(encoding="utf-8"))["list"]:
            name = clean(it.get("product_NM"))
            if not name:
                continue
            key = f"menu:starbucks:{clean(it.get('product_CD')) or name}"
            items[key] = MenuItemRecord(
                key=key, brand_key="brand:starbucks", name=name, name_en=clean(it.get("product_ENGNM")),
                category=clean(it.get("cate_NAME")), is_decaf=detect_decaf(name)[0],
                decaf_option=p.stem in STARBUCKS_DECAF_OPTION_CODES, caffeine_mg=num(it.get("caffeine")),
                source_url=f"https://www.starbucks.co.kr/upload/json/menu/{p.stem}.js", collected_at=collected_at,
            )
    return Normalized(menu_items=list(items.values()))


def _soup(p: Path) -> BeautifulSoup:
    return BeautifulSoup(p.read_text(encoding="utf-8"), "lxml")


def normalize_mega(snap: Path, collected_at: str) -> Normalized:
    items: dict[str, MenuItemRecord] = {}
    for p in sorted(snap.glob("page_*.html")):
        for modal in _soup(p).select("div.inner_modal"):
            name_el = modal.select_one(".cont_text_title b")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            en = modal.select_one(".inner_modal_title .cont_text_info")
            m = re.search(r"카페인\s*([\d.]+)\s*mg", modal.get_text(" ", strip=True))
            key = f"menu:mega:{name}"
            items[key] = MenuItemRecord(
                key=key, brand_key="brand:mega", name=name, name_en=en.get_text(strip=True) if en else None,
                category="커피", is_decaf=detect_decaf(name)[0], caffeine_mg=float(m.group(1)) if m else None,
                source_url="https://www.mega-mgccoffee.com/menu/?menu_category1=1&menu_category2=1",
                collected_at=collected_at,
            )
    return Normalized(menu_items=list(items.values()))


def normalize_paik(snap: Path, collected_at: str) -> Normalized:
    items: dict[str, MenuItemRecord] = {}
    p = snap / "coffee.html"
    if not p.exists():
        return Normalized()
    for hv in _soup(p).select("div.hover"):
        h3 = hv.select_one("h3")
        if not h3:
            continue
        name = h3.get_text(strip=True)
        key = f"menu:paik:{name}"
        caffeine = None
        for li in hv.select("ul.ingredient_table li"):
            divs = li.find_all("div")
            if len(divs) >= 2 and "카페인" in divs[0].get_text():
                caffeine = num(divs[1].get_text(strip=True))
        if key in items and caffeine is None:
            continue  # the recommendation slider repeats items without nutrition rows
        en = hv.select_one(".menu_tit2")
        items[key] = MenuItemRecord(
            key=key, brand_key="brand:paik", name=name, name_en=en.get_text(strip=True) if en else None,
            category="커피", is_decaf=detect_decaf(name)[0], caffeine_mg=caffeine,
            source_url="https://paikdabang.com/menu/menu_coffee/", collected_at=collected_at,
        )
    return Normalized(menu_items=list(items.values()))


def normalize_coffeebean(snap: Path, collected_at: str) -> Normalized:
    brand = brands_by_key(settings.CURATED_DIR)["brand:coffeebean"]
    items: dict[str, MenuItemRecord] = {}
    for p in sorted(snap.glob("cat*_page*.html")):
        soup = _soup(p)
        cat_el = soup.select_one("div.category2 a.select_a")
        category = clean(cat_el.get_text()) if cat_el else None
        for li in soup.select("ul.menu_list li"):
            kor = li.select_one(".txt .kor")
            if not kor:
                continue
            name = clean(kor.get_text())
            if not name:
                continue
            # coffeebeankorea.com writes HOT/ICED as different names ("아메리카노" vs "아이스 아메리카노")
            # rather than a HOT/ICED suffix on the same name, so each stays its own item (never merged).
            eng = li.select_one(".txt .eng")
            caffeine = None
            for dl in li.select("div.info dl"):
                dt, dd = dl.select_one("dt"), dl.select_one("dd")
                if dt and dd and "카페인" in dd.get_text():
                    caffeine = num(dt.get_text(strip=True))
            is_decaf = detect_decaf(name)[0]
            key = f"menu:coffeebean:{name}"
            items[key] = MenuItemRecord(
                key=key, brand_key="brand:coffeebean", name=name,
                name_en=clean(eng.get_text()) if eng else None, category=category,
                is_decaf=is_decaf, decaf_option=menu_decaf_option(brand, category, is_decaf),
                caffeine_mg=caffeine,
                source_url="https://www.coffeebeankorea.com/menu/list.asp", collected_at=collected_at,
            )
    return Normalized(menu_items=list(items.values()))


def _html_text(html: str) -> str:
    return BeautifulSoup(html or "", "lxml").get_text("\n", strip=True)


_ROAST_CONTEXT = re.compile(r"로스팅|로스트|roast", re.I)


def _roast_from_text(text: str) -> str | None:
    """Roast words only count in sentences about roasting ("다크 초콜릿" is a flavor, not a roast)."""
    sentences = re.split(r"[\n.!?]+", text or "")
    return normalize_roast(" ".join(s for s in sentences if _ROAST_CONTEXT.search(s)))


def normalize_shopify(snap: Path, collected_at: str, shops: list[dict] | None = None) -> Normalized:
    shops = shops if shops is not None else settings.load_config("sources.yaml")["shopify"]
    by_domain = {s["domain"]: s for s in shops}
    out = Normalized()
    for p in sorted(snap.glob("*.json")):
        if p.name == "manifest.json":
            continue
        shop = by_domain.get(p.stem, {"domain": p.stem, "roaster": p.stem, "product_types": []})
        for prod in json.loads(p.read_text(encoding="utf-8"))["products"]:
            if prod.get("product_type") not in shop["product_types"]:
                continue
            title = clean(prod.get("title")) or "(unknown)"
            text = _html_text(prod.get("body_html"))
            key = f"shopify:{p.stem}:{prod.get('handle')}"
            url = f"https://{p.stem}/products/{prod.get('handle')}"
            is_decaf, decaf_process = detect_decaf(title, text)
            out.coffees.append(CoffeeRecord(
                key=key, name=title, roaster=shop["roaster"],
                origin_country=normalize_country(f"{title} {text}"),
                process=process_from_text(f"{title} {text}"), roast_level=_roast_from_text(text),
                is_decaf=is_decaf, decaf_process=decaf_process,
                flavor_summary=text.split("\n")[0] if text else None,
                source="shopify", source_url=url, collected_at=collected_at,
            ))
            if text:
                out.reviews.append(ReviewRecord(key=f"review:{key}", coffee_key=key, text=text,
                                                source="shopify", source_url=url, collected_at=collected_at))
    return out


def normalize_brands(curated_dir: Path) -> list[BrandRecord]:
    p = curated_dir / "brands.yaml"
    if not p.exists():
        return []
    return [BrandRecord.model_validate(b) for b in yaml.safe_load(p.read_text(encoding="utf-8"))]


def menu_decaf_option(brand: BrandRecord, category: str | None, is_decaf: bool) -> bool:
    """Can the guest ask for a decaf shot? Only for espresso-based categories the brand lists, never for a drink
    that is already decaf."""
    return bool(brand.decaf_available and not is_decaf and category in brand.decaf_option_categories)


def brands_by_key(curated_dir: Path) -> dict[str, BrandRecord]:
    return {b.key: b for b in normalize_brands(curated_dir)}
