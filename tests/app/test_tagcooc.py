"""app/core/tagcooc.py -- open-variant flavor-tag co-occurrence (docs/adr/0018-open-tag-cooccurrence.md)."""
import json
from pathlib import Path

from app.core.tagcooc import COOC_FILE, TagCooc, cooc_line, with_cooc_fill
from app.models import Prediction

ROOT = Path(__file__).resolve().parents[2]


def _table():
    return TagCooc.from_beans(
        [{"caramelized", "chocolate", "nutty"}] * 4 + [{"caramelized", "chocolate"}] * 2 + [{"caramelized"}] * 2
        + [{"chocolate", "cocoa"}] * 6 + [{"jasmine", "peach"}] * 3 + [{"lemon"}] * 3,
        parent={"chocolate": "cocoa", "lemon": "citrus fruit"})


def test_counts_and_roundtrip():
    t = _table()
    assert t.n_beans == 20 and t.tag_count["caramelized"] == 8 and t.pair_count["caramelized"]["chocolate"] == 6
    assert TagCooc.from_doc(json.loads(json.dumps(t.to_doc()))) == t
    assert TagCooc.from_beans([set(), {"x"}]).n_beans == 1        # beans without tags do not count


def test_gate_support_pair_share_lift_and_order():
    t = _table()
    # caramelized (8 beans): chocolate 6/8 (lift 0.75/0.6 = 1.25), nutty 4/8 (lift 0.5/0.2 = 2.5)
    assert [b for b, *_ in t.candidates(["caramelized"])] == ["chocolate", "nutty"]
    assert t.candidates(["caramelized"])[0] == ("chocolate", "caramelized", 8, 6)
    assert [b for b, *_ in t.candidates(["caramelized"], min_lift=1.5)] == ["nutty"]
    assert t.candidates(["jasmine"]) == []                         # 3 beans < min_support 5
    assert [b for b, *_ in t.candidates(["jasmine"], min_support=3)] == ["peach"]
    assert t.candidates(["caramelized"], min_pair=7) == []
    assert t.candidates(["unknown"]) == []


def test_same_branch_and_shown_tags_are_never_added():
    t = _table()
    # chocolate's partners: caramelized 6/12, cocoa 6/12, nutty 4/12 -- cocoa is chocolate's own SCA node
    assert [b for b, *_ in t.candidates(["chocolate"])] == ["caramelized", "nutty"]
    assert [b for b, *_ in t.candidates(["caramelized"], shown=["chocolate"])] == ["nutty"]


def test_with_cooc_fill_tops_up_to_target_with_evidence():
    t = _table()
    pred = Prediction(acidity=3.0, body=3.0, sweetness=None, confidence="medium", tags=["caramelized"],
                      evidence=["문구의 향미: 캐러멜", "유사 원두 산미 평균 3.0/5"], n_neighbors=10)
    ko = {"caramelized": "캐러멜", "chocolate": "초콜릿"}
    out = with_cooc_fill(pred, ["caramelized"], t, ko)
    assert out.tags == ["caramelized", "chocolate"]
    assert out.evidence == ["문구의 향미: 캐러멜", "'캐러멜' 표기 원두 8개 중 6개가 '초콜릿'도 언급", "유사 원두 산미 평균 3.0/5"]
    assert with_cooc_fill(pred, ["caramelized"], t, ko, target=3).tags == ["caramelized", "chocolate", "nutty"]
    # unchanged: no table, no guest tag, already enough tags
    assert with_cooc_fill(pred, ["caramelized"], None) is pred
    assert with_cooc_fill(pred, [], t) is pred
    assert with_cooc_fill(pred, ["caramelized"], t, target=1) is pred
    assert cooc_line("a", "b", 5, 3) == "'a' 표기 원두 5개 중 3개가 'b'도 언급"


def test_load_missing_is_none(tmp_path):
    assert TagCooc.load(tmp_path / "nope.json") is None


def test_shipped_table_is_licence_clean_and_in_config():
    """config/ ships in the image (.dockerignore); the table's one writer is scripts/build_tag_cooc.py."""
    path = ROOT / "config" / COOC_FILE
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(doc["sources"]) == {"roasters_kr", "shopify", "shopify_gauged"}
    assert "roasterdb" not in json.dumps(doc["sources"]) and "coffeereview" not in json.dumps(doc["sources"])
    assert "build_tag_cooc.py" in doc["what"]
    t = TagCooc.load(path)
    assert t is not None and t.n_beans == doc["n_beans"] and t.n_beans == sum(doc["by_source"].values())
    assert t.parent.get("chocolate") == "cocoa"
    assert "!config/" in (ROOT / ".dockerignore").read_text(encoding="utf-8")
