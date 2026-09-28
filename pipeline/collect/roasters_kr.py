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
  * Anthracite (anthracitecoffee.com), Momos (momos.co.kr) and Deca Coffee
    Lab (decacoffeelab.com, checked but not collected -- see below) run on
    imweb, whose product pages single-quote their ``ld+json`` script tag
    (``ld_json_blocks`` accepts either quote style). Anthracite's facts live
    in a plain ``지역 : ... 가공방식 : ...`` paragraph (parsed with the same
    ``extract_labeled_fields`` used for Fritz/Coffee Libre); Momos hides a
    legal "상품 정보고시" disclosure table (``<td>/<td>`` pairs, not
    ``<th>/<td>``) that happens to carry a real "노트" (flavor notes) row.
  * Felt (feltcoffee.com) and Manufact (manufactcoffee.com) are Cafe24 shops
    like Fritz/Namusairo/Coffee Libre. Felt keys its facts table "Name /
    Notice / Coffee / Description / Price" (English labels); roast level is
    parsed out of the "Notice" cell. Manufact packs region, flavor notes and
    roast level into one "원두 정보" cell separated by ``<br>`` -- each line
    is classified by content (contains a known country name -> region line;
    contains "로스트" -> roast line; otherwise -> notes line) since blends
    only carry two of the three lines.
  * Bean Brothers (beanbrothers.co.kr) is a Godo5 shop (like 1kg Coffee) but
    exposes a genuine "FACT SHEET" block of ``<p><span>label</span>
    <span>value</span></p>`` rows (parsed by ``bean_brothers_fact_sheet``);
    blends only carry a "블렌드 구성"/"로스팅" pair, so ``origin_country`` is
    left ``None`` for them, same as elsewhere in this module.
  * Taste-intensity gauges (``gauge_*`` fields, docs/adr/0011-roaster-gauges-feature-model.md): a
    roaster's own published 산미/바디/단맛(/쓴맛) gauge is a human label, so it is kept as a fact when the
    page shows it as markup or text (never read from an image). Surveyed 2026-09: Coffee Libre (5 dots,
    half dots, acidity+sweetness), 1kg Coffee ("SENSORY CHART" 5-segment bars in markup: acidity,
    sweetness, bitterness, body), G Roasting (groasting.com, Cafe24; "산미 4.5│바디감 2│단맛 3" numbers in
    og:description) and Naeil Coffee (naeilcoffee.co.kr, imweb; "Acidity ●●●◐○ / Body ●●●○○") carry
    one. Fritz, Namusairo, Blue Bottle, Anthracite, Felt, Bean Brothers, Momos and Manufact show none
    (prose or images only). Every gauge seen is a 0-5 scale in half steps and is mapped onto our 1-5
    scale by ``normalize_gauge`` (``gauge_scale`` names the site's scale on each record). Also sampled
    for gauges and skipped (no text/markup gauge on their product pages): Brown Cherry, Coffeelec,
    Roasting Tiger, Wondoo Banjeom, Wannabean, Pourr, Coffee Gdero.
  * Deca Coffee Lab, Center Coffee, Hell Cafe, Leesar, Lowkey Coffee, Coffee
    Montage and three guessed domains (Mesh Coffee, Pastel Coffee Works,
    Coffee Graffiti) were evaluated and skipped -- see the collector
    docstrings below and the roasters-kr README/data-recipe notes for why.
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
from pipeline.rules import parse_altitude_m  # noqa: F401 (re-exported for tests)

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
    # Optional structured facts some shops label explicitly (never parsed out of prose).
    altitude_m: int | None = None          # midpoint of a labelled "재배고도" range, metres
    variety: str | None = None             # labelled "품종" value, as written by the shop
    # Roaster-published taste-intensity gauges (dots / bars / "산미 4.5" numbers on the product page),
    # normalised to our 1-5 scale by ``normalize_gauge``; ``gauge_scale`` records the site's own scale so
    # the mapping stays auditable. None = the page shows no HTML/text gauge for that attribute (image-only
    # gauges are never read).
    gauge_acidity: float | None = None
    gauge_body: float | None = None
    gauge_sweetness: float | None = None
    gauge_bitterness: float | None = None
    gauge_scale: str | None = None
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
    "스티커", "도서", "기프트", "세트", "케이크", "커피용품", "시럽",
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
    (r"이에이\s*프로세스|에틸\s*아세테이트|\be\.?a\.?\s*process\b|ethyl\s*acetate", "EA Process"),
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


MAX_NOTE_WORD_LEN = 16


def split_note_list(raw: str | None, limit: int = 8) -> list[str]:
    """Split an already-isolated "note"-style fact-table value (comma or
    middle-dot separated, Korean or English) into individual words. Unlike
    ``split_ko_flavor_list`` this does not require the value to start with
    Korean text -- it is meant for text already pulled out of a specific
    labelled field rather than a generic free-form description.

    Some sites (seen on Anthracite/Momos blends) reuse the same "노트" label
    for a marketing tagline instead of a word list on some products, and a
    stray comma inside an ordinary Korean sentence still splits into
    several long fragments -- so any split producing a fragment longer than
    ``MAX_NOTE_WORD_LEN`` is treated as prose and rejected wholesale rather
    than partially stored."""
    if not raw:
        return []
    parts = [p.strip(" .") for p in re.split(r"[,·ㆍ]", _nfc(raw)) if p.strip(" .")]
    if any(len(p) > MAX_NOTE_WORD_LEN for p in parts):
        return []
    return parts[:limit]


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
    # Cafe24 shops always double-quote the type attribute; imweb shops
    # (Anthracite, Momos, ...) single-quote it -- accept either.
    for raw in re.findall(r'''<script type=["']application/ld\+json["']>(.*?)</script>''', html_text, re.S):
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
# Taste-intensity gauges and labelled altitude
# --------------------------------------------------------------------------- #

GAUGE_ATTRS = ("acidity", "body", "sweetness", "bitterness")
GAUGE_LABELS: dict[str, str] = {
    "산미": "acidity", "신맛": "acidity", "acidity": "acidity",
    "바디감": "body", "바디": "body", "body": "body",
    "단맛": "sweetness", "sweetness": "sweetness",
    "쓴맛": "bitterness", "bitterness": "bitterness",
}
_GAUGE_WORD = r"산미|신맛|바디감|바디|단맛|쓴맛|acidity|body|sweetness|bitterness"
# "산미 Acidity ●●●◐○" / "신맛 ●●○○○" / "Acidity ●●●●○ / Body ●●○○○" -- exactly five dots; a run of any
# other length (seen once: a six-dot typo on a drip-bag page) is rejected rather than guessed.
_DOT_GAUGE = re.compile(
    rf"({_GAUGE_WORD})\s*(?:acidity|body|sweetness|bitterness)?\s*:?\s*(?<![●◐○])([●◐○]{{5}})(?![●◐○])",
    re.IGNORECASE)
# "산미 4.5│향미 5│균형 2│바디감 2│단맛 3" (G Roasting's og:description) -- a 0-5 number after the word.
_NUM_GAUGE = re.compile(rf"({_GAUGE_WORD})\s*:?\s*([0-5](?:\.\d)?)(?![\d.])", re.IGNORECASE)


def normalize_gauge(value: float, scale_max: float = 5.0) -> float:
    """Map a site's 0..scale_max gauge reading onto our 1-5 scale: rescale to fifths, then clamp to
    [1, 5]. Every site collected so far is a five-step gauge where one filled step already means
    "low", so 1 step -> 1, 3 -> 3, 5 -> 5 and only a lone half step (0.5) is lifted to the floor of 1."""
    v = value * 5.0 / scale_max
    return round(min(5.0, max(1.0, v)), 2)


def gauge_fields(raw: dict[str, float], scale: str, scale_max: float = 5.0) -> dict[str, Any]:
    """{attr: site value} -> BeanFactRecord gauge_* kwargs (normalised) plus gauge_scale; {} when empty."""
    if not raw:
        return {}
    out: dict[str, Any] = {f"gauge_{a}": normalize_gauge(v, scale_max) for a, v in raw.items() if a in GAUGE_ATTRS}
    out["gauge_scale"] = scale
    return out


def dot_gauges(text: str | None) -> dict[str, float]:
    """Five-dot gauges (● full = 1, ◐ half = 0.5, ○ empty = 0) keyed by attribute; first hit per attribute."""
    out: dict[str, float] = {}
    for m in _DOT_GAUGE.finditer(_nfc(text or "")):
        attr = GAUGE_LABELS[m.group(1).lower()]
        if attr not in out:
            out[attr] = m.group(2).count("●") + 0.5 * m.group(2).count("◐")
    return out


def number_gauges(text: str | None) -> dict[str, float]:
    """"산미 4.5│바디감 2│단맛 3"-style numeric 0-5 gauges keyed by attribute; first hit per attribute."""
    out: dict[str, float] = {}
    for m in _NUM_GAUGE.finditer(_nfc(text or "")):
        attr = GAUGE_LABELS[m.group(1).lower()]
        if attr not in out:
            out[attr] = float(m.group(2))
    return out


def fact_value(value: str | None, max_len: int = 30) -> str | None:
    """A labelled fact value, or None when the label matched inside a sentence instead ("...엄선한 품종과
    품질의 커피로 구성합니다" is not a variety): too long, or ending like a Korean sentence."""
    value = (value or "").strip(" .,:")
    if not value or len(value) > max_len or re.search(r"(니다|세요|어요|아요)$", value):
        return None
    return value


def find_roast_word(text: str | None) -> str | None:
    """First Korean roast-degree word anywhere in the text (G Roasting puts it bare in the title,
    "약배전 산미높은 ..."); ``ROAST_BRACKET_WORDS`` lists 중강/중약 before 강/약 so the longer word wins."""
    text = _nfc(text or "")
    return next((w for w in ROAST_BRACKET_WORDS if w in text), None)


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
        "variety": fact_value(ko_prefix(fields.get("variety"))),
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

LIBRE_LABELS = (("농장명", "farm"), ("지역", "region"), ("가공방식", "process_raw"),
                ("재배고도", "altitude_raw"), ("품종", "variety"))
LIBRE_GAUGE_SCALE = "coffeelibre: 5 dots, half-step (●=1, ◐=0.5)"


def parse_libre_product(html_text: str, url: str) -> dict | None:
    ld = first_product_ld(html_text)
    if ld is None:
        return None
    name, desc, price = ld_name_desc_price(ld)
    if is_excluded(name):
        return None
    soup = BeautifulSoup(html_text, "lxml")
    text = normalize_ws(soup.get_text(" "))
    fields = extract_labeled_fields(text, LIBRE_LABELS, extra_markers=("생산자", "산미", "신맛", "단맛"))
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
        "altitude_m": parse_altitude_m(fields.get("altitude_raw")),
        "variety": fact_value(ko_prefix(fields.get("variety"))),
        # "산미 Acidity ●●●◐○ 단맛 Sweetness ●●●○○" (or "신맛 ●●○○○") in the product summary: five dots,
        # half dots allowed; acidity and sweetness only (this site shows no body gauge).
        **gauge_fields(dot_gauges(text), LIBRE_GAUGE_SCALE),
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


ONEKG_SENSORY_SCALE = "onekgcoffee: SENSORY CHART 5-segment bar, half-step (fill=1, half=0.5)"


def onekg_sensory_gauges(soup: BeautifulSoup) -> dict[str, float]:
    """The "감각 표현 차트 / SENSORY CHART" block: one ``kg-sensory-row`` per attribute (신맛/단맛/쓴맛/
    바디감) whose bar is five ``<span>`` segments classed ``kg-sensory-fill`` / ``-half`` / ``-empty``. The
    value is read from the markup classes, not from rendered colours; a bar that isn't five segments is
    skipped."""
    out: dict[str, float] = {}
    for row in soup.select("div.kg-sensory-row"):
        label_el = row.select_one(".kg-sensory-label")
        attr = GAUGE_LABELS.get(_nfc(label_el.get_text(strip=True)).lower()) if label_el else None
        spans = row.select(".kg-sensory-bar > span")
        if attr is None or attr in out or len(spans) != 5:
            continue
        classes = [" ".join(sp.get("class") or []) for sp in spans]
        out[attr] = sum(1.0 if "kg-sensory-fill" in c else 0.5 if "kg-sensory-half" in c else 0.0
                        for c in classes)
    return out


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
        **gauge_fields(onekg_sensory_gauges(soup), ONEKG_SENSORY_SCALE),
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
# Anthracite (anthracitecoffee.com) -- imweb
# --------------------------------------------------------------------------- #

ANTHRACITE_LABELS = (
    ("지역", "region"), ("로스팅레벨", "roast"), ("로스팅 레벨", "roast"),
    ("가공방식", "process_raw"), ("플레이버", "notes_flavor"), ("노트", "notes_raw"),
    ("중량", "weight_raw"),
)
ANTHRACITE_EXTRA_MARKERS = ("품종", "고도", "등급", "구성")


def parse_anthracite_product(html_text: str, url: str) -> dict | None:
    ld = first_product_ld(html_text)
    if ld is None:
        return None
    name, _desc, price = ld_name_desc_price(ld)
    if is_excluded(name):
        return None
    soup = BeautifulSoup(html_text, "lxml")
    summary = soup.find("div", class_="goods_summary")
    text = normalize_ws(summary.get_text(" ")) if summary else ""
    fields = extract_labeled_fields(text, ANTHRACITE_LABELS, extra_markers=ANTHRACITE_EXTRA_MARKERS)
    process_raw = fields.get("process_raw")
    decaf = is_decaf(name)
    # Blends carry both a "플레이버" word list (e.g. "볶은 견과, 스파이시,
    # 다크 초콜렛") and a separate "노트" marketing tagline sentence; single
    # origins only ever have "노트", and it is a clean word list there --
    # prefer 플레이버 when present, since 노트 is the one that turns into
    # prose on blends (``split_note_list`` also rejects prose defensively).
    flavor_notes = split_note_list(fields.get("notes_flavor")) or split_note_list(fields.get("notes_raw"))
    return {
        "name": name,
        "origin_country": find_country(name),
        "origin_region": fields.get("region") or None,
        "origin_farm": None,  # never exposed as its own field on this site
        # Process is stated in English ("Washed", "Double Anaerobic
        # Fermentation/Thermel Shock") rather than one of our Korean
        # PROCESS_WORDS -- store the site's own short value rather than
        # losing it to a canonical-word lookup that can only match Korean.
        "process": process_raw or None,
        "roast_level": fields.get("roast") or None,
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(process_raw) if decaf else None,
        "flavor_notes": flavor_notes,
        "price_krw": int(price) if price is not None else None,
        "weight_g": parse_weight_g(fields.get("weight_raw")) or parse_weight_g(name),
        "product_url": url,
    }


@dataclass
class AnthraciteCollector:
    name: str = "anthracite"
    base_url: str = "https://anthracitecoffee.com"
    roaster: str = "앤트러사이트"

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        sitemap = fetch_cached(http, f"{self.base_url}/sitemap.xml", cache_dir, "sitemap.xml")
        if not sitemap:
            return []
        ids = dict.fromkeys(re.findall(r"/shop_view/(\d+)", sitemap))
        records = []
        for pid in ids:
            url = f"{self.base_url}/shop_view/?idx={pid}"
            page = fetch_cached(http, url, cache_dir, f"product_{pid}.html")
            if not page:
                continue
            facts = parse_anthracite_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{pid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# Felt (feltcoffee.com) -- Cafe24
# --------------------------------------------------------------------------- #


def parse_felt_product(html_text: str, url: str) -> dict | None:
    ld = first_product_ld(html_text)
    if ld is None:
        return None
    name, desc, price = ld_name_desc_price(ld)
    if is_excluded(name):
        return None
    soup = BeautifulSoup(html_text, "lxml")
    facts = label_value_map(soup)
    notice = facts.get("Notice", "")
    coffee_raw = facts.get("Coffee", "")
    roast_m = re.search(r"ROASTING\s*LEVEL\s*:\s*([^/\n]+)", notice, re.IGNORECASE)
    decaf = is_decaf(name)
    variant = (ld.get("hasVariant") or [{}])[0] if ld.get("@type") == "ProductGroup" else ld
    return {
        "name": name,
        "origin_country": find_country(name),
        "origin_region": None,  # only ever spelled out in the English "Coffee" prose line
        "origin_farm": None,
        "process": find_process(name),
        "roast_level": normalize_ws(roast_m.group(1)) if roast_m else None,
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(coffee_raw) if decaf else None,
        "flavor_notes": split_note_list(desc or facts.get("Description")),
        "price_krw": int(price) if price is not None else None,
        "weight_g": parse_weight_g(variant.get("name") or "") or parse_weight_g(name),
        "product_url": url,
    }


@dataclass
class FeltCollector:
    name: str = "felt"
    base_url: str = "https://feltcoffee.com"
    roaster: str = "펠트"
    category: str = "30"  # COFFEE

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        listing = fetch_cached(http, f"{self.base_url}/product/list.html?cate_no={self.category}",
                                cache_dir, f"list_{self.category}.html")
        if not listing:
            return []
        seen: dict[str, str] = {}
        for m in re.finditer(rf'href="(/product/[^"?]+/(\d+)/category/{self.category}[^"]*)"', listing):
            seen.setdefault(m.group(2), m.group(1))
        records = []
        for pid, rel_url in seen.items():
            url = f"{self.base_url}{rel_url}"
            page = fetch_cached(http, url, cache_dir, f"product_{pid}.html")
            if not page:
                continue
            facts = parse_felt_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{pid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# Bean Brothers (beanbrothers.co.kr) -- Godo5
# --------------------------------------------------------------------------- #


def bean_brothers_fact_sheet(soup: BeautifulSoup) -> dict[str, str]:
    """Parse the "FACT SHEET" block of ``<p><span>label</span>
    <span>value</span></p>`` rows. A label-less row (empty first span)
    continues the previous label's value -- used for multi-line blend
    composition ("블렌드 구성" listing each origin/percentage on its own
    row). Label/value text is inconsistently punctuated across products
    (some end the label with ":", some start the value with ":") so both
    are stripped of a leading/trailing colon."""
    h1 = soup.find(lambda t: t.name == "h1" and t.get_text(strip=True) == "FACT SHEET")
    if h1 is None:
        return {}
    container = h1.find_parent("div")
    if container is None:
        return {}
    collected: dict[str, list[str]] = {}
    current: str | None = None
    for p in container.find_all("p", recursive=False):
        spans = p.find_all("span")
        if len(spans) < 2:
            continue
        label = _nfc(spans[0].get_text(strip=True)).strip(" :：")
        value = _nfc(spans[1].get_text(strip=True)).strip(" :：")
        if label:
            current = label
            collected.setdefault(current, [])
            if value:
                collected[current].append(value)
        elif current and value:
            collected[current].append(value)
    return {k: ", ".join(v) for k, v in collected.items() if v}


def parse_beanbrothers_product(html_text: str, url: str) -> dict | None:
    m = re.search(r'<meta property="og:title" content="([^"]*)"', html_text)
    name = clean_name(m.group(1)) if m else None
    if is_excluded(name):
        return None
    soup = BeautifulSoup(html_text, "lxml")
    fact_sheet = bean_brothers_fact_sheet(soup)
    region = fact_sheet.get("지역")
    process_raw = fact_sheet.get("가공") or fact_sheet.get("가공방식")
    decaf = is_decaf(name)
    price_el = soup.select_one("p.price")
    price_text = price_el.get_text(" ", strip=True) if price_el else None
    weight_m = re.search(r'<option\s+value="(\d+(?:\.\d+)?(?:g|kg))"', html_text)
    return {
        "name": name,
        "origin_country": find_country(name) or find_country(region),
        "origin_region": region,
        "origin_farm": fact_sheet.get("농장"),
        "process": process_raw,
        "roast_level": fact_sheet.get("로스팅"),
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(process_raw) if decaf else None,
        "flavor_notes": split_note_list(fact_sheet.get("테이스팅 노트")),
        "price_krw": parse_price_krw(price_text),
        "weight_g": parse_weight_g(weight_m.group(1)) if weight_m else None,
        "product_url": url,
    }


@dataclass
class BeanBrothersCollector:
    name: str = "beanbrothers"
    base_url: str = "https://beanbrothers.co.kr"
    roaster: str = "빈브라더스"
    categories: tuple[str, ...] = ("007001001", "007001002")  # 블렌드, 싱글오리진

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        ids: dict[str, None] = {}
        for cate in self.categories:
            listing = fetch_cached(http, f"{self.base_url}/goods/goods_list.php?cateCd={cate}&sort=date&pageNum=100",
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
            facts = parse_beanbrothers_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{gid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# Momos (momos.co.kr) -- imweb
# --------------------------------------------------------------------------- #


def momos_disclosure_map(soup: BeautifulSoup) -> dict[str, str]:
    """The legally-required "상품 정보고시" (product info disclosure) table
    is plain ``<td>/<td>`` pairs (no ``<th>``), and happens to carry a real
    "노트" (flavor notes) row alongside the food-labelling boilerplate."""
    div = soup.find("div", class_="tms-product-tab-desc")
    if div is None:
        return {}
    table = div.find("table")
    if table is None:
        return {}
    result: dict[str, str] = {}
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 2:
            continue
        label = _nfc(re.sub(r"\s+", "", tds[0].get_text(" ", strip=True)))
        value = _nfc(tds[1].get_text(" ", strip=True))
        if label and value and label not in result:
            result[label] = value
    return result


def parse_momos_product(html_text: str, url: str) -> dict | None:
    ld = first_product_ld(html_text)
    if ld is None:
        return None
    name, _desc, price = ld_name_desc_price(ld)
    if is_excluded(name):
        return None
    soup = BeautifulSoup(html_text, "lxml")
    disclosure = momos_disclosure_map(soup)
    # The short "향미노트" summary card (``.tms-product-info``) is always a
    # clean word list; the legal disclosure table's "노트" row repeats that
    # list for single origins but is sometimes rewritten into a marketing
    # sentence for blends, so prefer the summary card and only fall back to
    # the disclosure row (``split_note_list`` rejects it if still prose).
    summary_notes_el = soup.select_one("div.tms-product-info td")
    summary_notes = summary_notes_el.get_text(" ", strip=True) if summary_notes_el else None
    flavor_notes = split_note_list(summary_notes) or split_note_list(disclosure.get("노트"))
    decaf = is_decaf(name)
    return {
        "name": name,
        "origin_country": find_country(name),
        "origin_region": None,
        "origin_farm": None,
        "process": find_process(name),
        "roast_level": None,  # not exposed as its own fact on this site
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(name) if decaf else None,
        "flavor_notes": flavor_notes,
        "price_krw": int(price) if price is not None else None,
        "weight_g": parse_weight_g(disclosure.get("내용량")) or parse_weight_g(name),
        "product_url": url,
    }


@dataclass
class MomosCollector:
    name: str = "momos"
    base_url: str = "https://momos.co.kr"
    roaster: str = "모모스커피"

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        listing = fetch_cached(http, f"{self.base_url}/shop", cache_dir, "list_shop.html")
        if not listing:
            return []
        ids = dict.fromkeys(re.findall(r"/shop/\?idx=(\d+)", listing))
        records = []
        for pid in ids:
            url = f"{self.base_url}/shop_view/?idx={pid}"
            page = fetch_cached(http, url, cache_dir, f"product_{pid}.html")
            if not page:
                continue
            facts = parse_momos_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{pid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# Manufact (manufactcoffee.com) -- Cafe24
# --------------------------------------------------------------------------- #


def manufact_bean_info_lines(soup: BeautifulSoup) -> list[str]:
    for th in soup.find_all("th"):
        label = _nfc(re.sub(r"\s+", "", th.get_text(" ", strip=True)))
        if label != "원두정보":
            continue
        td = th.find_next_sibling("td")
        if td is None:
            return []
        return [normalize_ws(x) for x in td.get_text("\n", strip=True).split("\n") if normalize_ws(x)]
    return []


def parse_manufact_product(html_text: str, url: str) -> dict | None:
    soup = BeautifulSoup(html_text, "lxml")
    facts = label_value_map(soup)
    name = clean_name(facts.get("상품명"))
    if is_excluded(name):
        return None
    lines = manufact_bean_info_lines(soup)
    origin_country: str | None = None
    origin_region: str | None = None
    roast_level: str | None = None
    notes_line: str | None = None
    weight_line: str | None = None
    for line in lines:
        if "로스트" in line:
            roast_level = re.sub(r"\s*로스트\s*$", "", line).strip() or None
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:g|kg)", line, re.IGNORECASE):
            # some products tack a bare weight ("100g") on as its own line
            weight_line = line
            continue
        country = find_country(line)
        if country:
            origin_country = country
            origin_region = normalize_ws(line.replace(country, "")) or None
            continue
        notes_line = line
    decaf = is_decaf(name)
    return {
        "name": name,
        "origin_country": origin_country,
        "origin_region": origin_region,
        "origin_farm": None,
        # Process is folded into the free-form "원두 정보" cell above rather
        # than getting its own row on this site -- left unset like 1kg
        # Coffee's marketing-prose-only process rather than mis-splitting it.
        "process": None,
        "roast_level": roast_level,
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(" ".join(lines)) if decaf else None,
        "flavor_notes": split_note_list(notes_line),
        "price_krw": parse_price_krw(facts.get("판매가")),
        "weight_g": parse_weight_g(weight_line) or parse_weight_g(name),
        "product_url": url,
    }


@dataclass
class ManufactCollector:
    name: str = "manufact"
    base_url: str = "https://manufactcoffee.com"
    roaster: str = "매뉴팩트"
    category: str = "66"  # COFFEE BEANS

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        listing = fetch_cached(http, f"{self.base_url}/product/list.html?cate_no={self.category}",
                                cache_dir, f"list_{self.category}.html")
        if not listing:
            return []
        seen: dict[str, str] = {}
        for m in re.finditer(rf'href="(/product/[^"?]+/(\d+)/category/{self.category}[^"]*)"', listing):
            seen.setdefault(m.group(2), m.group(1))
        records = []
        for pid, rel_url in seen.items():
            url = f"{self.base_url}{rel_url}"
            page = fetch_cached(http, url, cache_dir, f"product_{pid}.html")
            if not page:
                continue
            facts = parse_manufact_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{pid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# G Roasting (groasting.com) -- Cafe24
# --------------------------------------------------------------------------- #

GROASTING_GAUGE_SCALE = "groasting: numeric 0-5, half-step (og:description '산미 4.5│바디감 2│단맛 3')"
GROASTING_LABELS = (("아로마/플레이버", "notes_raw"),)
GROASTING_EXTRA_MARKERS = ("산미/기타", "G 로스팅 포인트")


def parse_groasting_product(html_text: str, url: str) -> dict | None:
    """G Roasting publishes its taste gauge as a one-line text summary in each product's
    og:description / JSON-LD description ("싱그러운 ... 후미\\r\\n산미 4.5│향미 5│균형 2│바디감 2│단맛 3").
    An older five-row bar-chart table with the same numbers is still in the page source but inside an HTML
    comment (not displayed, and sometimes stale -- e.g. 바디감 2.5 there vs 2 in the live summary), so only
    the displayed summary line is read. 향미/균형 (aroma/balance) are not intensity scales we model and are
    dropped. Roast degree is a bare word in the title ("약배전 산미높은 ..."); country and process are read
    from the title too. Flavor notes are the "커핑노트 아로마/플레이버 : ..." word list."""
    ld = first_product_ld(html_text)
    if ld is None:
        return None
    name, desc, price = ld_name_desc_price(ld)
    if is_excluded(name):
        return None
    m = re.search(r'<meta property="og:description" content="([^"]*)"', html_text)
    summary = html.unescape(m.group(1)) if m else (ld.get("description") or "")
    soup = BeautifulSoup(html_text, "lxml")
    facts = label_value_map(soup)
    text = normalize_ws(soup.get_text(" "))
    fields = extract_labeled_fields(text, GROASTING_LABELS, extra_markers=GROASTING_EXTRA_MARKERS)
    decaf = is_decaf(name)
    offers = ld.get("offers") or {}
    price = price if price is not None else offers.get("lowPrice") or offers.get("price")
    return {
        "name": name,
        "origin_country": find_country(name),
        "origin_region": None,
        "origin_farm": None,
        "process": find_process(name),
        "roast_level": find_roast_word(name),
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(text) if decaf else None,
        "flavor_notes": split_note_list((fields.get("notes_raw") or "").replace("｜", " ").replace("|", " ")),
        "price_krw": int(float(price)) if price is not None else parse_price_krw(facts.get("판매가")),
        "weight_g": parse_weight_g(facts.get("용량선택", "")) or parse_weight_g(name),
        **gauge_fields(number_gauges(summary), GROASTING_GAUGE_SCALE),
        "product_url": url,
    }


@dataclass
class GRoastingCollector:
    name: str = "groasting"
    base_url: str = "https://groasting.com"
    roaster: str = "G로스팅"
    categories: tuple[str, ...] = ("26", "91", "93")  # 원두전체, 스페셜티, 디카페인

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        ids: dict[str, None] = {}
        for cate in self.categories:
            listing = fetch_cached(http, f"{self.base_url}/product/list.html?cate_no={cate}",
                                    cache_dir, f"list_{cate}.html")
            if not listing:
                continue
            for pid in re.findall(r"product_no=(\d+)", listing):
                ids.setdefault(pid, None)
        records = []
        for pid in ids:
            url = f"{self.base_url}/product/detail.html?product_no={pid}"
            page = fetch_cached(http, url, cache_dir, f"product_{pid}.html")
            if not page:
                continue
            facts = parse_groasting_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{pid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# Naeil Coffee (naeilcoffee.co.kr) -- imweb
# --------------------------------------------------------------------------- #

NAEIL_GAUGE_SCALE = "naeilcoffee: 5 dots, half-step (●=1, ◐=0.5), acidity/body only"
# Site-local exclusions on top of EXCLUDE_KEYWORDS: steeped coffee bags, B2B payment pages and green beans.
NAEIL_EXCLUDE = ("커피백", "사업자", "결제창", "생두")
_NAEIL_PROCESS = re.compile(r"\b(washed|natural|honey|anaerobic)\b", re.IGNORECASE)


def parse_naeil_product(html_text: str, url: str) -> dict | None:
    """Naeil Coffee (imweb) shows "Tasting Notes A / B / C  Acidity ●●●◐○ / Body ●●●○○" under each bean's
    title (acidity and body only). Single origins also carry a labelled fact line in the JSON-LD description
    -- "Ethiopia Hunkute Sidamo G1 Washed 재배지역 : ... 재배고도 : 1,950-2,050m / 재배품종 : Heirloom / ..."
    -- from which country (first words), process, altitude and variety are read; blends (Dusk, Balance, ...)
    have none of it. Only whole-bean products are kept: the name must say 원두 or the description must carry
    the 재배지역 fact line (single origins are titled just "HUNKUTE 200g")."""
    ld = first_product_ld(html_text)
    if ld is None:
        return None
    name, desc, price = ld_name_desc_price(ld)
    if is_excluded(name) or any(k in (name or "") for k in NAEIL_EXCLUDE):
        return None
    if "원두" not in name and "재배지역" not in desc:
        return None
    soup = BeautifulSoup(html_text, "lxml")
    # the product's own summary block only -- never a "related products" widget elsewhere on the page
    summary = soup.find("div", class_="goods_summary")
    text = normalize_ws((summary or soup).get_text(" "))
    header = desc.split("재배지역")[0] if "재배지역" in desc else ""
    fields = extract_labeled_fields(normalize_ws(desc), (("재배지역", "region"), ("재배고도", "altitude_raw"),
                                                         ("재배품종", "variety")), extra_markers=("인증", "/"))
    notes_m = re.search(r"Tasting Notes\s*(.+?)\s*Acidity", text)
    from pipeline.rules import normalize_country  # local import: rules is a normalize-stage helper
    decaf = is_decaf(name, desc)
    process_m = _NAEIL_PROCESS.search(header)
    return {
        "name": name,
        "origin_country": normalize_country(header),
        "origin_region": fields.get("region") or None,
        "origin_farm": None,
        "process": process_m.group(1).title() if process_m else None,
        "roast_level": None,  # not stated on this site
        "is_decaf": decaf,
        "decaf_process": find_decaf_method(text) if decaf else None,
        "flavor_notes": [w.strip() for w in notes_m.group(1).split("/") if w.strip()][:8] if notes_m else [],
        "price_krw": int(price) if price is not None else None,
        "weight_g": parse_weight_g(name),
        "altitude_m": parse_altitude_m(fields.get("altitude_raw")),
        # the JSON-LD description is cut off at ~100 chars ("... / 재배품종 : Arusha bl..."): keep the variety
        # only when the next label (인증) proves the value wasn't truncated.
        "variety": (fields.get("variety") or "").strip(" .") or None if re.search(r"재배품종.*인증", desc) else None,
        **gauge_fields(dot_gauges(text), NAEIL_GAUGE_SCALE),
        "product_url": url,
    }


@dataclass
class NaeilCoffeeCollector:
    name: str = "naeilcoffee"
    base_url: str = "https://www.naeilcoffee.co.kr"
    roaster: str = "내일의커피"

    def collect(self, http, cache_dir: Path, collected_at: str) -> list[BeanFactRecord]:
        sitemap = fetch_cached(http, f"{self.base_url}/sitemap.xml", cache_dir, "sitemap.xml")
        if not sitemap:
            return []
        ids = dict.fromkeys(re.findall(r"/shop_view/(\d+)", sitemap))
        records = []
        for pid in ids:
            url = f"{self.base_url}/shop_view/?idx={pid}"
            page = fetch_cached(http, url, cache_dir, f"product_{pid}.html")
            if not page:
                continue
            facts = parse_naeil_product(page, url)
            if facts is None:
                continue
            records.append(BeanFactRecord(
                key=f"{self.name}:{pid}", site=self.name, roaster=self.roaster,
                collected_at=collected_at, **facts,
            ))
        return records


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

ALL_ROASTER_COLLECTORS = [
    FritzCollector(), NamusairoCollector(), CoffeeLibreCollector(),
    OnekgCoffeeCollector(), BlueBottleCollector(),
    AnthraciteCollector(), FeltCollector(), BeanBrothersCollector(),
    MomosCollector(), ManufactCollector(), GRoastingCollector(), NaeilCoffeeCollector(),
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
        "gauged": sum(1 for r in records if r.gauge_scale),
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
