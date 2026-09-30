"""Tests for pipeline.normalize.mfds_food, using tiny synthetic rows (no real xlsx needed).

Rows are built via `_row(**overrides)` in the exact 0-based column layout the real 2026-08-28
MFDS workbook uses (see COL in the module under test) — only the columns we read are populated.
"""
from pipeline.normalize.mfds_food import (
    COL, brand_key_for_company, clean_drink_name, menu_items_from_mfds, normalize_rows, parse_amount, parse_row,
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


# ===== Phase 2: menu_items_from_mfds ========================================================

def _drink(**overrides) -> tuple:
    """Row for a Phase 2 target brand (theventi) unless overridden."""
    defaults = {"company": "더벤티", "basis": "100ml", "serving_size": "200ml"}
    defaults.update(overrides)
    return _row(**defaults)


def test_menu_items_from_mfds_one_row_per_drink_and_temperature():
    rows = [
        _drink(name="커피_아메리카노 핫(HOT)", caffeine=50.0),
        _drink(name="커피_아메리카노 아이스(ICED)", caffeine=80.0),
    ]
    items, _ = menu_items_from_mfds(normalize_rows(rows), "2026-08-28", frozenset({"brand:theventi"}))
    assert {i.name for i in items} == {"아메리카노(HOT)", "아메리카노(ICED)"}
    assert all(i.brand_key == "brand:theventi" and i.source == "mfds_food" for i in items)
    assert all(i.source_url == "https://various.foodsafetykorea.go.kr/nutrient/" for i in items)


def test_menu_items_from_mfds_no_temperature_tag_keeps_bare_name():
    rows = [_drink(name="커피_카페 라떼 프리미엄마일드")]
    items, _ = menu_items_from_mfds(normalize_rows(rows), "2026-08-28", frozenset({"brand:theventi"}))
    assert [i.name for i in items] == ["카페 라떼 프리미엄마일드"]


def test_menu_items_from_mfds_picks_the_regular_size_by_priority_token():
    # per-serving caffeine = basis mg * serving_ml / 100: (L) -> 200*300/100=600, (R) -> 100*200/100=200.
    rows = [
        _drink(name="커피_카페 라떼 핫(HOT) (L)", caffeine=200.0, serving_size="300ml"),
        _drink(name="커피_카페 라떼 핫(HOT) (R)", caffeine=100.0, serving_size="200ml"),
    ]
    [item], _ = menu_items_from_mfds(normalize_rows(rows), "2026-08-28", frozenset({"brand:theventi"}))
    assert item.caffeine_mg == 200.0    # (R)'s per-serving value, not (L)'s larger 600


def test_menu_items_from_mfds_falls_back_to_the_smallest_size_when_no_priority_token():
    # neither (J) nor (L) is a priority token -> pick the smaller listed serving_size ((L), 200ml).
    rows = [
        _drink(name="커피_카페 모카 핫(HOT) (J)", caffeine=200.0, serving_size="300ml"),
        _drink(name="커피_카페 모카 핫(HOT) (L)", caffeine=100.0, serving_size="200ml"),
    ]
    [item], _ = menu_items_from_mfds(normalize_rows(rows), "2026-08-28", frozenset({"brand:theventi"}))
    assert item.caffeine_mg == 200.0    # (L)'s per-serving value (100*200/100), not (J)'s larger 600


def test_menu_items_from_mfds_folds_a_multi_word_size_suffix_into_its_siblings():
    # "(Mini Venti)" doesn't match clean_drink_name's single-word SIZE_TAG_RE, so it stays on drink_name;
    # menu_items_from_mfds must still fold it into the same (drink, temperature) group as (Tall)/(Grande)/
    # (Venti), and pick (Tall) as the default -- never ship "화이트 아메리카노 (Mini Venti)" as a bogus extra item.
    rows = [
        _drink(name="커피_화이트 아메리카노 핫(HOT) (Venti)", caffeine=820.0),
        _drink(name="커피_화이트 아메리카노 핫(HOT) (Tall)", caffeine=205.0),
        _drink(name="커피_화이트 아메리카노 핫(HOT) (Mini Venti)", caffeine=614.8),
        _drink(name="커피_화이트 아메리카노 핫(HOT) (Grande)", caffeine=409.9),
    ]
    items, _ = menu_items_from_mfds(normalize_rows(rows), "2026-08-28", frozenset({"brand:theventi"}))
    assert [i.name for i in items] == ["화이트 아메리카노(HOT)"]
    assert items[0].caffeine_mg == 410.0    # (Tall)'s 205 basis mg, scaled to _drink's 200ml serving


def test_menu_items_from_mfds_only_includes_target_brand_keys():
    rows = [_drink(company="더벤티"), _drink(company="어떤신규브랜드", name="커피_다른 음료")]
    items, _ = menu_items_from_mfds(normalize_rows(rows), "2026-08-28", frozenset({"brand:theventi"}))
    assert {i.brand_key for i in items} == {"brand:theventi"}


def test_menu_items_from_mfds_protein_label_above_and_below_threshold():
    rows = [
        _drink(name="커피_카페 라떼 핫(HOT)", protein=1.0),    # 1.0 g/100ml >= 0.32 -> milk
        _drink(name="커피_아메리카노 핫(HOT)", protein=0.0),    # < 0.32 -> not milk
    ]
    _, labels = menu_items_from_mfds(normalize_rows(rows), "2026-08-28", frozenset({"brand:theventi"}))
    assert labels == {"카페 라떼(HOT)": True, "아메리카노(HOT)": False}


def test_protein_near_the_threshold_gets_no_milk_label():
    from pipeline.normalize.mfds_food import MFDS_MILK_PROTEIN_THRESHOLD, MFDS_MILK_UNSURE_BAND
    lo, hi = MFDS_MILK_UNSURE_BAND
    assert lo < MFDS_MILK_PROTEIN_THRESHOLD < hi       # 0.32 (탐앤탐스 싱글오리진) is never labelled


def test_known_spelling_slips_are_fixed_for_display():
    from pipeline.normalize.mfds_food import clean_drink_name
    assert clean_drink_name("커피_에소프레소 (더블) 핫(HOT)")[0] == "에스프레소 (더블)"
    assert clean_drink_name("커피_비닐라 라떼 아이스(ICED)")[0] == "바닐라 라떼"
    assert clean_drink_name("커피_아포가또")[0] == "아포가또"          # a brand spelling, left alone
