from scripts.competition.export_csv import (
    ALLOWED_COFFEE_SOURCES,
    BRAND_COLUMNS,
    COFFEE_COLUMNS,
    MENU_ITEM_COLUMNS,
    MILK_LABEL_COLUMNS,
    SCA_KO_COLUMNS,
    build_column_definitions,
    rows_for_brands,
    rows_for_coffees,
    rows_for_menu_items,
    rows_for_milk_labels,
    rows_for_sca_ko,
    write_csv,
)


def _raw_coffee(**over):
    row = {
        "key": "cqi:abc", "name": "Yirgacheffe", "roaster": "Some Roaster",
        "origin_country": "Ethiopia", "origin_region": "Yirgacheffe", "process": "washed",
        "roast_level": "light", "is_decaf": False, "decaf_process": None,
        "acidity": 4, "body": 3, "sweetness": 3, "flavor_tags": ["floral", "citrus"],
        "flavor_summary": "밝고 화사한 시트러스와 꽃향이 두드러진다.",
        "source": "cqi", "source_url": "https://example.com/abc", "collected_at": "2026-01-01",
        "id": 1, "active": True,
    }
    row.update(over)
    return row


class TestRowsForCoffees:
    def test_drops_free_text_flavor_summary_column(self):
        rows = rows_for_coffees([_raw_coffee()])
        assert "flavor_summary" not in rows[0]
        assert set(rows[0]) == set(COFFEE_COLUMNS)

    def test_excludes_restricted_sources(self):
        rows = rows_for_coffees([
            _raw_coffee(key="cqi:a", source="cqi"),
            _raw_coffee(key="coffeereview_kaggle:b", source="coffeereview_kaggle"),
            _raw_coffee(key="roasterdb:c", source="roasterdb"),
            _raw_coffee(key="roasters_kr:d", source="roasters_kr"),
            _raw_coffee(key="shopify:e", source="shopify"),
        ])
        sources = {r["source"] for r in rows}
        assert sources == {"cqi", "roasters_kr", "shopify"}
        assert sources.isdisjoint({"coffeereview_kaggle", "roasterdb"})

    def test_allowed_sources_constant_matches_filter(self):
        assert set(ALLOWED_COFFEE_SOURCES) == {"cqi", "roasters_kr", "shopify"}

    def test_keeps_row_order_and_values(self):
        rows = rows_for_coffees([_raw_coffee(key="cqi:a"), _raw_coffee(key="cqi:b")])
        assert [r["key"] for r in rows] == ["cqi:a", "cqi:b"]


class TestRowsForMenuItems:
    def test_selects_brand_key_not_brand_id(self):
        raw = {"id": 1, "key": "mega:latte", "brand_id": 5, "brand_key": "mega", "name": "라떼",
               "name_en": "Latte", "category": "coffee", "is_decaf": False, "decaf_option": True,
               "caffeine_mg": 150.0, "coffee_id": None, "source_url": "https://mega.example",
               "collected_at": "2026-01-01", "active": True}
        rows = rows_for_menu_items([raw])
        assert set(rows[0]) == set(MENU_ITEM_COLUMNS)
        assert rows[0]["brand_key"] == "mega"
        assert "brand_id" not in rows[0] and "id" not in rows[0]


class TestRowsForBrands:
    def test_drops_free_text_notes_and_internal_ids(self):
        raw = {"id": 9, "key": "mega", "name": "메가커피", "decaf_available": True,
               "decaf_surcharge_krw": 500, "default_bean_coffee_id": 1, "decaf_bean_coffee_id": 2,
               "notes": "내부 참고용 자유 서술", "source_url": "https://mega.example",
               "verified_at": "2026-01-01", "bean": {"acidity": 3}, "decaf_bean": None, "active": True}
        rows = rows_for_brands([raw])
        assert set(rows[0]) == set(BRAND_COLUMNS)
        assert "notes" not in rows[0]


class TestRowsForMilkLabels:
    def test_flattens_name_to_bool_mapping(self):
        rows = rows_for_milk_labels({"카페라떼": True, "아메리카노": False})
        assert set(rows[0]) == set(MILK_LABEL_COLUMNS)
        by_name = {r["name"]: r["is_milk"] for r in rows}
        assert by_name == {"카페라떼": True, "아메리카노": False}


class TestRowsForScaKo:
    def test_derives_name_en_and_level_from_key_path(self):
        rows = rows_for_sca_ko({"fruity": "과일", "fruity>berry": "베리", "fruity>berry>blackberry": "블랙베리"})
        by_key = {r["key"]: r for r in rows}
        assert set(rows[0]) == set(SCA_KO_COLUMNS)
        assert by_key["fruity"]["level"] == 1 and by_key["fruity"]["name_en"] == "fruity"
        assert by_key["fruity>berry>blackberry"]["level"] == 3
        assert by_key["fruity>berry>blackberry"]["name_en"] == "blackberry"
        assert by_key["fruity>berry>blackberry"]["name_ko"] == "블랙베리"


class TestColumnDefinitions:
    def test_row_count_matches_sum_of_table_columns(self):
        defs = build_column_definitions()
        assert len(defs) == len(COFFEE_COLUMNS) + len(MENU_ITEM_COLUMNS) + len(BRAND_COLUMNS)

    def test_each_row_has_required_fields_and_sequential_numbers(self):
        defs = build_column_definitions()
        assert [d["변수번호"] for d in defs] == list(range(1, len(defs) + 1))
        for d in defs:
            assert set(d) == {"변수번호", "타깃여부", "타입", "컬럼명", "비고"}
            assert d["비고"]


class TestWriteCsvBom:
    def test_written_file_starts_with_utf8_bom(self, tmp_path):
        path = tmp_path / "out.csv"
        write_csv(path, ["a", "b"], [{"a": 1, "b": "x"}])
        assert path.read_bytes()[:3] == b"\xef\xbb\xbf"

    def test_written_file_round_trips_header_and_rows(self, tmp_path):
        import csv
        path = tmp_path / "out.csv"
        write_csv(path, ["a", "b"], [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = list(csv.DictReader(f))
        assert reader == [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]

    def test_serializes_lists_and_booleans_for_excel(self, tmp_path):
        import csv
        path = tmp_path / "out.csv"
        write_csv(path, ["tags", "flag", "missing"], [{"tags": ["a", "b"], "flag": True, "missing": None}])
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = list(csv.DictReader(f))
        assert reader == [{"tags": "a;b", "flag": "true", "missing": ""}]
