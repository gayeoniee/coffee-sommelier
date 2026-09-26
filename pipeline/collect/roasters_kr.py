"""Facts-only collector for Korean specialty-coffee roastery product pages.

Purpose: for a public-competition "open data" variant of the knowledge base we may
only keep FACTS about each bean product (name, roaster, origin, process, roast
level, decaf status, flavor-note words, price, weight, url) -- never marketing
prose or full descriptions. Each site's raw HTML/JSON is cached to
``data/raw/roasters_kr/<site>/`` so re-running the collector does not refetch
pages that are already on disk, and robots.txt is (re-)checked on every request
by ``pipeline.http.PoliteClient``.

Site notes / scope decisions:
  * Fritz (fritz.co.kr), Namusairo (namusairo.com) and Coffee Libre
    (coffeelibre.kr) are Cafe24 shops with product pages carrying a
    JSON-LD ``Product``/``ProductGroup`` block plus a plain-text or
    ``<th>/<td>`` facts table.
  * 1kg Coffee (1kgcoffee.co.kr, Godo5) exposes origin/roast via ``<dt>/<dd>``
    summary rows, but process and flavor notes are only in marketing prose --
    those two fields are intentionally left ``None``/``[]`` for this site
    rather than scraping the paragraph.
  * Blue Bottle Korea (kr.bluebottlecoffee.com) is Shopify; ``/products.json``
    gives structured price/weight but origin/process/notes live inside
    ``body_html`` prose, so those facts are recovered with small, targeted
    regex/keyword scans (never by storing the paragraph itself).
  * Only actual bean products are kept. Drip bags, capsules, RTD/cold brew
    bottles, merch, tools, gift sets and bundles are skipped everywhere
    (``EXCLUDE_KEYWORDS`` / Shopify ``product_type``) -- they either lack
    single-product facts or mix multiple origins.
  * Terarosa is excluded entirely: its ``/market/`` path is disallowed by
    robots.txt.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from pipeline.http import RobotsDisallowed

# --------------------------------------------------------------------------- #
# Record model
# --------------------------------------------------------------------------- #


class BeanFactRecord(BaseModel):
    key: str
    site: str
    roaster: str
    name: str
    origin_country: str | None = None
    origin_region: str | None = None
    origin_farm: str | None = None
    process: str | None = None
    roast_level: str | None = None
    is_decaf: bool = False
    decaf_process: str | None = None
    flavor_notes: list[str] = Field(default_factory=list)
    price_krw: int | None = None
    weight_g: int | None = None
    product_url: str
    collected_at: str


def write_beans_jsonl(path: Path, records: list[BeanFactRecord]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")
    return len(records)


# --------------------------------------------------------------------------- #
# Shared fact-extraction helpers
# --------------------------------------------------------------------------- #

EXCLUDE_KEYWORDS = (
    "캡슐", "콜드브루", "드립백", "드립 백", "티백", "머그", "텀블러", "굿즈",
    "스티커", "도서", "기프트카드", "세트", "케이크", "커피용품", "시럽",
    "브루잉", "인스턴트", "쇼핑백", "여과지", "필터", "구독",
)

DECAF_KEYWORDS = ("디카페인", "디카프", "decaf")

COUNTRY_NAMES = (
    "에티오피아", "콜롬비아", "코스타리카", "과테말라", "니카라과", "온두라스",
    "인도네시아", "파푸아뉴기니", "엘살바도르", "엘 살바도르", "도미니카",
    "동티모르", "케냐", "브라질", "파나마", "예멘", "페루", "볼리비아",
    "르완다", "부룬디", "멕시코", "우간다", "말라위", "베트남", "자메이카",
    "하와이", "인도", "중국", "태국", "라오스", "잠비아", "탄자니아",
)

PROCESS_WORDS = (
    "무산소 내추럴", "펄프드 내추럴", "허니 프로세스", "세미 워시드",
    "무산소", "워시드", "내추럴", "허니",
)

ROAST_BRACKET_WORDS = ("중강배전", "중약배전", "약배전", "중배전", "강배전")

DECAF_METHOD_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"스위스\s*워터|swiss\s*water", "Swiss Water Process"),
    (r"슈가\s*케인|sugar\s*cane|sugarcane", "Sugarcane Process"),
    (r"마운틴\s*워터|mountain\s*water|\bmwp\b", "Mountain Water Process"),
    (r"이에이\s*프로세스|\be\.?a\.?\s*process\b", "EA Process"),
    (r"co2|이산화탄소", "CO2 Process"),
)

FLAVOR_LEXICON = (
    "시트러스", "캐러멜", "다크초콜릿", "블랙베리", "라즈베리", "히비스커스",
    "패션프룻", "초콜릿", "코코아", "바닐라", "헤이즐넛", "아몬드", "호두",
    "견과류", "무화과", "건포도", "살구", "복숭아", "청사과", "사과", "포도",
    "자두", "딸기", "베리", "오렌지", "레몬", "라임", "자몽", "플로럴",
    "자스민", "장미", "당밀", "메이플", "꿀",
)

_TERMINATORS = (
    "배송안내", "교환/반품", "교환 및 반품", "상품결제정보", "주 문 안 내",
    "수상경력", "상품상세정보",
)


def _nfc(text: str | None) -> str:
    """NFC-normalize before any Korean substring match. Some source pages
    (seen on Blue Bottle Korea) store a product's title in decomposed Hangul
    (NFD), which renders identically but silently fails every keyword match
    against our NFC literals -- and even a ``[가-힣]`` regex class, since
    decomposed jamo fall outside that codepoint block."""
    return unicodedata.normalize("NFC", text) if text else ""


def is_excluded(name: str | None) -> bool:
    if not name:
        return True
    name = _nfc(name)
    return any(kw in name for kw in EXCLUDE_KEYWORDS)


def is_decaf(*texts: str | None) -> bool:
    blob = _nfc(" ".join(t for t in texts if t))
    return any(re.search(re.escape(kw), blob, re.IGNORECASE) for kw in DECAF_KEYWORDS)


def find_country(text: str | None) -> str | None:
    """Return whichever known country name appears earliest in the text --
    titles like "니카라과 CoE 9위 핀카 리브레 에티오피아" name the origin
    country first and a coffee *variety* (e.g. the Ethiopian heirloom
    cultivar) later, so the earliest match -- not the longest name -- wins."""
    if not text:
        return None
    text = _nfc(text)
    best: tuple[int, str] | None = None
    for name in COUNTRY_NAMES:
        idx = text.find(name)
        if idx == -1:
            continue
        if best is None or idx < best[0] or (idx == best[0] and len(name) > len(best[1])):
            best = (idx, name)
    return best[1] if best else None


def find_process(text: str | None) -> str | None:
    if not text:
        return None
    compact = _nfc(text).replace(" ", "")
    for w in PROCESS_WORDS:
        if w.replace(" ", "") in compact:
            return w
    return None


def find_roast_from_brackets(text: str | None) -> str | None:
    if not text:
        return None
    text = _nfc(text)
    for m in re.finditer(r"\[([^\]]*)\]", text):
        content = m.group(1)
        for w in ROAST_BRACKET_WORDS:
            if w in content:
                return w
    return None


def find_decaf_method(text: str | None) -> str | None:
    if not text:
        return None
    text = _nfc(text)
    for pattern, canonical in DECAF_METHOD_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return canonical
    return None


def extract_flavor_words(text: str | None, limit: int = 8) -> list[str]:
    if not text:
        return []
    text = _nfc(text)
    found: list[str] = []
    for w in FLAVOR_LEXICON:
        if w in text and w not in found:
            found.append(w)
        if len(found) >= limit:
            break
    return found


def ko_prefix(s: str | None) -> str:
    """Return the leading run of Korean text (letters/commas/spaces) from a
    mixed Korean/English fact string such as ``"콜롬비아 Colombia"``. Falls
    back to the stripped original when it does not start with Korean text."""
    if not s:
        return ""
    s = _nfc(s).strip()
    m = re.match(r"^([가-힣][가-힣,\s]*)", s)
    return m.group(1).strip(" ,") if m else s


def split_ko_flavor_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    prefix = ko_prefix(raw)
    if not prefix:
        return []
    return [p.strip(" .") for p in re.split(r"[,·]", prefix) if p.strip(" .")]


def parse_price_krw(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"([\d][\d,]*)\s*원", text) or re.search(r"[\d][\d,]{2,}", text)
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(0))
    return int(digits) if digits else None


def parse_weight_g(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g)\b", text, re.IGNORECASE)
    if not m:
        return None
    value = float(m.group(1))
    grams = value * 1000 if m.group(2).lower() == "kg" else value
    return int(grams)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", _nfc(text).replace("\xa0", " ")).strip()


def clean_name(name: str | None) -> str | None:
    if not name:
        return None
    name = re.sub(r"<br\s*/?>", " ", name)
    return normalize_ws(html.unescape(name))


def ld_json_blocks(html_text: str) -> list[dict]:
    blocks = []
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.S):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict):
            blocks.append(d)
    return blocks


def first_product_ld(html_text: str) -> dict | None:
    for d in ld_json_blocks(html_text):
        if d.get("@type") in ("Product", "ProductGroup"):
            return d
    return None


def ld_name_desc_price(d: dict) -> tuple[str | None, str, float | None]:
    """Return (name, description, price) for a Product or ProductGroup node,
    using the first variant's description/price for a ProductGroup."""
    name = clean_name(d.get("name"))
    if d.get("@type") == "ProductGroup":
        variant = (d.get("hasVariant") or [{}])[0]
        desc = variant.get("description") or ""
        price = (variant.get("offers") or {}).get("price")
    else:
        desc = d.get("description") or ""
        price = (d.get("offers") or {}).get("price")
    return name, _nfc(desc), price


