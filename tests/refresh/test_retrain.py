from scripts.refresh.retrain import compare, metrics, render


def test_metrics_read_each_models_held_out_numbers():
    assert metrics("tag", {"holdout": {"model": {"f1": 0.73}}}) == {"f1": 0.73}
    attr = {"attrs": {"acidity": {"holdout": {"model": {"within1": 0.83}}}}}
    assert metrics("attr", attr) == {"acidity": 0.83, "body": None, "sweetness": None}
    feat = {"cv": {"body": {"table": {"ridge": {"within1": 0.6}}}}}
    assert metrics("feature", feat)["body"] == 0.6


def test_compare_needs_a_real_gain_and_no_real_drop():
    assert compare({"f1": 0.73}, {"f1": 0.745})["improved"]
    assert not compare({"f1": 0.73}, {"f1": 0.735})["improved"]                      # < 0.01: noise
    assert not compare({"a": 0.8, "b": 0.7}, {"a": 0.85, "b": 0.69})["improved"]     # b dropped 0.01
    assert compare({"a": 0.8, "b": None}, {"a": 0.82, "b": 0.5})["delta"] == {"a": 0.02}
    assert not compare({"a": None}, {"a": 0.9})["improved"]


def test_render_lists_skipped_models():
    md = render("2026-10-05", {"tag": compare({"f1": 0.7}, {"f1": 0.72}), "feature": {"skipped": "beans 없음"}})
    assert "| tag | f1 | 0.7 | 0.72 | 0.02 |" in md and "beans 없음" in md and "개선된 모델: tag" in md
