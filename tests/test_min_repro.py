import csv
import sqlite3

from scripts.competition.min_repro import FILES, load, main, predict_attr, run_filter, violates

COFFEE_COLS = ["key", "name", "roaster", "origin_country", "origin_region", "process", "roast_level", "is_decaf",
               "decaf_process", "acidity", "acidity_label_source", "body", "body_label_source", "sweetness",
               "flavor_tags", "source", "source_url", "collected_at"]


def _write(path, cols, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _coffee(key, roaster, acidity, country="Ethiopia", tags="citrus;floral", decaf="false", src="gauge"):
    return {"key": key, "name": key, "roaster": roaster, "origin_country": country, "process": "washed",
            "roast_level": "light", "is_decaf": decaf, "acidity": acidity, "acidity_label_source": src,
            "flavor_tags": tags, "source": "roasters_kr"}


def _fixture(tmp_path):
    coffees = [_coffee(f"a{i}", "A", 4) for i in range(4)] + [_coffee(f"b{i}", "B", 4) for i in range(4)]
    coffees += [_coffee("c0", "C", 2, country="Brazil", tags="chocolate", decaf="true")]
    _write(tmp_path / FILES["coffees"], COFFEE_COLS, coffees)
    menu = [
        {"brand_key": "brand:x", "name": "아메리카노", "is_decaf": "false", "caffeine_mg": "150"},
        {"brand_key": "brand:x", "name": "디카페인 아메리카노", "is_decaf": "true", "caffeine_mg": "5"},
        {"brand_key": "brand:x", "name": "디카페인 라떼", "is_decaf": "true", "caffeine_mg": "5"},
        {"brand_key": "brand:x", "name": "디카페인 카페모카", "is_decaf": "true", "caffeine_mg": "136.7"},
    ]
    _write(tmp_path / FILES["menu_items"], ["brand_key", "name", "is_decaf", "caffeine_mg", "source_url"], menu)
    _write(tmp_path / FILES["brands"], ["key", "name", "decaf_available", "decaf_surcharge_krw", "source_url",
                                        "verified_at"], [{"key": "brand:x", "name": "X", "decaf_available": "true"}])
    labels = [{"name": "아메리카노", "is_milk": "false"}, {"name": "디카페인 아메리카노", "is_milk": "false"},
              {"name": "디카페인 라떼", "is_milk": "true"}, {"name": "디카페인 카페모카", "is_milk": "true"}]
    _write(tmp_path / FILES["milk_labels"], ["name", "is_milk"], labels)


def test_load_counts_rows_per_table(tmp_path):
    _fixture(tmp_path)
    counts = load(tmp_path, sqlite3.connect(":memory:"))
    assert counts == {"coffees": 9, "menu_items": 4, "brands": 1, "milk_labels": 4}


def test_filter_excludes_high_caffeine_decaf_and_milk(tmp_path):
    _fixture(tmp_path)
    conn = sqlite3.connect(":memory:")
    load(tmp_path, conn)
    result = run_filter(conn)
    assert result["violations"] == []
    names = [n for _, n, _ in result["sample"]]           # 디카페인+우유X 페르소나
    assert names == ["디카페인 아메리카노"]                 # 136.7mg 모카·우유 라떼·일반 아메리카노는 빠진다


def test_independent_check_flags_what_the_filter_must_not_pass():
    assert violates({"is_decaf": True, "caffeine_mg": 136.7}, True, "decaf_only", True)
    assert violates({"is_decaf": False, "caffeine_mg": 5}, False, "decaf_only", True)
    assert violates({"is_decaf": True, "caffeine_mg": 5}, True, "any", False)
    assert violates({"is_decaf": False, "caffeine_mg": None}, False, "low", True)
    assert violates({"is_decaf": True, "caffeine_mg": 5}, False, "decaf_only", False) is None


def test_neighbour_prediction_leaves_out_same_roaster():
    pool = [dict(_coffee(f"a{i}", "A", 1), _f={"country:Ethiopia"}, is_decaf=0) for i in range(5)]
    pool += [dict(_coffee(f"b{i}", "B", 5), _f={"country:Ethiopia"}, is_decaf=0) for i in range(5)]
    for p in pool:
        p["acidity"] = float(p["acidity"])
    target = dict(pool[0])
    pred, _, nbrs = predict_attr(target, pool)
    assert pred == 5.0                                       # 같은 로스터리 A(산미 1)는 이웃이 될 수 없다
    assert all(p["roaster"] == "B" for p in nbrs)


def test_main_runs_end_to_end_without_keys(tmp_path, capsys):
    _fixture(tmp_path)
    result = main(tmp_path)
    out = capsys.readouterr().out
    assert "위반 0건" in out
    assert result["neighbours"]["targets"] == 9            # 게이지 산미가 있는 원두 전부
