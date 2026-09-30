"""Normalize coffee-shop drink rows out of the MFDS 식품영양성분 DB (식약처 식품안전나라, 음식 DB).

Source file (not committed — see data/raw/ in .gitignore):
    data/raw/mfds_food/20260828_음식DB.xlsx  (19,617 rows x 160 columns, version 2026-08-28)

Licence: 이용허락범위 제한 없음(data.go.kr), 출처 표시 의무 — see docs/adr/0023-mfds-food-db.md.

This module is pure/offline: it reads the workbook with openpyxl and returns plain
MfdsCoffeeDrink records. It does not touch the DB; `pipeline.normalize.run_normalize` is the only
caller that turns the Phase 2 subset (`menu_items_from_mfds` below) into shipped MenuItemRecords.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from pipeline.records import MenuItemRecord
from pipeline.rules import clean, detect_decaf, num

# 0-based column indices in the 2026-08-28 workbook (row 1 is the header).
COL = dict(
    food_code=0, name=1, rep_name=9, mid_name=11, sub_name=13, basis=16,
    protein=19, carbs=23, sugar=24, caffeine=150, source_name=152,
    serving_size=154, company=155,
)

# 업체명 (MFDS) -> our data/curated/brands.yaml key, for the 8 brands we already track that appear
# in this workbook (커피빈/폴바셋/블루보틀 are in our menus too but have zero 대표식품명=='커피' rows
# here — 커피빈 only shows up for bakery/dessert items; see the ADR).
BRAND_KEY_BY_COMPANY = {
    "스타벅스": "starbucks",
    "메가커피": "mega",
    "빽다방": "paik",
    "이디야": "ediya",
    "할리스": "hollys",
    "컴포즈커피": "compose",
    "투썸플레이스": "twosome",
    "커피빈": "coffeebean",
    "폴바셋": "paulbassett",
    "폴 바셋": "paulbassett",
    "블루보틀": "bluebottle",
}

# Readable slugs for well-known chains that are not (yet) one of our 8 tracked brands. Anything else
# falls back to a stable hash-based slug (_slug_for) so every company still gets a deterministic key.
NEW_BRAND_SLUGS = {
    "더벤티": "theventi", "탐앤탐스": "tomntoms", "엔제리너스": "angelinus", "파스쿠찌": "pascucci",
    "카페베네": "caffebene", "공차": "gongcha", "드롭탑": "droptop", "커피베이": "coffeebay",
    "요거프레소": "yogerpresso", "바나프레소": "banapresso", "커피마마": "coffeemama",
    "커피에반하다": "coffeeinlove", "달콤": "dalkomm", "아임일리터": "imaliter", "더리터": "theliter",
    "카페게이트": "cafegate", "카페띠아모": "cafetiamo", "블루샥": "blueshark", "카페루앤비": "caferuenb",
    "토프레소": "topresso", "베러댄와플": "betterthanwaffle", "카페봄봄": "cafebombom",
    "디저트39": "dessert39", "청자다방": "cheongjadabang", "스무디킹": "smoothieking",
    "롤링핀": "rollingpin", "팔공티": "palgongtea",
    "매머드익스프레스": "mammothexpress",  # space variant "매머드 익스프레스" also matches (_norm_company strips spaces)
}

# 매머드익스프레스 / 매머드 익스프레스 both appear (space variant) — normalize before lookup.
_SPACE_RE = re.compile(r"\s+")


def _norm_company(raw: str) -> str:
    return _SPACE_RE.sub("", raw)


def _slug_for(company_raw: str) -> str:
    key = _norm_company(company_raw)
    if key in {_norm_company(c) for c in BRAND_KEY_BY_COMPANY}:
        for c, v in BRAND_KEY_BY_COMPANY.items():
            if _norm_company(c) == key:
                return v
    for c, slug in NEW_BRAND_SLUGS.items():
        if _norm_company(c) == key:
            return slug
    return "kr-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def brand_key_for_company(company_raw: str | None) -> str | None:
    """Map an MFDS 업체명 to `brand:<our key>` (existing brand) or `brand:<slug>` (new one).

    Returns None for 해당없음 / blank (no identifiable company).
    """
    company = clean(company_raw)
    if not company or company == "해당없음":
        return None
    return f"brand:{_slug_for(company)}"


TEMP_RE = re.compile(r"핫\s*\(HOT\)|\(HOT\)|아이스\s*\(ICED?\)|\(ICED?\)", re.I)
SIZE_TAG_RE = re.compile(r"\(([A-Za-z]{1,10})\)")
FOOD_COMBO_EXCLUDE_RE = re.compile(r"SET|세트|번버거|샌드위치|와플(?!.*커피)|베이글")


def _temperature(name: str) -> str | None:
    m = TEMP_RE.search(name)
    if not m:
        return None
    return "HOT" if "HOT" in m.group(0).upper() else "ICED"


def _size_tag(name_wo_temp: str) -> str | None:
    tags = SIZE_TAG_RE.findall(name_wo_temp)
    return tags[-1] if tags else None


def clean_drink_name(raw_name: str) -> tuple[str, str | None, str | None]:
    """Strip the "커피_" prefix and pull out (base_name, temperature, size_tag)."""
    name = clean(raw_name) or ""
    name = re.sub(r"^커피_", "", name)
    temperature = _temperature(name)
    base = TEMP_RE.sub(" ", name)
    size_tag = _size_tag(base)
    if size_tag:
        # Only strip the trailing size parenthetical, not an unrelated one earlier in the name.
        base = re.sub(r"\(" + re.escape(size_tag) + r"\)\s*$", "", base.strip())
    base = re.sub(r"\s{2,}", " ", base).strip()
    return base, temperature, size_tag


_AMOUNT_RE = re.compile(r"([\d.]+)\s*(g|ml)", re.I)


def parse_amount(text: str | None) -> tuple[float | None, str | None]:
    s = clean(text)
    if not s:
        return None, None
    m = _AMOUNT_RE.search(s)
    if not m:
        return None, None
    return float(m.group(1)), m.group(2).lower()


class MfdsCoffeeDrink(BaseModel):
    key: str
    food_code: str
    raw_name: str
    company_raw: str | None
    brand_key: str | None          # brand:<our key> or brand:<slug>; None if company is 해당없음
    drink_name: str                # cleaned, "커피_" stripped, temp/size removed
    temperature: str | None        # HOT / ICED / None
    size_tag: str | None           # R / L / Tall / Venti / EX / Max / ... (raw token, no parens)
    is_decaf: bool
    mid_category: str | None
    sub_category: str | None
    basis_amount: float | None     # 영양성분함량기준량, e.g. 100
    basis_unit: str | None         # g / ml
    serving_amount: float | None   # 식품중량
    serving_unit: str | None
    unit_mismatch: bool            # basis_unit != serving_unit (1g treated as 1ml to scale)
    caffeine_mg_per_basis: float | None
    protein_g_per_basis: float | None
    sugar_g_per_basis: float | None
    caffeine_mg_per_serving: float | None
    protein_g_per_serving: float | None
    sugar_g_per_serving: float | None
    source_name: str | None


def _scale(per_basis: float | None, basis_amount: float | None, serving_amount: float | None) -> float | None:
    if per_basis is None or basis_amount in (None, 0) or serving_amount is None:
        return None
    return per_basis * serving_amount / basis_amount


def is_coffee_shop_drink(rep_name: str | None, name: str | None) -> bool:
    """Select coffee-shop drink rows: 대표식품명 == 커피, minus obvious food/combo contamination."""
    if rep_name != "커피":
        return False
    if name and FOOD_COMBO_EXCLUDE_RE.search(name):
        return False
    return True


def parse_row(row: tuple, food_code_prefix: str = "mfds") -> MfdsCoffeeDrink | None:
    """Build one MfdsCoffeeDrink from a raw (already 0-indexed, in COL order) worksheet row.

    Returns None if the row is not a coffee-shop drink (see is_coffee_shop_drink) or has no name.
    """
    name = clean(row[COL["name"]])
    rep_name = clean(row[COL["rep_name"]])
    if not name or not is_coffee_shop_drink(rep_name, name):
        return None

    food_code = clean(row[COL["food_code"]]) or ""
    company_raw = clean(row[COL["company"]])
    drink_name, temperature, size_tag = clean_drink_name(name)
    is_decaf = detect_decaf(drink_name)[0]

    basis_amount, basis_unit = parse_amount(row[COL["basis"]])
    serving_amount, serving_unit = parse_amount(row[COL["serving_size"]])
    unit_mismatch = bool(basis_unit and serving_unit and basis_unit != serving_unit)

    caffeine = num(row[COL["caffeine"]])
    protein = num(row[COL["protein"]])
    sugar = num(row[COL["sugar"]])

    return MfdsCoffeeDrink(
        key=f"{food_code_prefix}:{food_code}",
        food_code=food_code,
        raw_name=name,
        company_raw=company_raw,
        brand_key=brand_key_for_company(company_raw),
        drink_name=drink_name,
        temperature=temperature,
        size_tag=size_tag,
        is_decaf=is_decaf,
        mid_category=clean(row[COL["mid_name"]]),
        sub_category=clean(row[COL["sub_name"]]),
        basis_amount=basis_amount,
        basis_unit=basis_unit,
        serving_amount=serving_amount,
        serving_unit=serving_unit,
        unit_mismatch=unit_mismatch,
        caffeine_mg_per_basis=caffeine,
        protein_g_per_basis=protein,
        sugar_g_per_basis=sugar,
        caffeine_mg_per_serving=_scale(caffeine, basis_amount, serving_amount),
        protein_g_per_serving=_scale(protein, basis_amount, serving_amount),
        sugar_g_per_serving=_scale(sugar, basis_amount, serving_amount),
        source_name=clean(row[COL["source_name"]]),
    )


def normalize_rows(rows: list[tuple]) -> list[MfdsCoffeeDrink]:
    """rows: worksheet rows (tuples), header already excluded, in the 2026-08-28 column order."""
    out = []
    for row in rows:
        rec = parse_row(row)
        if rec is not None:
            out.append(rec)
    return out


def load_workbook_rows(xlsx_path: Path) -> list[tuple]:
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb[wb.sheetnames[0]]
    return [row for row in ws.iter_rows(min_row=2, values_only=True)]


def normalize_mfds_food(xlsx_path: Path) -> list[MfdsCoffeeDrink]:
    return normalize_rows(load_workbook_rows(xlsx_path))


# ===== Phase 2 (docs/adr/0023-mfds-food-db.md): franchise menus sourced from this DB =====
#
# twosome fills its collector-blocked gap (ADR 0014, CloudFront 403); the rest are new brands whose
# coffee rows have a measured caffeine value for >=90% of drinks (phase 1 inventory) among the
# candidates phase 1 flagged as promising (더벤티·아임일리터·공차·달콤, plus checking 파스쿠찌·탐앤탐스·
# 커피에반하다·바나프레소·매머드익스프레스·드롭탑): 파스쿠찌(63%)·바나프레소(87%)·드롭탑(75%) fail the
# bar and are left out.
MFDS_SOURCE = "mfds_food"
MFDS_SOURCE_URL = "https://various.foodsafetykorea.go.kr/nutrient/"  # 식품안전나라 "음식 DB" (수동 다운로드, ADR 0023)
MFDS_MILK_PROTEIN_THRESHOLD = 0.32  # g protein per 100g/100ml basis -- ADR 0023 phase-1 fit (acc 98.1%)
# Too close to the threshold to call: no label, so the menu stays needs_review (never recommended) until a person
# labels it -- the condition guarantee (0 violations) outranks coverage. 탐앤탐스 black 싱글오리진 drinks sit at 0.32.
MFDS_MILK_UNSURE_BAND = (0.25, 0.45)

PHASE2_MENU_BRAND_KEYS = frozenset({
    "brand:twosome",       # gap-fill: collector blocked (CloudFront 403), 0 menu rows without this
    "brand:theventi", "brand:imaliter", "brand:gongcha", "brand:dalkomm", "brand:tomntoms",
    "brand:coffeeinlove", "brand:mammothexpress",
})

# A drink's "default/regular" size, checked in this order; brands with none of these tokens (e.g.
# gongcha's L/J, or a brand that never tags a size at all) fall back to the smallest listed size.
_DEFAULT_SIZE_PRIORITY = ("R", "Tall", "M", "레귤러")


def _pick_default_size(rows: list[MfdsCoffeeDrink]) -> MfdsCoffeeDrink:
    for tag in _DEFAULT_SIZE_PRIORITY:
        matches = [d for d in rows if d.size_tag == tag]
        if matches:
            return matches[0]
    return min(rows, key=lambda d: d.serving_amount if d.serving_amount is not None else float("inf"))


def _protein_density(d: MfdsCoffeeDrink) -> float | None:
    """Protein per 100g/100ml of the row's own basis (normally 100 already; normalized just in case)."""
    if d.protein_g_per_basis is None or not d.basis_amount:
        return None
    return d.protein_g_per_basis / d.basis_amount * 100


