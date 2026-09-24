import csv

import pytest

from pipeline.gold import GOLD_COLUMNS, label_gold, sample_gold, score_gold
from pipeline.records import CoffeeRecord, ReviewRecord, write_jsonl


def setup(tmp_path, n_decaf=3, n_regular=20):
    coffees, reviews = [], []
    for i in range(n_decaf + n_regular):
        key = f"c{i}"
        coffees.append(CoffeeRecord(key=key, name=key, is_decaf=i < n_decaf, acidity=3, flavor_tags=["lemon"],
                                    source="t", collected_at="x"))
        reviews.append(ReviewRecord(key=f"r{i}", coffee_key=key, text=f"text {i}", source="t", collected_at="x"))
    coffees.append(CoffeeRecord(key="notext", name="n", source="t", collected_at="x"))
    write_jsonl(tmp_path / "e" / "coffees.jsonl", coffees)
    write_jsonl(tmp_path / "n" / "reviews.jsonl", reviews)


def test_sample_gold_includes_decaf_and_protects_labels(tmp_path):
    setup(tmp_path)
    out = tmp_path / "gold.csv"
    assert sample_gold(tmp_path / "e", tmp_path / "n", out, n=10) == 10
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert list(rows[0].keys()) == GOLD_COLUMNS
    assert sum(r["pred_is_decaf"] == "1" for r in rows) == 3
    assert all(r["key"] != "notext" and r["gold_acidity"] == "" for r in rows)
    with pytest.raises(FileExistsError):
        sample_gold(tmp_path / "e", tmp_path / "n", out, n=10)


def test_score_gold(tmp_path):
    p = tmp_path / "g.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        base = {c: "" for c in GOLD_COLUMNS}
        w.writerow({**base, "key": "a", "pred_acidity": "4", "gold_acidity": "4", "pred_body": "2", "gold_body": "4",
                    "pred_is_decaf": "1", "gold_is_decaf": "1", "pred_tags": "lemon; honey", "gold_tags": "lemon"})
        w.writerow({**base, "key": "b", "pred_acidity": "", "gold_acidity": "3", "pred_body": "3", "gold_body": "3",
                    "pred_is_decaf": "0", "gold_is_decaf": "1", "pred_tags": "", "gold_tags": ""})
    s = score_gold(p)
    assert s["acidity"] == {"n": 2, "exact": 0.5, "within1": 0.5}
    assert s["body"] == {"n": 2, "exact": 0.5, "within1": 0.5}
    assert s["sweetness"] == {"n": 0, "exact": None, "within1": None}
    assert s["is_decaf"] == {"n": 2, "accuracy": 0.5}
    assert s["tags"] == {"n": 1, "jaccard": 0.5}


class FakeJudge:
    last_model = "judge"

    def __init__(self):
        self.calls = 0

    def chat_json(self, messages, schema):
        self.calls += 1
        if "FAIL" in messages[-1]["content"]:
            from pipeline.llm import LLMError
            raise LLMError("x")
        return schema(flavor_tags=["Lemon", "made-up"], acidity=5, body=None, sweetness=2, is_decaf=True)


def test_label_gold_fills_only_empty_rows(tmp_path):
    p = tmp_path / "g.csv"
    base = {c: "" for c in GOLD_COLUMNS}
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        w.writerow({**base, "key": "a", "name": "A", "text": "bright lemon"})
        w.writerow({**base, "key": "b", "name": "B", "text": "x", "gold_acidity": "1"})   # human label kept
        w.writerow({**base, "key": "c", "name": "C", "text": "FAIL"})
    judge = FakeJudge()
    assert label_gold(p, judge, ["lemon", "honey"]) == 1
    rows = {r["key"]: r for r in csv.DictReader(p.open(encoding="utf-8-sig"))}
    assert (rows["a"]["gold_acidity"], rows["a"]["gold_body"], rows["a"]["gold_sweetness"]) == ("5", "", "2")
    assert (rows["a"]["gold_is_decaf"], rows["a"]["gold_tags"]) == ("1", "lemon")
    assert rows["b"]["gold_acidity"] == "1" and rows["b"]["gold_body"] == ""
    assert rows["c"]["gold_acidity"] == ""
    assert judge.calls == 2
