"""Tests for pipeline.normalize.mfds_food, using tiny synthetic rows (no real xlsx needed).

Rows are built via `_row(**overrides)` in the exact 0-based column layout the real 2026-08-28
MFDS workbook uses (see COL in the module under test) — only the columns we read are populated.
"""
from pipeline.normalize.mfds_food import (
    COL, brand_key_for_company, clean_drink_name, normalize_rows, parse_amount, parse_row,
)

N_COLS = 156  # 1 past the highest index we read (company=155)


def _row(**overrides) -> tuple:
    row = [None] * N_COLS
    defaults = {
        "food_code": "D000001",
        "name": "커피_아메리카노 아이스(ICED) (L)",
        "rep_name": "커피",
        "mid_name": "아메리카노",
        "sub_name": "해당없음",
        "basis": "100ml",
        "protein": 0.30,
        "sugar": None,
        "caffeine": 30.0,
        "source_name": "식품의약품안전처",
        "serving_size": "473ml",
        "company": "스타벅스",
    }
    defaults.update(overrides)
    for field_name, value in defaults.items():
        row[COL[field_name]] = value
    return tuple(row)


def test_brand_key_maps_known_company():
    assert brand_key_for_company("스타벅스") == "brand:starbucks"
    assert brand_key_for_company("메가커피") == "brand:mega"


def test_brand_key_new_company_gets_stable_slug():
    a = brand_key_for_company("더벤티")
    b = brand_key_for_company("더벤티")
    assert a == b == "brand:theventi"


def test_brand_key_space_variants_collapse_to_same_slug():
    assert brand_key_for_company("매머드익스프레스") == brand_key_for_company("매머드 익스프레스")


def test_brand_key_none_for_no_company():
    assert brand_key_for_company("해당없음") is None
    assert brand_key_for_company(None) is None


def test_brand_key_unknown_company_gets_hash_slug():
    key = brand_key_for_company("어떤신규브랜드")
    assert key is not None and key.startswith("brand:kr-")


def test_clean_drink_name_strips_prefix_temp_and_size():
    base, temp, size = clean_drink_name("커피_아메리카노 아이스(ICED) (L)")
    assert base == "아메리카노"
    assert temp == "ICED"
    assert size == "L"


def test_clean_drink_name_hot_no_size():
    base, temp, size = clean_drink_name("커피_카페 라떼 핫(HOT)")
    assert base == "카페 라떼"
    assert temp == "HOT"
    assert size is None


def test_clean_drink_name_no_temp_tag():
    base, temp, size = clean_drink_name("커피_에스프레소")
    assert base == "에스프레소"
    assert temp is None
    assert size is None


def test_decaf_flag_from_name():
    rec = parse_row(_row(name="커피_디카페인 아메리카노 핫(HOT)"))
    assert rec.is_decaf is True
    rec2 = parse_row(_row(name="커피_아메리카노 핫(HOT)"))
    assert rec2.is_decaf is False


def test_parse_amount():
    assert parse_amount("473ml") == (473.0, "ml")
    assert parse_amount("355g") == (355.0, "g")
    assert parse_amount(None) == (None, None)
    assert parse_amount("") == (None, None)


def test_per_serving_scaling_same_unit():
    rec = parse_row(_row(basis="100ml", caffeine=30.0, protein=0.30, serving_size="473ml"))
    assert rec.unit_mismatch is False
    assert round(rec.caffeine_mg_per_serving, 2) == round(30.0 * 473 / 100, 2)
    assert round(rec.protein_g_per_serving, 3) == round(0.30 * 473 / 100, 3)


def test_per_serving_scaling_flags_unit_mismatch():
    # basis in g, serving in ml (or vice versa) — still scaled (1g ~= 1ml) but flagged.
    rec = parse_row(_row(basis="100g", serving_size="473ml", caffeine=30.0))
    assert rec.unit_mismatch is True
    assert round(rec.caffeine_mg_per_serving, 2) == round(30.0 * 473 / 100, 2)


def test_missing_caffeine_is_none():
    rec = parse_row(_row(caffeine=None))
    assert rec.caffeine_mg_per_basis is None
    assert rec.caffeine_mg_per_serving is None


def test_non_coffee_rep_name_excluded():
    assert parse_row(_row(rep_name="케이크", name="케이크_치즈 케이크")) is None


def test_combo_set_excluded():
    assert parse_row(_row(name="커피_쿠키&크림 번버거 + 아메리카노 SET")) is None


def test_new_company_row_gets_slug_brand_key():
    rec = parse_row(_row(company="더벤티"))
    assert rec.brand_key == "brand:theventi"


def test_normalize_rows_filters_non_drinks():
    rows = [
        _row(food_code="A1", name="커피_아메리카노 핫(HOT)"),
        _row(food_code="A2", rep_name="빵", name="빵_크림빵"),
    ]
    out = normalize_rows(rows)
    assert len(out) == 1
    assert out[0].food_code == "A1"