ROAST_ONLY_WORDS = set(ROAST_BRACKET_WORDS)


def flavor_notes_from_ld_description(desc: str | None) -> list[str]:
    """JSON-LD descriptions on these Cafe24 shops are either
    ``"Flavor Notes : <ko notes>\\n<en notes>"`` (single-origin products), a
    bare roast word, or -- for blends, which get no "Flavor Notes" line --
    a full marketing sentence ("...즐길 수 있는 풍부한 풍미를."). Only the
    first form is a short fact list; require either the explicit prefix or a
    comma-separated shape, and reject anything that reads as a sentence, so
    the latter is never stored."""
    if not desc:
        return []
    first_line = re.split(r"[\r\n]+", desc.strip())[0]
    had_prefix = bool(re.match(r"^Flavor\s*Notes?\s*:?\s*", first_line, flags=re.IGNORECASE))
    first_line = re.sub(r"^Flavor\s*Notes?\s*:?\s*", "", first_line, flags=re.IGNORECASE).strip()
    if not first_line or first_line in ROAST_ONLY_WORDS or "%" in first_line:
        return []
    if not had_prefix and "," not in first_line:
        return []  # no explicit note marker and nothing list-shaped -> marketing prose
    if re.search(r"(습니다|입니다|합니다|주세요|주시길|해보세요)\.?$", first_line):
        return []  # reads as a sentence, not a note list
    return split_ko_flavor_list(first_line)


