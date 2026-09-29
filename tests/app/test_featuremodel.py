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


def test_neighbour_support_weight_sums_similarity_of_labelled_neighbours():
    from app.core.featuremodel import neighbour_support_weight
    from app.models import Neighbor
    ns = [Neighbor(1, "a", 0.8, None, None, 3.0), Neighbor(2, "b", 0.7, None, None, None),
         Neighbor(3, "c", 0.005, None, None, 4.0)]     # similarity floored at 0.01 (matches predict_from_neighbors)
    assert neighbour_support_weight(ns, "sweetness") == 0.81
    assert neighbour_support_weight(ns, "acidity") == 0.0
    assert neighbour_support_weight([], "sweetness") == 0.0
    assert neighbour_support_weight(None, "sweetness") == 0.0


def test_from_doc_parses_abstain_min_weight():
    fm = FeatureModel.from_doc({"attrs": {}, "abstain": {"attrs": ["sweetness"], "min_weight": {"sweetness": 1.4}}})
    assert fm.abstain == ("sweetness",) and fm.abstain_min_weight == {"sweetness": 1.4}
    fm2 = FeatureModel.from_doc({"attrs": {}, "abstain": {"attrs": ["sweetness"]}})
    assert fm2.abstain_min_weight == {}


def _sweet_model(min_weight=None):
    doc = {"attrs": {"sweetness": {"intercept": 3.8, "weights": {"roast_dark": 0.5}}},
           "abstain": {"attrs": ["sweetness"], **({"min_weight": {"sweetness": min_weight}} if min_weight else {})}}
    return FeatureModel.from_doc(doc)


def test_with_feature_model_weighted_support_answers_below_the_raw_neighbour_count():
    from app.core.featuremodel import with_feature_model
    from app.models import Neighbor, ParsedBean, Prediction
    parsed = ParsedBean(text="브라질 강배전", origin_country="Brazil", roast_level="dark")
    pred = Prediction(acidity=3.0, body=3.0, sweetness=None, confidence="high", tags=[], evidence=[], n_neighbors=10)
    # only 2 of the neighbours carry sweetness (below predict_from_neighbors' MIN_NEIGHBORS=3, so pred.sweetness is
    # already None), but both are close matches -- similarity-weighted evidence 1.6 clears a 1.4 threshold.
    neighbors = [Neighbor(1, "a", 0.9, None, None, 3.5), Neighbor(2, "b", 0.7, None, None, 4.0),
                Neighbor(3, "c", 0.5, None, None, None)]
    out = with_feature_model(pred, _sweet_model(min_weight=1.4), parsed, {}, {}, neighbors=neighbors)
    assert out.sweetness == 4.3 and "단맛: 근거 부족" not in out.evidence            # 3.8 + roast_dark 0.5


def test_with_feature_model_weighted_support_still_abstains_below_threshold():
    from app.core.featuremodel import with_feature_model
    from app.models import Neighbor, ParsedBean, Prediction
    parsed = ParsedBean(text="브라질 강배전", origin_country="Brazil", roast_level="dark")
    pred = Prediction(acidity=3.0, body=3.0, sweetness=None, confidence="high", tags=[], evidence=[], n_neighbors=10)
    neighbors = [Neighbor(1, "a", 0.5, None, None, 3.5)]                          # weight 0.5 < 1.4
    out = with_feature_model(pred, _sweet_model(min_weight=1.4), parsed, {}, {}, neighbors=neighbors)
    assert out.sweetness is None and "단맛: 근거 부족" in out.evidence


def test_with_feature_model_without_neighbors_arg_keeps_the_legacy_rule():
    """A caller that doesn't pass `neighbors` (e.g. degraded mode) gets the ADR 0016 behaviour even when the
    model configures a min_weight -- it never answers on weighted support it can't compute."""
    from app.core.featuremodel import with_feature_model
    from app.models import ParsedBean, Prediction
    parsed = ParsedBean(text="브라질 강배전", origin_country="Brazil", roast_level="dark")
    pred = Prediction(acidity=3.0, body=3.0, sweetness=None, confidence="high", tags=[], evidence=[], n_neighbors=10)
    out = with_feature_model(pred, _sweet_model(min_weight=1.4), parsed, {}, {})
    assert out.sweetness is None and "단맛: 근거 부족" in out.evidence
    # with a neighbour average present (pred.sweetness not None), the legacy rule still answers with no neighbors
    pred2 = Prediction(acidity=3.0, body=3.0, sweetness=3.6, confidence="high", tags=[], evidence=[], n_neighbors=10)
    out2 = with_feature_model(pred2, _sweet_model(min_weight=1.4), parsed, {}, {})
    assert out2.sweetness == 4.3


def test_shipped_config_v3_blocks_load():
    doc = json.loads((settings.CONFIG_DIR / "feature_model_open.json").read_text(encoding="utf-8"))
    fm = FeatureModel.from_doc(doc)
    if "tags" in doc:
        assert fm.tag_model is not None and fm.tag_model.tags and 0 < fm.tag_model.threshold < 1
    if "abstain" in doc:
        assert set(fm.abstain) <= {"acidity", "body", "sweetness"}
        assert set(fm.abstain_min_weight) <= set(fm.abstain) and all(v > 0 for v in fm.abstain_min_weight.values())
    if "calibration" in doc:
        assert all(set(v.values()) <= {"high", "medium", "low"} for v in fm.calibration.values())
