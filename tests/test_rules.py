import math

import pandas as pd
import pytest

from pipeline.records import CoffeeRecord, read_jsonl, write_jsonl
from pipeline.rules import (
    clean, detect_decaf, join_text, normalize_country, normalize_process,
    normalize_roast, num, process_from_text, to_quintile,
)


def test_clean_and_num():
    assert clean(float("nan")) is None
    assert clean("  ") is None
    assert clean(" a ") == "a"
    assert num("8.5") == 8.5
    assert num("abc") is None
    assert join_text("a", None, " ", "b") == "a\nb"
    assert join_text(None, "") is None


@pytest.mark.parametrize("texts,expected", [
    (("Ethiopia Decaf", "Swiss Water process"), (True, "swiss-water")),
    (("콜롬비아 디카페인", "슈가케인 공법"), (True, "sugarcane-ea")),
    (("Colombia decaf",), (True, "unknown")),
    (("Double Carbonic Maceration / Natural", "CO2"), (False, None)),
    (("Decaf Mexico", "supercritical CO2"), (True, "co2")),
    (("Mountain Water Process Mexico",), (True, "mountain-water")),
    (("Kenya AA",), (False, None)),
    (("Ethiopia Limu Washed", "Visit https://www.facebook.com/jadecafe19930822/ for more"), (False, None)),
    (("Homacho Waeno Natural", "visit www.theredecafe.com."), (False, None)),
    (("Guatemala ASDECAFE",), (False, None)),
    (("Sidamo Natural Water Decaf",), (True, "unknown")),
    (("DECAF Colombia",), (True, "unknown")),
])
def test_detect_decaf(texts, expected):
    assert detect_decaf(*texts) == expected


@pytest.mark.parametrize("text,expected", [
    ("Nyeri growing region, south-central Kenya", "Kenya"),
    ("United States (Hawaii)", "United States"),
    ("Tanzania, United Republic Of", "Tanzania"),
    ("Sumatra, Indonesia", "Indonesia"),
    ("Ethiopia; Colombia", "Ethiopia"),
    ("에티오피아 예가체프", "Ethiopia"),
    ("Cote d?Ivoire", "Côte d'Ivoire"),
    ("somewhere", None),
    (None, None),
])
def test_normalize_country(text, expected):
    assert normalize_country(text) == expected


@pytest.mark.parametrize("label,expected", [
    ("Washed / Wet", "washed"),
    ("Natural / Dry", "natural"),
    ("Semi-washed / Semi-pulped", "semi-washed"),
    ("Pulped natural / honey", "honey"),
    ("Double Anaerobic Washed", "anaerobic"),
    ("SEMI-LAVADO", "semi-washed"),
    ("Other", None),
    (None, None),
])
def test_normalize_process(label, expected):
    assert normalize_process(label) == expected


@pytest.mark.parametrize("text,expected", [
    ("Produced by smallholders and wet-processed (washed).", "washed"),
    ("dry-processed (natural method)", "natural"),
    ("A natural sweetness with washed clarity", "washed"),
    ("르완다 냐마셰케 내추럴", "natural"),
    ("honey notes and cocoa", None),
])
def test_process_from_text(text, expected):
    assert process_from_text(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Medium-Light", "medium-light"),
    ("Very Dark", "dark"),
    ("Medium", "medium"),
    ("Unknown", None),
    ("블론드 로스트", "light"),
    # flavor / body / brew phrases that share a roast word never set a roast (ADR 0014)
    ("Ethiopia washed, notes of dark chocolate and dark cherry", None),
    ("dark fruit, light body, French press", None),
    ("에티오피아 워시드, 다크 초콜릿, 다크 체리", None),
    ("다크초콜릿 향, 라이트 바디", None),
    ("Medium roast with dark chocolate notes", "medium"),
    ("다크 초콜릿 노트의 미디엄 로스트", "medium"),
    ("Dark roast, dark chocolate", "dark"),
    ("강배전, 다크 초콜릿", "dark"),
    ("light roast, medium body", "light"),
    ("Brazil | medium roast | notes: light citrus, dark beer, walnut", "medium"),
    ("Roast: Light", "light"),
    ("에티오피아 약배전, 다크 베리", "light"),
])
def test_normalize_roast(text, expected):
    assert normalize_roast(text) == expected


def test_to_quintile_even_spread_and_missing():
    q = to_quintile(pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, None]))
    assert list(q[:10]) == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    assert pd.isna(q.iloc[10])


def test_jsonl_roundtrip(tmp_path):
    rec = CoffeeRecord(key="k", name="n", source="s", collected_at="2026-09-24", flavor_tags=["lemon"])
    p = tmp_path / "x.jsonl"
    assert write_jsonl(p, [rec]) == 1
    assert read_jsonl(p, CoffeeRecord) == [rec]