def extract_labeled_fields(text: str, label_map: tuple[tuple[str, str], ...],
                           extra_markers: tuple[str, ...] = ()) -> dict[str, str]:
    """Scan free-form Korean product-fact text (already whitespace-normalized)
    for a fixed set of ``"<label> ... : <value>"`` runs, stopping each value
    at the next known label or a known boilerplate terminator (shipping /
    return-policy notices etc). Labels are matched in the order they occur;
    not every product carries every label. ``extra_markers`` are labels that
    exist on the page (e.g. "생산자", "품종") but are not collected -- they
    still need to act as boundaries so their text doesn't leak into the
    previous field's value."""
    labels = [lbl for lbl, _ in label_map]
    label_to_field = dict(label_map)
    markers = sorted(set(labels) | set(extra_markers) | set(_TERMINATORS), key=len, reverse=True)
    pattern = "|".join(re.escape(m) for m in markers)
    positions = [(m.start(), m.group()) for m in re.finditer(pattern, text)]
    result: dict[str, str] = {}
    for i, (pos, marker) in enumerate(positions):
        field = label_to_field.get(marker)
        if not field:
            continue
        start = pos + len(marker)
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        value = text[start:end]
        value = re.sub(r"^\s*(?:[A-Za-z][A-Za-z\s]*)?:\s*", "", value).strip(" :\xa0")
        if value and field not in result:
            result[field] = value
    return result


