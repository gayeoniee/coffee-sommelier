import json
import random

import numpy as np
from sklearn.linear_model import Ridge

from app.core.featuremodel import (
    FEATURES, FeatureModel, altitude_from_text, bean_features, evidence_line, feature_label_ko,
)
from pipeline import settings


def test_bean_features_one_hots_and_counts():
    f = bean_features(origin_country="Ethiopia", process="washed", roast_level="light", altitude_m=1950,
                      text="예가체프 게이샤", note_categories=["fruity", "fruity", "floral", "fruity", "fruity"],
                      neighbor_value=3.5)
    assert f == {"region_east_africa": 1.0, "country_Ethiopia": 1.0, "alt_1600_2000": 1.0, "process_washed": 1.0,
                 "roast_light": 1.0, "variety_geisha": 1.0, "notes_fruity": 3.0, "notes_floral": 1.0, "nbr": 0.5}
    assert set(f) <= set(FEATURES)


def test_bean_features_blend_decaf_and_unknowns():
    f = bean_features(origin_country=None, process=None, roast_level="dark", is_decaf=True,
                      decaf_process="sugarcane-ea")
    assert f == {"blend_or_unknown_origin": 1.0, "roast_dark": 1.0, "decaf": 1.0, "decaf_sugarcane": 1.0}


def test_altitude_from_text():
    assert altitude_from_text("콜롬비아 우일라 1,900m 워시드") == 1900
    assert altitude_from_text("해발 1,600~2,000m") == 1800
    assert altitude_from_text("1800-2000 masl") == 1900
    assert altitude_from_text("200g 원두") is None
    assert altitude_from_text(None) is None


def test_bean_features_reads_altitude_from_text_when_not_given():
    assert "alt_ge2000" in bean_features(origin_country="Kenya", process=None, roast_level=None,
                                         text="케냐 2,100m SL28")
    assert "variety_kenyan_sl" in bean_features(origin_country="Kenya", process=None, roast_level=None,
                                                text="케냐 SL28")


def test_predict_matches_sklearn_ridge():
    rng = random.Random(0)
    names = [f for f in FEATURES]
    X = np.array([[rng.choice((0.0, 1.0)) for _ in names] for _ in range(60)])
    y = np.array([rng.uniform(1, 5) for _ in range(60)])
    m = Ridge(alpha=3.0).fit(X, y)
    fm = FeatureModel.from_doc({"attrs": {"acidity": {"intercept": float(m.intercept_),
                                                      "weights": {n: float(c) for n, c in zip(names, m.coef_)}}}})
    for row in X[:10]:
        feats = {n: v for n, v in zip(names, row) if v}
        value, contrib = fm.predict({"acidity": feats})["acidity"]
        assert abs(value - min(5, max(1, m.predict(row[None])[0]))) < 0.006
        assert abs(sum(contrib.values()) + m.intercept_ - m.predict(row[None])[0]) < 1e-9


def test_predict_skips_attributes_not_shipped_and_clips():
    fm = FeatureModel.from_doc({"attrs": {"acidity": {"intercept": 4.8, "weights": {"roast_light": 1.0}}}})
    out = fm.predict({"acidity": {"roast_light": 1.0}, "body": {"roast_light": 1.0}})
    assert set(out) == {"acidity"}
    assert out["acidity"][0] == 5.0


def test_evidence_line_korean_top_contributors():
    line = evidence_line("acidity", {"alt_1600_2000": 0.32, "process_washed": 0.2, "roast_dark": -0.3,
                                     "notes_other": 0.01}, altitude_m=1900)
    assert line == "특징 모델: 고지대(1,900m)·워시드 → 산미↑, 강배전 → 산미↓"
    assert evidence_line("body", {"notes_other": 0.01}) is None
    assert feature_label_ko("country_Ethiopia") == "에티오피아"
    # with the shown value + the model's baseline: the arrows are relative to that baseline
    assert evidence_line("acidity", {"roast_light": 0.3, "roast_dark": -0.2}, value=2.94, base=2.2) ==         "특징 모델 산미 2.9/5: 약배전 ↑, 강배전 ↓ (기준값 2.2)"


def test_load_missing_file_returns_none(tmp_path):
    assert FeatureModel.load(tmp_path / "nope.json") is None



def test_shipped_config_loads_and_only_uses_known_features():
    p = settings.CONFIG_DIR / "feature_model_open.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    fm = FeatureModel.load(p)
    assert fm is not None and set(fm.models) <= {"acidity", "body", "sweetness"}
    for spec in doc["attrs"].values():
        assert set(spec["weights"]) <= set(FEATURES)


def test_tag_logit_matches_sklearn_logistic_regression():
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(0)
    names = ["region_east_africa", "process_washed", "roast_dark"]
    X = rng.integers(0, 2, size=(60, 3)).astype(float)
    y = (X[:, 0] + rng.normal(0, 0.5, 60) > 0.5).astype(int)
    m = LogisticRegression().fit(X, y)
    fm = FeatureModel.from_doc({"attrs": {}, "tags": {
        "features": names, "threshold": 0.0, "max_tags": 5,
        "tags": {"lemon": {"intercept": float(m.intercept_[0]), "weights": dict(zip(names, m.coef_[0].tolist()))}}}})
    for row in X[:10]:
        feats = {n: v for n, v in zip(names, row) if v}
        assert abs(fm.tag_model.probs(feats)[0][1] - m.predict_proba(row[None])[0, 1]) < 1e-9


def test_support_bucket_from_the_input_itself():
    from app.core.featuremodel import support_bucket
    from app.models import ParsedBean
    t2c, ko = {"lemon": "fruity"}, {"lemon": "레몬"}
    assert support_bucket("sweetness", ParsedBean(text="달콤한 에티오피아"), t2c, ko) == "cue"
    assert support_bucket("sweetness", ParsedBean(text="Ethiopia, lemon", origin_country="Ethiopia"), t2c, ko) == "notes"
    assert support_bucket("acidity", ParsedBean(text="에티오피아 워시드", origin_country="Ethiopia"), t2c, ko) == "facts"
    assert support_bucket("body", ParsedBean(text="하우스 블렌드"), t2c, ko) == "sparse"


def test_shipped_config_v3_blocks_load():
    doc = json.loads((settings.CONFIG_DIR / "feature_model_open.json").read_text(encoding="utf-8"))
    fm = FeatureModel.from_doc(doc)
    if "tags" in doc:
        assert fm.tag_model is not None and fm.tag_model.tags and 0 < fm.tag_model.threshold < 1
    if "abstain" in doc:
        assert set(fm.abstain) <= {"acidity", "body", "sweetness"}
    if "calibration" in doc:
        assert all(set(v.values()) <= {"high", "medium", "low"} for v in fm.calibration.values())
