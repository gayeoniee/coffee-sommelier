import ast
import json
import logging
import time
from pathlib import Path

from app.core.tagmodel import TagModel

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def test_app_never_imports_numpy():
    """app/ deploys without the "pipeline" dependency group (Dockerfile: uv sync --no-default-groups), so
    numpy (a pipeline-only dep) must never be imported from anywhere under app/ -- app/core/tagmodel.py's
    forward pass is pure Python specifically to keep this true."""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "numpy" for a in node.names):
                offenders.append(str(path))
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "numpy":
                offenders.append(str(path))
    assert not offenders, f"app/ must not import numpy (pipeline-only dependency): {offenders}"


def test_load_missing_file_returns_none_and_logs_once(tmp_path, caplog):
    missing = tmp_path / "does_not_exist.json"
    with caplog.at_level(logging.WARNING, logger="coffee.tagmodel"):
        assert TagModel.load(missing) is None
        assert TagModel.load(missing) is None      # second call: still None, doesn't crash
    assert sum("no learned tag model found" in r.message for r in caplog.records) == 1


def _toy_model(threshold=0.3) -> TagModel:
    # 2 -> 2 (relu) -> 2 (sigmoid), weights chosen so the math is easy to hand-check.
    return TagModel(labels=["lemon", "chocolate"], threshold=threshold, embed_model="test",
                    w1=[[1.0, 0.0], [0.0, 1.0]], b1=[0.0, 0.0],
                    w2=[[2.0, -2.0], [-2.0, 2.0]], b2=[0.0, 0.0])


def test_predict_sorted_desc_and_matches_hand_computed_values():
    m = _toy_model()
    preds = m.predict([1.0, 0.0])
    # hidden = relu([1,0]) = [1,0]; out = [1*2 + 0*-2, 1*-2 + 0*2] = [2, -2] -> sigmoid
    from math import exp
    expected_lemon = 1 / (1 + exp(-2))
    expected_choc = 1 / (1 + exp(2))
    assert [t for t, _ in preds] == ["lemon", "chocolate"]     # sorted descending by probability
    assert abs(dict(preds)["lemon"] - expected_lemon) < 1e-9
    assert abs(dict(preds)["chocolate"] - expected_choc) < 1e-9


def test_tags_applies_threshold_and_cap():
    m = _toy_model(threshold=0.5)
    assert [t for t, _ in m.tags([1.0, 0.0])] == ["lemon"]           # chocolate's prob is well under 0.5
    assert [t for t, _ in m.tags([1.0, 0.0], threshold=0.01)] == ["lemon", "chocolate"]
    assert len(m.tags([1.0, 0.0], threshold=0.0, cap=1)) == 1


def test_load_reads_plain_and_gzipped_json(tmp_path):
    doc = {"tags": ["lemon"], "threshold": 0.3, "embed_model": "m",
          "W1": [[1.0], [0.0]], "b1": [0.0], "W2": [[1.0]], "b2": [0.0]}
    plain = tmp_path / "tag_model.json"
    plain.write_text(json.dumps(doc), encoding="utf-8")
    m = TagModel.load(plain)
    assert m is not None and m.labels == ["lemon"] and m.threshold == 0.3

    import gzip
    gz = tmp_path / "tag_model.json.gz"
    with gzip.open(gz, "wt", encoding="utf-8") as f:
        json.dump(doc, f)
    m2 = TagModel.load(gz)
    assert m2 is not None and m2.labels == ["lemon"]


def test_matches_tiny_sklearn_reference_on_random_data():
    import random

    from sklearn.neural_network import MLPClassifier

    rng = random.Random(0)
    n_features, n_tags, n_samples = 20, 3, 200
    X = [[rng.uniform(-1, 1) for _ in range(n_features)] for _ in range(n_samples)]
    Y = [[rng.randint(0, 1) for _ in range(n_tags)] for _ in range(n_samples)]
    clf = MLPClassifier(hidden_layer_sizes=(8,), max_iter=200, random_state=0)
    clf.fit(X, Y)
    assert clf.out_activation_ == "logistic"

    W1, W2 = clf.coefs_
    b1, b2 = clf.intercepts_
    m = TagModel(labels=["t0", "t1", "t2"], threshold=0.3, embed_model="test",
                w1=[[round(float(x), 5) for x in row] for row in W1], b1=[round(float(x), 5) for x in b1],
                w2=[[round(float(x), 5) for x in row] for row in W2], b2=[round(float(x), 5) for x in b2])

    for x in X[:10]:
        expected = clf.predict_proba([x])[0]
        got = dict(m.predict(x))
        for i, label in enumerate(["t0", "t1", "t2"]):
            assert abs(got[label] - expected[i]) < 1e-4