def label_value_map(soup: BeautifulSoup) -> dict[str, str]:
    """Collect ``<th>/<td>`` and ``<dt>/<dd>`` fact pairs, keyed by the label
    with internal whitespace stripped (so "지 역" and "지역" are the same
    key). First occurrence wins -- later duplicate labels are usually option
    selectors, not facts."""
    result: dict[str, str] = {}
    for th in soup.find_all("th"):
        td = th.find_next_sibling("td")
        if td is None:
            continue
        label = _nfc(re.sub(r"\s+", "", th.get_text(" ", strip=True)))
        value = _nfc(td.get_text(" ", strip=True))
        if label and value and label not in result:
            result[label] = value
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is None:
            continue
        label = _nfc(re.sub(r"\s+", "", dt.get_text(" ", strip=True)))
        value = _nfc(dd.get_text(" ", strip=True))
        if label and value and label not in result:
            result[label] = value
    return result


def fetch_cached(http, url: str, cache_dir: Path, key: str) -> str | None:
    """GET url, caching the raw text under cache_dir/key so a rerun does not
    refetch it. Returns None (and leaves no cache file) when robots.txt
    disallows the URL."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / key
    if path.exists():
        return path.read_text(encoding="utf-8")
    try:
        text = http.get(url).text
    except RobotsDisallowed:
        return None
    path.write_text(text, encoding="utf-8")
    return text


# --------------------------------------------------------------------------- #
# Fritz (fritz.co.kr) -- Cafe24
# --------------------------------------------------------------------------- #

FRITZ_LABELS = (
    ("국가", "country"), ("지역", "region"), ("생산자", "farm"),
    ("농장", "farm"), ("품종", "variety"), ("가공방식", "process_raw"),
)


def parse_fritz_product(html_text: str, url: str) -> dict | None:
    ld = first_product_ld(html_text)
    if ld is None:
        return None
    name, desc, price = ld_name_desc_price(ld)
    if is_excluded(name):
        return None
    soup = BeautifulSoup(html_text, "lxml")
    text = normalize_ws(soup.get_text(" "))
    fields = extract_labeled_fields(text, FRITZ_LABELS)
    process_raw = fields.get("process_raw", "")
    decaf = is_decaf(name, process_raw)
    return {
        "name": name,
        "origin_country": ko_prefix(fields.get("country")) or None,
        "origin_region": ko_prefix(fields.get("region")) or None,
        "origin_farm": ko_prefix(fields.get("farm")) or None,
        "process": find_process(process_raw),
        "roast_level": None,
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(text) if decaf else None,
        "flavor_notes": flavor_notes_from_ld_description(desc),
        "price_krw": int(price) if price is not None else None,
        "weight_g": parse_weight_g(name) or parse_weight_g(text),
        "product_url": url,
    }


@dataclass
class FritzCollector:
    name: str = "fritz"
    base_url: str = "https://www.fritz.co.kr"
    roaster: str = "프릳츠"
    categories: tuple[str, ...] = ("24", "25", "117")  # 싱글오리진, 블렌드, 디카페인

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        ids: dict[str, None] = {}
        for cate in self.categories:
            listing = fetch_cached(http, f"{self.base_url}/product/list.html?cate_no={cate}",
                                    cache_dir, f"list_{cate}.html")
            if not listing:
                continue
            for pid in re.findall(r"/product/detail\.html\?product_no=(\d+)", listing):
                ids.setdefault(pid, None)
        records = []
        for pid in ids:
            url = f"{self.base_url}/product/detail.html?product_no={pid}"
            page = fetch_cached(http, url, cache_dir, f"product_{pid}.html")
            if not page:
                continue
            facts = parse_fritz_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{pid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# Namusairo (namusairo.com) -- Cafe24
# --------------------------------------------------------------------------- #


def parse_namusairo_product(html_text: str, url: str) -> dict | None:
    m = re.search(r"<title>(.*?)</title>", html_text, re.S)
    title = clean_name(m.group(1).split(" - ")[0]) if m else None
    if is_excluded(title):
        return None
    soup = BeautifulSoup(html_text, "lxml")
    facts = label_value_map(soup)
    name = clean_name(facts.get("상품명")) or title
    process_raw = facts.get("가공법", "")
    decaf = is_decaf(title, process_raw)
    return {
        "name": name,
        "origin_country": (facts.get("원산지") or "").strip() or None,
        "origin_region": ko_prefix(facts.get("지역")) or None,
        "origin_farm": ko_prefix(facts.get("가공소")) or None,
        "process": find_process(process_raw),
        "roast_level": facts.get("볶음도") or None,
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(process_raw) if decaf else None,
        "flavor_notes": split_ko_flavor_list(facts.get("향미")),
        "price_krw": parse_price_krw(facts.get("판매가")),
        "weight_g": parse_weight_g(facts.get("용량")),
        "product_url": url,
    }


@dataclass
class NamusairoCollector:
    name: str = "namusairo"
    base_url: str = "https://namusairo.com"
    roaster: str = "나무사이로"
    category_path: str = "/category/coffee/91/"

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        listing = fetch_cached(http, f"{self.base_url}{self.category_path}", cache_dir, "list_coffee.html")
        if not listing:
            return []
        seen: dict[str, str] = {}
        for m in re.finditer(r'href="(/product/[^"?]+/(\d+)/category/\d+[^"]*)"', listing):
            seen.setdefault(m.group(2), m.group(1))
        records = []
        for pid, rel_url in seen.items():
            url = f"{self.base_url}{rel_url}"
            page = fetch_cached(http, url, cache_dir, f"product_{pid}.html")
            if not page:
                continue
            facts = parse_namusairo_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{pid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# Coffee Libre (coffeelibre.kr) -- Cafe24
# --------------------------------------------------------------------------- #

LIBRE_LABELS = (("농장명", "farm"), ("지역", "region"), ("가공방식", "process_raw"))


def parse_libre_product(html_text: str, url: str) -> dict | None:
    ld = first_product_ld(html_text)
    if ld is None:
        return None
    name, desc, price = ld_name_desc_price(ld)
    if is_excluded(name):
        return None
    soup = BeautifulSoup(html_text, "lxml")
    text = normalize_ws(soup.get_text(" "))
    fields = extract_labeled_fields(text, LIBRE_LABELS, extra_markers=("생산자", "재배고도", "품종"))
    facts = label_value_map(soup)
    process_raw = fields.get("process_raw", "")
    decaf = is_decaf(name)
    roast = find_roast_from_brackets(name)
    return {
        "name": name,
        # Country is read from the title only (Coffee Libre's single-origin
        # naming convention puts it there, e.g. "[싱글오리진] 온두라스 ...");
        # scanning the whole page risks matching an unrelated country named
        # in a "customers also bought" widget or ingredient note elsewhere
        # on a blend's page.
        "origin_country": find_country(name),
        "origin_region": ko_prefix(fields.get("region")) or None,
        "origin_farm": ko_prefix(fields.get("farm")) or None,
        "process": find_process(process_raw),
        "roast_level": roast,
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(text) if decaf else None,
        "flavor_notes": flavor_notes_from_ld_description(desc),
        "price_krw": int(price) if price is not None else None,
        "weight_g": parse_weight_g(facts.get("옵션", "")) or parse_weight_g(name),
        "product_url": url,
    }


@dataclass
class CoffeeLibreCollector:
    name: str = "coffeelibre"
    base_url: str = "https://coffeelibre.kr"
    roaster: str = "커피 리브레"
    category: str = "47"  # 원두

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        listing = fetch_cached(http, f"{self.base_url}/product/list.html?cate_no={self.category}",
                                cache_dir, f"list_{self.category}.html")
        if not listing:
            return []
        ids = dict.fromkeys(re.findall(rf"product_no=(\d+)&cate_no={self.category}", listing))
        records = []
        for pid in ids:
            url = f"{self.base_url}/product/detail.html?product_no={pid}&cate_no={self.category}&display_group=1"
            page = fetch_cached(http, url, cache_dir, f"product_{pid}.html")
            if not page:
                continue
            facts = parse_libre_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{pid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# 1kg Coffee (1kgcoffee.co.kr) -- Godo5
# --------------------------------------------------------------------------- #


def parse_onekg_product(html_text: str, url: str) -> dict | None:
    m = re.search(r"<title>(.*?)</title>", html_text, re.S)
    title = clean_name(m.group(1).split(" | ")[0]) if m else None
    if is_excluded(title):
        return None
    soup = BeautifulSoup(html_text, "lxml")
    facts = label_value_map(soup)
    origin_raw = (facts.get("원산지") or "").strip()
    if origin_raw.endswith("산") and len(origin_raw) > 1:
        origin_raw = origin_raw[:-1]
    decaf = is_decaf(title)
    full_text = normalize_ws(soup.get_text(" "))
    return {
        "name": title,
        "origin_country": origin_raw or find_country(title),
        "origin_region": None,
        "origin_farm": None,
        # 1kg Coffee only exposes process/flavor-notes inside marketing prose,
        # not a structured field -- left unset rather than scraping the paragraph.
        "process": find_process(title),
        "roast_level": facts.get("배전도(볶음도)") or facts.get("볶음도") or None,
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(title + " " + full_text) if decaf else None,
        "flavor_notes": [],
        "price_krw": parse_price_krw(facts.get("판매가")),
        # The legal-disclosure "용량(중량), 수량" row is usually just
        # "제품 상세페이지 참조" ("see product page") with no digits, so try
        # each candidate's *parsed* weight rather than the first non-empty
        # raw string -- the option-selector "용량" row (e.g. "100g 334g 1kg")
        # is where the real number lives.
        "weight_g": (parse_weight_g(facts.get("용량(중량),수량"))
                     or parse_weight_g(facts.get("용량"))
                     or parse_weight_g(title)),
        "product_url": url,
    }


@dataclass
class OnekgCoffeeCollector:
    name: str = "onekgcoffee"
    base_url: str = "https://www.1kgcoffee.co.kr"
    roaster: str = "1킬로커피"
    categories: tuple[str, ...] = ("034001", "034002", "034003")  # 싱글오리진, 블렌드, 디카페인

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        ids: dict[str, None] = {}
        for cate in self.categories:
            listing = fetch_cached(http, f"{self.base_url}/goods/goods_list.php?cateCd={cate}",
                                    cache_dir, f"list_{cate}.html")
            if not listing:
                continue
            for gid in re.findall(r"goods_view\.php\?goodsNo=(\d+)", listing):
                ids.setdefault(gid, None)
        records = []
        for gid in ids:
            url = f"{self.base_url}/goods/goods_view.php?goodsNo={gid}"
            page = fetch_cached(http, url, cache_dir, f"product_{gid}.html")
            if not page:
                continue
            facts = parse_onekg_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{gid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# Blue Bottle Korea (kr.bluebottlecoffee.com) -- Shopify
# --------------------------------------------------------------------------- #


def parse_bluebottle_product(product: dict, base_url: str) -> dict | None:
    if product.get("product_type") != "원두":
        return None
    name = clean_name(product.get("title"))
    if is_excluded(name):
        return None
    variant = (product.get("variants") or [{}])[0]
    body_text = normalize_ws(BeautifulSoup(product.get("body_html") or "", "lxml").get_text(" "))
    title_and_body = f"{name} {body_text}"
    decaf = is_decaf(name, " ".join(product.get("tags") or []))
    price = variant.get("price")
    return {
        "name": name,
        "origin_country": find_country(name),
        "origin_region": None,
        "origin_farm": None,
        "process": find_process(name) or find_process(body_text),
        "roast_level": None,  # only ever appears mixed into blend-ratio prose; not a reliable single fact
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(title_and_body) if decaf else None,
        "flavor_notes": extract_flavor_words(body_text),
        "price_krw": int(float(price)) if price is not None else None,
        "weight_g": parse_weight_g(variant.get("title") or "") or parse_weight_g(name),
        "product_url": f"{base_url}/products/{product.get('handle')}",
    }


@dataclass
class BlueBottleCollector:
    name: str = "bluebottle"
    base_url: str = "https://kr.bluebottlecoffee.com"
    roaster: str = "블루보틀"
    max_pages: int = 10

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        records = []
        seen_handles: set[str] = set()
        for page in range(1, self.max_pages + 1):
            raw = fetch_cached(http, f"{self.base_url}/products.json?limit=250&page={page}",
                                cache_dir, f"products_page{page}.json")
            if not raw:
                break
            try:
                products = json.loads(raw).get("products", [])
            except ValueError:
                break
            if not products:
                break
            for p in products:
                handle = p.get("handle")
                if not handle or handle in seen_handles:
                    continue
                seen_handles.add(handle)
                facts = parse_bluebottle_product(p, self.base_url)
                if facts is None:
                    continue
                records.append(BeanFactRecord(
                    key=f"{self.name}:{handle}", site=self.name, roaster=self.roaster,
                    collected_at=collected_at, **facts,
                ))
        return records


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

ALL_ROASTER_COLLECTORS = [
    FritzCollector(), NamusairoCollector(), CoffeeLibreCollector(),
    OnekgCoffeeCollector(), BlueBottleCollector(),
]


def _site_stats(records: list[BeanFactRecord]) -> dict[str, Any]:
    n = len(records)
    def pct(pred):
        return round(100 * sum(1 for r in records if pred(r)) / n, 1) if n else 0.0
    return {
        "count": n,
        "decaf": sum(1 for r in records if r.is_decaf),
        "pct_with_origin": pct(lambda r: bool(r.origin_country)),
        "pct_with_process": pct(lambda r: bool(r.process)),
        "pct_with_notes": pct(lambda r: bool(r.flavor_notes)),
    }


def run_roasters_kr_collect(http, raw_root: Path, collected_at: str,
                             collectors=None) -> tuple[dict[str, Any], Path]:
    """Run every roasters_kr site collector, merge their facts into
    ``<raw_root>/roasters_kr/beans.jsonl`` and return (per-site stats, path)."""
    collectors = collectors if collectors is not None else ALL_ROASTER_COLLECTORS
    roasters_root = raw_root / "roasters_kr"
    all_records: list[BeanFactRecord] = []
    stats: dict[str, Any] = {}
    for c in collectors:
        cache_dir = roasters_root / c.name
        try:
            records = c.collect(http, cache_dir, collected_at)
        except Exception as e:  # one broken source must not stop the others
            stats[c.name] = {"error": f"{type(e).__name__}: {e}"}
            continue
        all_records.extend(records)
        stats[c.name] = _site_stats(records)
    out_path = roasters_root / "beans.jsonl"
    write_beans_jsonl(out_path, all_records)
    stats["total"] = _site_stats(all_records)
    return stats, out_path
