import csv
import json

import pytest

from pipeline.gold import GOLD_COLUMNS, read_gold_rows, label_gold, sample_gold, score_gold
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
    rows = read_gold_rows(out)
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
    rows = {r["key"]: r for r in read_gold_rows(p)}
    assert (rows["a"]["gold_acidity"], rows["a"]["gold_body"], rows["a"]["gold_sweetness"]) == ("5", "", "2")
    assert (rows["a"]["gold_is_decaf"], rows["a"]["gold_tags"]) == ("1", "lemon")
    assert rows["b"]["gold_acidity"] == "1" and rows["b"]["gold_body"] == ""
    assert rows["c"]["gold_acidity"] == ""
    assert judge.calls == 2


def test_sample_gold_records_value_origin(tmp_path):
    from pipeline.records import TaxonomyNode
    norm, enr = tmp_path / "n", tmp_path / "e"
    base = dict(source="t", collected_at="x")
    write_jsonl(norm / "coffees.jsonl", [
        CoffeeRecord(key="src", name="src", acidity=4, flavor_tags=["honey"], **base),   # values from the source
        CoffeeRecord(key="rul", name="rul", **base),                                     # rule tags, LLM scores
        CoffeeRecord(key="llm", name="llm", **base),                                     # LLM tags and scores
        CoffeeRecord(key="emp", name="emp", **base),                                     # nothing filled
    ])
    write_jsonl(enr / "coffees.jsonl", [
        CoffeeRecord(key="src", name="src", acidity=4, flavor_tags=["honey"], **base),
        CoffeeRecord(key="rul", name="rul", acidity=2, flavor_tags=["lemon"], **base),
        CoffeeRecord(key="llm", name="llm", body=5, flavor_tags=["honey"], **base),
        CoffeeRecord(key="emp", name="emp", **base),
    ])
    write_jsonl(norm / "reviews.jsonl", [
        ReviewRecord(key=f"r{k}", coffee_key=k, text=("lemon zest " if k == "rul" else "nice cup ") * 400, **base)
        for k in ("src", "rul", "llm", "emp")])
    write_jsonl(norm / "taxonomy.jsonl", [TaxonomyNode(key=f"sca:x>{v}", level=2, name_en=v) for v in ("lemon", "honey")])
    ok = {"status": "ok", "hash": "h", "model": "m", "output": {}}
    (enr / "cache.jsonl").write_text("\n".join(json.dumps({**ok, "key": k}) for k in ("rul", "llm"))
                                     + "\n" + json.dumps({"key": "emp", "status": "failed"}) + "\n", encoding="utf-8")
    out = tmp_path / "g.csv"
    sample_gold(enr, norm, out, n=10)
    rows = {r["key"]: r for r in read_gold_rows(out)}
    got = {k: tuple(r[f"origin_{f}"] for f in ("acidity", "body", "sweetness", "tags")) for k, r in rows.items()}
    assert got == {"src": ("source", "none", "none", "source"),
                   "rul": ("llm", "none", "none", "rule"),
                   "llm": ("none", "llm", "none", "llm"),
                   "emp": ("none", "none", "none", "none")}
    assert len(rows["rul"]["text"]) == 3000


def test_score_gold_by_origin_and_bad_cells(tmp_path):
    p = tmp_path / "g.csv"
    base = {c: "" for c in GOLD_COLUMNS}
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        w.writerow({**base, "key": "a", "pred_acidity": "4", "gold_acidity": "4.0", "origin_acidity": "source",
                    "pred_tags": "lemon", "gold_tags": "lemon", "origin_tags": "rule"})
        w.writerow({**base, "key": "b", "pred_acidity": "2", "gold_acidity": "3", "origin_acidity": "llm",
                    "pred_tags": "honey", "gold_tags": "lemon", "origin_tags": "llm"})
        w.writerow({**base, "key": "c", "pred_acidity": "", "gold_acidity": "?", "origin_acidity": "none"})
    s = score_gold(p)
    assert s["acidity"] == {"n": 2, "exact": 0.5, "within1": 1.0}
    assert s["by_origin"]["acidity"] == {"source": {"n": 1, "exact": 1.0, "within1": 1.0},
                                         "llm": {"n": 1, "exact": 0.0, "within1": 1.0}}
    assert s["by_origin"]["tags"] == {"rule": {"n": 1, "jaccard": 1.0}, "llm": {"n": 1, "jaccard": 0.0}}