# A handful of dalkomm rows carry a single store's own submission tacked on as a trailing branch code,
# e.g. "카페 라떼 (K(코끼리))" alongside the brand-level "카페 라떼" -- clean_drink_name (shared with Phase 1
# and left untouched there) has no reason to know about this, so it's stripped here, Phase 2-only, before
# grouping: this folds the branch-specific row into the same (drink, temperature) group as its brand-level
# counterpart instead of shipping it as a second, oddly-named near-duplicate menu item.
_BRANCH_CODE_RE = re.compile(r"\s*\([A-Za-z]{1,3}\([^()]+\)\)\s*$")


def _strip_branch_code(name: str) -> str:
    return _BRANCH_CODE_RE.sub("", name).strip()


# clean_drink_name's own SIZE_TAG_RE only strips a trailing single-word Latin size tag (e.g. "(Venti)"); a
# multi-word one such as 커피에반하다's "(Mini Venti)" doesn't match it and is left on the name, which would
# otherwise ship as a bogus extra "drink" distinct from its "화이트 아메리카노" Tall/Grande/Venti siblings.
# Stripped here, Phase 2-only, so it joins that same group instead (and, having no recognized size_tag of its
# own, never wins the default-size pick over an actual R/Tall/M/레귤러 row there). Restricted to known size
# vocabulary (not "any trailing Latin parenthetical") so an unrelated English aside is never mistaken for one.
_SIZE_WORDS = r"Mini|Venti|Grande|Tall|Trenta|Short|Solo|Max|Jumbo|Regular|EX"
_EXTRA_SIZE_RE = re.compile(rf"\s*\((?:{_SIZE_WORDS})(?:\s+(?:{_SIZE_WORDS}))*\)\s*$", re.I)