def test_tag_model_enabled_for_open_data_variant_the_gate_is_in_load_not_here(monkeypatch):
    """_tag_model_enabled() no longer hard-disables the open variant: Goal B2 (docs/adr/0009) lets the open
    deployment load its OWN licence-clean tag_model_open.json when one has been shipped. The licence gate on
    the coffeereview_kaggle-derived full model lives in TagModel.load()'s file selection instead (see the test
    below), not in this flag."""
    from app import config
    from app.graphs import _tag_model_enabled

    monkeypatch.setattr(config, "DATA_VARIANT", "open")
    monkeypatch.delenv("TAG_MODEL", raising=False)
    assert _tag_model_enabled() is True


def test_load_under_open_variant_never_resolves_to_the_full_licence_restricted_file(tmp_path, monkeypatch):
    """Under DATA_VARIANT=open, load() must only ever look for tag_model_open.json -- never fall back to
    tag_model.json, even when the open file doesn't exist (the open deployment then loads no tag model at all,
    same as before Goal B2)."""
    from app import config
    from pipeline import settings

    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_VARIANT", "open")
    doc = {"tags": ["lemon"], "threshold": 0.3, "embed_model": "m",
          "W1": [[1.0], [0.0]], "b1": [0.0], "W2": [[1.0]], "b2": [0.0]}
    (tmp_path / "tag_model.json").write_text(json.dumps(doc), encoding="utf-8")   # full file present, but...
    assert TagModel.load() is None                                                # ...never loaded under open

    (tmp_path / "tag_model_open.json").write_text(json.dumps(doc), encoding="utf-8")
    m = TagModel.load()
    assert m is not None and m.labels == ["lemon"]


def test_tag_model_disabled_by_explicit_env_override(monkeypatch):
    from app import config
    from app.graphs import _tag_model_enabled

    monkeypatch.setattr(config, "DATA_VARIANT", "full")
    monkeypatch.setenv("TAG_MODEL", "off")
    assert _tag_model_enabled() is False


def test_tag_model_enabled_by_default_for_non_open_variants(monkeypatch):
    from app import config
    from app.graphs import _tag_model_enabled

    monkeypatch.setattr(config, "DATA_VARIANT", "full")
    monkeypatch.delenv("TAG_MODEL", raising=False)
    assert _tag_model_enabled() is True


def test_predict_latency_under_50ms():
    rng_seed = 12345

    def pseudo_random(n, seed):
        x = seed
        out = []
        for _ in range(n):
            x = (1103515245 * x + 12345) % (2 ** 31)
            out.append((x / (2 ** 31)) * 2 - 1)
        return out

    n_features, n_hidden, n_tags = 1024, 128, 54
    w1 = [pseudo_random(n_hidden, i) for i in range(n_features)]
    b1 = pseudo_random(n_hidden, 999)
    w2 = [pseudo_random(n_tags, i + 1000) for i in range(n_hidden)]
    b2 = pseudo_random(n_tags, 1999)
    m = TagModel(labels=[f"t{i}" for i in range(n_tags)], threshold=0.3, embed_model="test",
                w1=w1, b1=b1, w2=w2, b2=b2)
    embedding = pseudo_random(n_features, rng_seed)

    n_calls = 50
    t0 = time.perf_counter()
    for _ in range(n_calls):
        m.tags(embedding)
    elapsed = (time.perf_counter() - t0) / n_calls
    assert elapsed < 0.05, f"predict took {elapsed * 1000:.2f}ms per call, budget is 50ms"
