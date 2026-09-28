import json
import re
from pathlib import Path

import yaml
from bs4 import BeautifulSoup, NavigableString

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
            caffeine = num(it.get("caffeine"))
            # NOT menu_is_decaf: this feed (esp. W0000004 블렌디드 커피) also lists non-coffee cream
            # frappuccinos with 0mg caffeine (e.g. "화이트 타이거 프라푸치노") — the <=15mg rule would
            # wrongly brand those "decaf" — so stick to name-only detection here.
            items[key] = MenuItemRecord(
                key=key, brand_key="brand:starbucks", name=name, name_en=clean(it.get("product_ENGNM")),
                category=clean(it.get("cate_NAME")), is_decaf=detect_decaf(name)[0],
                decaf_option=p.stem in STARBUCKS_DECAF_OPTION_CODES, caffeine_mg=caffeine,
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
            caffeine = float(m.group(1)) if m else None
            key = f"menu:mega:{name}"
            # NOT menu_is_decaf: this single collected URL also lists non-coffee drinks (ades, smoothies,
            # teas, e.g. "레몬에이드") with 0mg caffeine under the same hardcoded category="커피" — the
            # <=15mg rule would wrongly brand those "decaf" — so stick to name-only detection here.
            items[key] = MenuItemRecord(
                key=key, brand_key="brand:mega", name=name, name_en=en.get_text(strip=True) if en else None,
                category="커피", is_decaf=detect_decaf(name)[0], caffeine_mg=caffeine,
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
            category="커피", is_decaf=menu_is_decaf(name, caffeine), caffeine_mg=caffeine,
            source_url="https://paikdabang.com/menu/menu_coffee/", collected_at=collected_at,
        )
    return Normalized(menu_items=list(items.values()))


HOLLYS_CATEGORY = "에스프레소"  # site tab label is "COFFEE" (menuDiv=ESPRESSO); only coffee-only category page


def _hollys_caffeine(table) -> float | None:
    """HOT/ICED (or a single row) share one nutrition table; 카페인 is always the last header column. Take the
    larger of HOT/ICED when both are present."""
    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    if "카페인" not in headers:
        return None
    col = headers.index("카페인") - 1  # header row has a leading blank <th> for the HOT/ICED row label
    best = None
    for row in table.select("tbody tr"):
        tds = row.find_all("td")
        if 0 <= col < len(tds):
            v = num(re.sub(r"[^0-9.]", "", tds[col].get_text()))
            if v is not None:
                best = v if best is None else max(best, v)
    return best


def normalize_hollys(snap: Path, collected_at: str) -> Normalized:
    items: dict[str, MenuItemRecord] = {}
    p = snap / "espresso.html"
    if not p.exists():
        return Normalized()
    soup = _soup(p)
    for br in soup.find_all("br"):
        br.replace_with(" ")  # e.g. "디카페인<br>콜드브루" -> one name, not two
    brand = brands_by_key(settings.CURATED_DIR)["brand:hollys"]
    for view in soup.select("div.menu_view01"):
        idx = (view.get("id") or "").removeprefix("menuView1_")
        span = view.select_one("p span")
        if not idx or not span:
            continue
        name = clean(span.get_text(" ", strip=True))
        if not name:
            continue
        p_tag = span.find_parent("p")
        en_bits = [str(c).strip() for c in p_tag.contents if c is not span] if p_tag else []
        name_en = clean(" ".join(b for b in en_bits if b))
        info = soup.find(id=f"menuView2_{idx}")
        table = info.find("table") if info else None
        caffeine = _hollys_caffeine(table) if table else None
        is_decaf = menu_is_decaf(name, caffeine)
        key = f"menu:hollys:{idx}"
        items[key] = MenuItemRecord(
            key=key, brand_key="brand:hollys", name=name, name_en=name_en,
            category=HOLLYS_CATEGORY, is_decaf=is_decaf,
            decaf_option=menu_decaf_option(brand, HOLLYS_CATEGORY, is_decaf, name),
            caffeine_mg=caffeine, source_url="https://www.hollys.co.kr/menu/espresso.do",
            collected_at=collected_at,
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
            # The site's item markup has an unclosed <div> before </li>, so lxml nests every following
            # <li> inside the current one; `li.select("div.info dl")` would then also match later items'
            # nutrition rows. `li.find("div", class_="info")` returns only the item's OWN div.info (the
            # first one in document order, before the mis-nested trailing content), and iterating its
            # direct dl children (recursive=False) cannot descend into a nested <li> at all.
            info = li.find("div", class_="info")
            if info:
                for dl in info.find_all("dl", recursive=False):
                    dt, dd = dl.select_one("dt"), dl.select_one("dd")
                    if dt and dd and "카페인" in dd.get_text():
                        caffeine = num(dt.get_text(strip=True))
            is_decaf = menu_is_decaf(name, caffeine)
            key = f"menu:coffeebean:{name}"
            items[key] = MenuItemRecord(
                key=key, brand_key="brand:coffeebean", name=name,
                name_en=clean(eng.get_text()) if eng else None, category=category,
                is_decaf=is_decaf, decaf_option=menu_decaf_option(brand, category, is_decaf, name),
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


def menu_is_decaf(name: str, caffeine_mg: float | None) -> bool:
    """A coffee-category drink counts as decaf either by name (디카페인/decaf) or, when no such word
    appears, by a low measured caffeine reading (<=15mg) — e.g. 컴포즈 「올데이 오트」 9.16mg."""
    return detect_decaf(name)[0] or (caffeine_mg is not None and caffeine_mg <= 15)


NO_SHOT_WORDS = (
    "콜드브루", "더치", "드립커피", "브루드",  # brewed coffees have no espresso shot to swap for decaf
    "말차", "큐브", "믹스커피", "데일리커피",  # not espresso-based (or a fixed blend): a decaf shot swap doesn't apply
)


def menu_decaf_option(brand: BrandRecord, category: str | None, is_decaf: bool, name: str = "") -> bool:
    """Can the guest ask for a decaf shot? Only for espresso-based categories the brand lists, never for a drink
    that is already decaf, and never for cold brew / drip (no shot to swap; brands sell separate decaf cold brew)."""
    n = "".join(name.split())
    if any(w in n for w in NO_SHOT_WORDS):
        return False
    return bool(brand.decaf_available and not is_decaf and category in brand.decaf_option_categories)


def brands_by_key(curated_dir: Path) -> dict[str, BrandRecord]:
    return {b.key: b for b in normalize_brands(curated_dir)}


COMPOSE_CATEGORY = "커피ㆍ콜드브루"  # site's own tag (numbering prefix "02. " stripped), matches brands.yaml
_COMPOSE_HOT_ICED = re.compile(r"^([HI])-(.+)$")  # site's own HOT/ICED marker, e.g. "H-아메리카노"/"I-아메리카노"


def normalize_compose(snap: Path, collected_at: str) -> Normalized:
    brand = brands_by_key(settings.CURATED_DIR)["brand:compose"]
    items: dict[str, MenuItemRecord] = {}
    for p in sorted(snap.glob("page_*.html")):
        for tr in _soup(p).select("#nutrition_table tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue  # the "no results" placeholder row on a past-the-end page
            badge = tds[0].select_one(".cafemenu_status_badge")
            if badge:
                badge.decompose()
            raw_name = clean(tds[0].get_text(strip=True))
            if not raw_name or raw_name.endswith("빵"):
                continue  # the coffee tab also lists a bread item ("커피엔 역시 커피빵")
            name = re.sub(r"\s+", " ", raw_name)
            m = _COMPOSE_HOT_ICED.match(name)
            base_name = m.group(2) if m else name
            caffeine = num(tds[4].get_text(strip=True))
            is_decaf = menu_is_decaf(base_name, caffeine)
            key = f"menu:compose:{base_name}"
            existing = items.get(key)
            if existing is not None and (caffeine is None or (existing.caffeine_mg or 0) >= caffeine):
                continue  # keep the larger of the HOT/ICED caffeine values
            items[key] = MenuItemRecord(
                key=key, brand_key="brand:compose", name=base_name, category=COMPOSE_CATEGORY,
                is_decaf=is_decaf, decaf_option=menu_decaf_option(brand, COMPOSE_CATEGORY, is_decaf, base_name),
                caffeine_mg=caffeine,
                source_url="https://composecoffee.com/compose?search_tag=02.+%EC%BB%A4%ED%94%BC%E3%86%8D%EC%BD%9C%EB%93%9C%EB%B8%8C%EB%A3%A8&tab=nutrition",
                collected_at=collected_at,
            )
    return Normalized(menu_items=list(items.values()))


PAULBASSETT_CATEGORY = "커피"  # the site's single coffee tab (cid1=A) covers every coffee sub-category


def normalize_paulbassett(snap: Path, collected_at: str) -> Normalized:
    brand = brands_by_key(settings.CURATED_DIR)["brand:paulbassett"]
    items: dict[str, MenuItemRecord] = {}
    for p in sorted(snap.glob("PB*.html")):
        soup = _soup(p)
        dt = soup.select_one(".menuTit dt")
        if not dt:
            continue
        name_en = clean(dt.find("span").get_text(strip=True)) if dt.find("span") else None
        name = None
        for c in dt.contents:
            if isinstance(c, NavigableString):
                t = clean(str(c))
                if t:
                    name = t
                    break
        if not name:
            continue
        caffeine = None
        size_div = soup.select_one('div[id^="pSize_"]')
        if size_div:
            for li in size_div.select("ul li"):
                tit = li.select_one(".tit")
                num_el = li.select_one(".num")
                if tit and num_el and "카페인" in tit.get_text():
                    m = re.search(r"[\d.]+", num_el.get_text(strip=True))
                    caffeine = float(m.group()) if m else None
        dpid = p.stem
        is_decaf = menu_is_decaf(f"{name} {name_en}" if name_en else name, caffeine)
        key = f"menu:paulbassett:{dpid}"
        items[key] = MenuItemRecord(
            key=key, brand_key="brand:paulbassett", name=name, name_en=name_en,
            category=PAULBASSETT_CATEGORY, is_decaf=is_decaf,
            decaf_option=menu_decaf_option(brand, PAULBASSETT_CATEGORY, is_decaf, name), caffeine_mg=caffeine,
            source_url=f"https://www.baristapaulbassett.co.kr/menu/View.pb?dpid={dpid}", collected_at=collected_at,
        )
    return Normalized(menu_items=list(items.values()))


EDIYA_SOURCE_URL = "https://ediya.com/contents/drink.html"
EDIYA_CATEGORY_NAMES = {"12": "COFFEE", "155": "DECAF"}   # the site's own checkbox labels (chked_val ids)
_EDIYA_TITLE = re.compile(r"^(?:\((L|EX|R|S)\)\s*)?(?:(HOT|ICED)\s+)?(.+)$")
_EDIYA_SIZE_ORDER = {"L": 0, "R": 1, "S": 2, None: 3, "EX": 4}   # L = the standard cup; EX = extra-large
_EDIYA_NOT_COFFEE = re.compile(r"아샷추|\btea\b", re.I)   # DECAF tab: 아샷추 복숭아 = "Peach Iced Tea with Espresso"
EDIYA_DECAF_MAX_MG = 100   # a "decaf" card above the app's own low-caffeine line (100 mg) is not served as decaf


def _one_space(s: str | None) -> str | None:
    return clean(re.sub(r"\s+", " ", s)) if s else None   # "Caramel  Macchiato" → one space


def parse_ediya_cards(html: str) -> list[dict]:
    """One dict per server-rendered drink card (#menu_ul .pro_detail): size ((L)/(EX), None if unmarked), temp
    (HOT/ICED, None for cold-only drinks written without it), base name, English name, caffeine mg, cup ml."""
    rows = []
    for d in BeautifulSoup(html, "lxml").select("#menu_ul .pro_detail"):
        h2 = d.select_one("h2")
        if not h2:
            continue
        span = h2.find("span")
        en = clean(span.get_text(" ", strip=True)) if span else None
        if span:
            span.extract()
        title = clean(h2.get_text(" ", strip=True))
        m = _EDIYA_TITLE.match(title or "")
        if not m:
            continue
        caffeine = None
        for dl in d.select(".pro_nutri dl"):
            dt, dd = dl.find("dt"), dl.find("dd")
            if dt and dd and dt.get_text(strip=True) == "카페인":
                mm = re.search(r"([\d.]+)\s*mg", dd.get_text())
                caffeine = float(mm.group(1)) if mm else None
        size_el = d.select_one(".pro_size")
        cup = re.search(r"(\d+)\s*ml", size_el.get_text()) if size_el else None
        en_base = _EDIYA_TITLE.match(en).group(3) if en else None
        rows.append({"id": d.get("id"), "size": m.group(1), "temp": m.group(2), "name": _one_space(m.group(3)),
                     "name_en": _one_space(en_base), "caffeine_mg": caffeine, "cup_ml": int(cup.group(1)) if cup else None})
    return rows


def normalize_ediya(snap: Path, collected_at: str) -> Normalized:
    """이디야커피 공식 음료 페이지(EdiyaCollector). Every card of one drink (sizes × HOT/ICED) becomes one item: the
    standard (L) cup's caffeine, the larger of HOT/ICED (same rule as 컴포즈/할리스). The DECAF category lists the
    decaf SKU of the SAME drink names (3~50 mg): kept as its own item "디카페인 <이름>" (key menu:ediya:decaf:...),
    except tea-with-a-decaf-shot drinks the DECAF tab also carries (아샷추 복숭아 — not coffee drinks). Cup volume is parsed (parse_ediya_cards) to pick the cup
    size; MenuItemRecord has no volume column."""
    brand = brands_by_key(settings.CURATED_DIR)["brand:ediya"]
    best: dict[tuple[str, str], dict] = {}
    for p in sorted(snap.glob("cat*_q*.html")):
        cat = EDIYA_CATEGORY_NAMES.get(re.match(r"cat(\d+)_", p.name).group(1))
        if cat is None:
            continue
        for r in parse_ediya_cards(p.read_text(encoding="utf-8")):
            k = (cat, r["name"])
            rank = (_EDIYA_SIZE_ORDER.get(r["size"], 3), -(r["caffeine_mg"] or 0))
            if k not in best or rank < best[k]["_rank"]:
                best[k] = {**r, "_rank": rank}
    # drinks whose decaf-bean version (a COFFEE card named "<name>(디카페인 원두)") still measures above the line
    too_strong_twins = {n.split("(")[0].strip() for c, n in best if c == "COFFEE" and detect_decaf(n)[0]
                        and (best[(c, n)]["caffeine_mg"] or 0) > EDIYA_DECAF_MAX_MG}
    items: dict[str, MenuItemRecord] = {}
    for (cat, name), r in sorted(best.items()):
        if cat == "DECAF":
            if _EDIYA_NOT_COFFEE.search(f"{name} {r['name_en'] or ''}") or detect_decaf(name)[0]:
                continue   # tea with a decaf shot, or a COFFEE item that is already a decaf-bean variant
            key, shown, en = f"menu:ediya:decaf:{name}", f"디카페인 {name}", r["name_en"]
            en = f"Decaf {en}" if en else None
        else:
            key, shown, en = f"menu:ediya:{name}", name, r["name_en"]
        is_decaf = cat == "DECAF" or menu_is_decaf(shown, r["caffeine_mg"])
        decaf_option = menu_decaf_option(brand, cat, is_decaf, shown)
        if (is_decaf and (r["caffeine_mg"] or 0) > EDIYA_DECAF_MAX_MG) or name in too_strong_twins:
            # 얼박샷추(디카페인 원두) 163 mg: decaf bean, but the energy drink keeps its caffeine -- neither it nor
            # the regular 얼박샷추 may be served to a decaf-only guest
            is_decaf, decaf_option = False, False
        items[key] = MenuItemRecord(
            key=key, brand_key="brand:ediya", name=shown, name_en=en, category=cat, is_decaf=is_decaf,
            decaf_option=decaf_option, caffeine_mg=r["caffeine_mg"],
            source_url=f"{EDIYA_SOURCE_URL}?chked_val={'12' if cat == 'COFFEE' else '155'},", collected_at=collected_at,
        )
    return Normalized(menu_items=list(items.values()))
