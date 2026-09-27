import json
import logging
import time
from pathlib import Path

from app.core.attrmodel import AttrModel, _Mlp, _Ridge

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def test_app_never_imports_numpy():
    """Same rule as tests/app/test_tagmodel.py -- app/ deploys without the "pipeline" dependency group, so
    this pure-Python forward pass must never import numpy."""
    import ast

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
    with caplog.at_level(logging.WARNING, logger="coffee.attrmodel"):
        assert AttrModel.load(missing) is None
        assert AttrModel.load(missing) is None
    assert sum("no learned attribute model found" in r.message for r in caplog.records) == 1


def test_ridge_predict_matches_hand_computed_value():
    m = AttrModel(embed_model="test", models={"acidity": _Ridge(w=[0.5, -0.25], b=3.0)})
    got = m.predict([2.0, 4.0])
    # 0.5*2 + -0.25*4 + 3.0 = 1.0 - 1.0 + 3.0 = 3.0
    assert got["acidity"] == 3.0
    assert got["body"] is None and got["sweetness"] is None       # not shipped for this toy model


def test_mlp_predict_matches_hand_computed_value():
    # 2 -> 2 (relu) -> 1 (identity): hidden = relu([1*1+0, 1*0+0*1]) = relu([1, 0]) = [1, 0]
    # out = 1*3 + 0*(-1) + 0.5 = 3.5
    m = _Mlp(w1=[[1.0, 0.0], [0.0, 1.0]], b1=[0.0, 0.0], w2=[[3.0], [-1.0]], b2=[0.5])
    assert abs(m.predict([1.0, 0.0]) - 3.5) < 1e-9


def test_predict_clips_to_1_5_and_rounds_to_2_decimals():
    m = AttrModel(embed_model="test", models={
        "acidity": _Ridge(w=[100.0], b=0.0),      # will blow way past 5
        "body": _Ridge(w=[-100.0], b=0.0),        # will blow way under 1
        "sweetness": _Ridge(w=[0.333333], b=3.0),
    })
    got = m.predict([1.0])
    assert got["acidity"] == 5.0 and got["body"] == 1.0
    assert got["sweetness"] == round(3.333333, 2)


def test_load_reads_plain_and_gzipped_json_with_mixed_ridge_and_mlp(tmp_path):
    doc = {"embed_model": "m", "attrs": {
        "acidity": {"type": "ridge", "W": [1.0, 0.0], "b": 0.5},
        "body": {"type": "mlp", "W1": [[1.0], [0.0]], "b1": [0.0], "W2": [[2.0]], "b2": [0.0]},
    }}
    plain = tmp_path / "attr_model.json"
    plain.write_text(json.dumps(doc), encoding="utf-8")
    m = AttrModel.load(plain)
    assert m is not None
    got = m.predict([1.0, 0.0])
    assert got["acidity"] == 1.5 and got["body"] == 2.0 and got["sweetness"] is None

    import gzip
    gz = tmp_path / "attr_model.json.gz"
    with gzip.open(gz, "wt", encoding="utf-8") as f:
        json.dump(doc, f)
    m2 = AttrModel.load(gz)
    assert m2 is not None and m2.predict([1.0, 0.0])["acidity"] == 1.5


def test_load_picks_open_file_under_data_variant_open(tmp_path, monkeypatch):
    from app import config
    from pipeline import settings

    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_VARIANT", "open")
    doc_open = {"embed_model": "m", "attrs": {"acidity": {"type": "ridge", "W": [1.0], "b": 0.0}}}
    (tmp_path / "attr_model_open.json").write_text(json.dumps(doc_open), encoding="utf-8")
    m = AttrModel.load()
    assert m is not None and m.predict([2.0])["acidity"] == 2.0


def test_matches_tiny_sklearn_reference_ridge_and_mlp():
    import random

    from sklearn.linear_model import Ridge
    from sklearn.neural_network import MLPRegressor

    rng = random.Random(0)
    n_features, n_samples = 20, 200
    X = [[rng.uniform(-1, 1) for _ in range(n_features)] for _ in range(n_samples)]
    y = [sum(row) * 0.3 + rng.uniform(-0.05, 0.05) + 3.0 for row in X]

    ridge = Ridge(alpha=1.0)
    ridge.fit(X, y)
    r = _Ridge(w=[round(float(w), 5) for w in ridge.coef_], b=round(float(ridge.intercept_), 5))
    for x in X[:10]:
        assert abs(r.predict(x) - ridge.predict([x])[0]) < 1e-4

    mlp = MLPRegressor(hidden_layer_sizes=(8,), max_iter=500, random_state=0)
    mlp.fit(X, y)
    assert mlp.out_activation_ == "identity"
    W1, W2 = mlp.coefs_
    b1, b2 = mlp.intercepts_
    m = _Mlp(w1=[[round(float(x), 5) for x in row] for row in W1], b1=[round(float(x), 5) for x in b1],
             w2=[[round(float(x), 5) for x in row] for row in W2], b2=[round(float(x), 5) for x in b2])
    for x in X[:10]:
        assert abs(m.predict(x) - mlp.predict([x])[0]) < 1e-4


def test_attr_model_enabled_by_default_in_both_variants(monkeypatch):
    """Unlike the tag model, the attribute regressor has a licence-clean open variant of its own -- DATA_VARIANT
    alone never disables it, only an explicit ATTR_MODEL=off does."""
    from app import config
    from app.graphs import _attr_model_enabled

    monkeypatch.delenv("ATTR_MODEL", raising=False)
    monkeypatch.setattr(config, "DATA_VARIANT", "full")
    assert _attr_model_enabled() is True
    monkeypatch.setattr(config, "DATA_VARIANT", "open")
    assert _attr_model_enabled() is True


def test_attr_model_disabled_by_explicit_env_override(monkeypatch):
    from app.graphs import _attr_model_enabled

    monkeypatch.setenv("ATTR_MODEL", "off")
    assert _attr_model_enabled() is False


def test_predict_latency_under_50ms():
    def pseudo_random(n, seed):
        x = seed
        out = []
        for _ in range(n):
            x = (1103515245 * x + 12345) % (2 ** 31)
            out.append((x / (2 ** 31)) * 2 - 1)
        return out

    n_features, n_hidden = 1024, 128
    models = {}
    for i, a in enumerate(("acidity", "body", "sweetness")):
        w1 = [pseudo_random(n_hidden, i * 1000 + j) for j in range(n_features)]
        b1 = pseudo_random(n_hidden, i * 1000 + 999)
        w2 = [[pseudo_random(1, i * 1000 + 2000 + j)[0]] for j in range(n_hidden)]
        b2 = pseudo_random(1, i * 1000 + 2999)
        models[a] = _Mlp(w1=w1, b1=b1, w2=w2, b2=b2)
    m = AttrModel(embed_model="test", models=models)
    embedding = pseudo_random(n_features, 12345)

    n_calls = 50
    t0 = time.perf_counter()
    for _ in range(n_calls):
        m.predict(embedding)
    elapsed = (time.perf_counter() - t0) / n_calls
    assert elapsed < 0.05, f"predict took {elapsed * 1000:.2f}ms per call, budget is 50ms"