def _strip_extra_size_suffix(name: str) -> str:
    return _EXTRA_SIZE_RE.sub("", name).strip()


def menu_items_from_mfds(drinks: list[MfdsCoffeeDrink], collected_at: str,
                         brand_keys: frozenset[str] | None = None) -> tuple[list[MenuItemRecord], dict[str, bool]]:
    """One MenuItemRecord per (brand, drink name, temperature) at the brand's default/regular size.

    Returns (menu items, protein-derived milk labels): the second is name -> bool (protein density >=
    MFDS_MILK_PROTEIN_THRESHOLD) for every item built here, to be merged into the hand milk labels by
    a caller (a hand label always wins -- pipeline.load.load_milk_labels) so these menus don't sit
    needs_review forever just because nobody has hand-labelled the name yet.
    """
    target = brand_keys if brand_keys is not None else PHASE2_MENU_BRAND_KEYS
    groups: dict[tuple[str, str, str | None], list[MfdsCoffeeDrink]] = defaultdict(list)
    for d in drinks:
        if d.brand_key in target:
            name = _strip_extra_size_suffix(_strip_branch_code(d.drink_name))
            groups[(d.brand_key, name, d.temperature)].append(d)

    items: dict[str, MenuItemRecord] = {}
    protein_labels: dict[str, bool] = {}
    for (brand_key, drink_name, temperature), rows in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")):
        chosen = _pick_default_size(rows)
        name = f"{drink_name}({temperature})" if temperature else drink_name
        key = f"menu:mfds:{brand_key.split(':', 1)[1]}:{drink_name}:{temperature or 'NA'}"
        items[key] = MenuItemRecord(
            key=key, brand_key=brand_key, name=name, category="커피", is_decaf=chosen.is_decaf,
            decaf_option=False, caffeine_mg=chosen.caffeine_mg_per_serving, source=MFDS_SOURCE,
            source_url=MFDS_SOURCE_URL, collected_at=collected_at,
        )
        density = _protein_density(chosen)
        if density is not None and not (MFDS_MILK_UNSURE_BAND[0] <= density < MFDS_MILK_UNSURE_BAND[1]):
            protein_labels[name] = density >= MFDS_MILK_PROTEIN_THRESHOLD
    return list(items.values()), protein_labels
