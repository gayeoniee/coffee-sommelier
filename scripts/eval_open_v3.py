"""Open variant v3 experiments (docs/adr/0016-open-variant-v3.md): body retry, sweetness abstention, calibrated
per-attribute confidence. Every candidate gets two evaluations:

  E1  grouped leave-one-roaster-out CV on the roasters' own gauges (ADR 0011 folds and targets; scripts/
      ablate_open_labels.py machinery: training_rows / matrix / pick_alpha / fit), out-of-fold per bean;
  E2  the Zenodo Q-grader panel (scripts/eval_zenodo_panel.py; evaluation only, never trained on), predictions
      through the analyze path's core functions with a candidate feature model.

Body: gauges (64) + shopify_gauged roaster-profile body labels ("A", altitude masked, ADR 0013) + explicit body
cues in licence-clean beans' own note text ("K": app.core.textcues.attr_cues / pipeline.enrich.ko_body_cue), ridge
with the neighbour feature. Ship rule: E1 within+-1 >= open neighbour average + 0.05 AND E2 (paired samples where
the neighbour average exists) MAE and within+-1 not worse than the neighbour average.

Sweetness abstention: the support signals the app can see at runtime (note words in the input, a sweetness cue,
roast/origin known) are measured against the out-of-fold error on E1; a rule is chosen on E1 only and then checked
on E2. Ship rule: accuracy on the answered cases improves on both, coverage >= 50% on E1.

Confidence: per attribute and support bucket, the E1 out-of-fold within+-1 rate -> high/medium/low (thresholds
below), written into config/feature_model_open.json by scripts/train_feature_model.py (CALIBRATION there reads
data/eval/open/phase5_open_v3.json).

    uv run python scripts/eval_open_v3.py            # writes data/eval/open/phase5_open_v3.json
"""
import json
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.featuremodel import FeatureModel, support_bucket  # noqa: E402
from app.core.textcues import attr_cues  # noqa: E402
from app.models import ATTRS  # noqa: E402
from pipeline import settings  # noqa: E402
from pipeline.enrich import ko_body_cue  # noqa: E402
from scripts.ablate_open_labels import CONFIGS, USE_NBR, cfg, fit, load_rows, matrix, pick_alpha, training_rows  # noqa: E402
from scripts.eval_zenodo_panel import (  # noqa: E402
    XLSX, CachedEmbedder, attr_metrics, download, predict, read_xlsx_rows, samples_from_rows,
)
from scripts.train_feature_model import OPEN_URL, R, score, spearman  # noqa: E402

OUT = settings.DATA_DIR / "eval" / "open" / "phase5_open_v3.json"
CACHE = settings.RAW_DIR / "open_tag_eval" / "v3_rows.pkl"
LICENCE_CLEAN = ("roasters_kr", "shopify", "shopify_gauged")
W_K = 0.5
CONF_HIGH, CONF_MEDIUM = 0.75, 0.55          # within+-1 rate -> confidence level
MIN_BUCKET = 10
SPEARMAN_HIGH = 0.3                          # "high" also needs rank information, not only a narrow label spread
ABSTAIN_RULE = "cue or neighbour value"      # chosen on E1 (coverage >= 50%, answered accuracy up), see ADR 0016


def level(m: dict) -> str:
    if m["within1"] >= CONF_HIGH and (m["spearman"] or 0) >= SPEARMAN_HIGH:
        return "high"
    return "medium" if m["within1"] >= CONF_MEDIUM and (m["spearman"] or 0) > 0 else "low"


# ---- rows --------------------------------------------------------------------------------------------------
def rows_with_cues(conn) -> list[dict]:
    if CACHE.exists():
        return pickle.loads(CACHE.read_bytes())
    rows = load_rows(conn)
    for r in rows:
        k = None
        if r["source"] in LICENCE_CLEAN:
            cue = attr_cues(f"{r['name']} {r['flavor_summary'] or ''}").get("body")
            ko = ko_body_cue(r["flavor_summary"] or "")
            k = cue[0] if cue else (4.5 if ko == 4 else 1.5 if ko == 2 else None)
        r["kbody"] = k
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(pickle.dumps(rows))
    return rows


def body_training(rows, held_out, c, use_k: bool, wk: float):
    tr = training_rows(rows, "body", held_out, c)
    if use_k:
        have = {id(t[0]) for t in tr}
        for r in rows:
            if (r["kbody"] is not None and r["roaster"] != held_out and not
                    (r["kind"] == "gauge" and r["labels"].get("body") is not None) and id(r) not in have
                    and not (r["kind"] == "A" and r["labels"].get("body") is not None and c["a"])):
                tr.append(({**r, "kind": "K"}, r["kbody"], wk))
    return tr


def oof(rows, attr, c, use_k=False, wk=W_K) -> dict[str, float]:
    """Out-of-fold predictions (key -> value) for the gauge beans of `attr`, recipe `c` (ablation config)."""
    data = [r for r in rows if r["kind"] == "gauge" and r["labels"].get(attr) is not None]
    out = {}
    for g in sorted({r["roaster"] for r in data}):
        tr = body_training(rows, g, c, use_k, wk) if attr == "body" else training_rows(rows, attr, g, c)
        X, _ = matrix([t[0] for t in tr], attr, c["pool"], USE_NBR[attr], c["bf"])
        y = np.array([t[1] for t in tr])
        w = np.array([t[2] for t in tr])
        grp = np.array([t[0]["roaster"] for t in tr])
        kinds = [t[0]["kind"] for t in tr]
        alpha = pick_alpha(X, y, w, grp, kinds, c["c"])
        m, _ = fit(X, y, w, grp, alpha, c["c"])
        test = [r for r in data if r["roaster"] == g]
        p = np.clip(m.predict(matrix(test, attr, c["pool"], USE_NBR[attr], c["bf"])[0]), 1, 5)
        out.update({r["key"]: float(v) for r, v in zip(test, p)})
    return out


def final_body_spec(rows, c, use_k, wk=W_K) -> dict:
    tr = body_training(rows, "__none__", c, use_k, wk)
    X, names = matrix([t[0] for t in tr], "body", c["pool"], True, False)
    y = np.array([t[1] for t in tr])
    w = np.array([t[2] for t in tr])
    grp = np.array([t[0]["roaster"] for t in tr])
    kinds = [t[0]["kind"] for t in tr]
    alpha = pick_alpha(X, y, w, grp, kinds, False)
    m, _ = fit(X, y, w, grp, alpha, False)
    return {"type": "ridge", "alpha": alpha, "intercept": round(float(m.intercept_), R),
            "weights": {n: round(float(v), R) for n, v in zip(names, m.coef_) if abs(v) >= 1e-4},
            "n_train": int(len(y)), "n_gauge": sum(k == "gauge" for k in kinds), "uses_nbr": True}


def e1_table(rows, attr, preds: dict[str, dict[str, float]]) -> dict:
    data = [r for r in rows if r["kind"] == "gauge" and r["labels"].get(attr) is not None]
    y = np.array([r["labels"][attr] for r in data])
    out = {}
    for name, p in preds.items():
        v = np.array([p[r["key"]] for r in data])
        out[name] = {**score(v, y), "spearman": spearman(v, y)}
    return out


def neighbour_oof(rows, attr) -> dict[str, float]:
    """Open neighbour average (pool with shopify_gauged, own roaster excluded); training-fold mean when absent."""
    data = [r for r in rows if r["kind"] == "gauge" and r["labels"].get(attr) is not None]
    out = {}
    for r in data:
        v = r["nbr"]["new"][attr]
        if v is None:
            v = float(np.mean([o["labels"][attr] for o in data if o["roaster"] != r["roaster"]]))
        out[r["key"]] = v
    return out


# ---- E2 ------------------------------------------------------------------------------------------------------
def zenodo(feature_model, repo, tax, embed, samples):
    """Per-sample predictions (open variant path) + the support signals."""
    from app.core.parse import parse_bean_text
    out = []
    for s in samples:
        pred, _ = predict(s, embed(s["text"]), repo, tax, feature_model=feature_model)
        parsed = parse_bean_text(s["text"])
        out.append({"id": s["id"], "truth": s["truth"], "pred": {a: getattr(pred, a) for a in ATTRS},
                    "bucket": {a: support_bucket(a, parsed, tax[0], tax[1]) for a in ATTRS}})
    return out


def paired(z_model, z_base, attr) -> dict:
    keys = [i for i, (m, b) in enumerate(zip(z_model, z_base))
            if m["truth"][attr] is not None and m["pred"][attr] is not None and b["pred"][attr] is not None]
    return {"model": attr_metrics([(z_model[i]["truth"][attr], z_model[i]["pred"][attr]) for i in keys]),
            "neighbour": attr_metrics([(z_base[i]["truth"][attr], z_base[i]["pred"][attr]) for i in keys]),
            "constant_3": attr_metrics([(z_model[i]["truth"][attr], 3.0) for i in keys])}


def main() -> int:
    from app.repo import Repo
    shipped = json.loads((settings.CONFIG_DIR / "feature_model_open.json").read_text(encoding="utf-8"))
    with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
        rows = rows_with_cues(conn)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "w_k": W_K,
              "k_body_labels": sum(r["kbody"] is not None for r in rows),
              "a_body_labels": sum(1 for r in rows if r["kind"] == "A" and r["labels"].get("body") is not None)}

    # ---- body -------------------------------------------------------------------------------------------------
    new = CONFIGS["~base (new pool)"]
    body_cands = {"gauges": (new, False), "+A": (CONFIGS["+A"], False), "+A wA=1": (CONFIGS["~A wA=1"], False),
                  "+K": (new, True), "+A+K": (CONFIGS["+A"], True)}
    bpreds = {"neighbour_avg": neighbour_oof(rows, "body")}
    for name, (c, k) in body_cands.items():
        bpreds[name] = oof(rows, "body", c, use_k=k)
    report["body_e1"] = e1_table(rows, "body", bpreds)
    from scripts.train_feature_model import SHIPPED_RECIPES
    # ADR 0020: which body candidate ships (the same config under its v3 name), None = neighbour average
    report["body_shipped"] = {"+A wA=1": "+A wA=1"}.get((SHIPPED_RECIPES.get("body") or (None, None))[1])

    embed = CachedEmbedder()
    samples = samples_from_rows(read_xlsx_rows(download(XLSX)))
    repo = Repo(OPEN_URL)
    try:
        tag_to_cat, tag_ko = repo.taxonomy()
        tax = (tag_to_cat, tag_ko, repo.tag_base_rates())
        z_base = zenodo(None, repo, tax, embed, samples)
        # the attribute heads only (v2 behaviour: no abstention, no tag model), whatever else the config carries
        z_ship = zenodo(FeatureModel.from_doc({"attrs": shipped["attrs"]}), repo, tax, embed, samples)
        report["body_e2"] = {}
        body_specs = {}
        for name, (c, k) in body_cands.items():
            spec = final_body_spec(rows, c, k)
            body_specs[name] = spec
            doc = {"attrs": {**shipped["attrs"], "body": spec}}
            zb = zenodo(FeatureModel.from_doc(doc), repo, tax, embed, samples)
            report["body_e2"][name] = paired(zb, z_base, "body")
        report["body_specs"] = body_specs

        # ---- sweetness abstention (rule chosen on E1, checked on E2) ---------------------------------------------
        from app.core.parse import ParsedBean
        # the SHIPPED recipe per attribute (scripts/train_feature_model.py SHIPPED_RECIPES); body without one is the
        # neighbour average
        from scripts.train_feature_model import SHIPPED_RECIPES

        def shipped_oof(attr):
            kind, arg = SHIPPED_RECIPES[attr]
            return oof(rows, attr, CONFIGS["base"] if kind == "gauges" else CONFIGS[arg])
        acid_oof = shipped_oof("acidity")
        sweet_oof = shipped_oof("sweetness")
        body_pred = shipped_oof("body") if "body" in SHIPPED_RECIPES else {
            r["key"]: r["nbr"]["new"]["body"] for r in rows if r["kind"] == "gauge"}
        e1 = {}
        for attr, p in (("acidity", acid_oof), ("sweetness", sweet_oof), ("body", body_pred)):
            e1[attr] = []
            for r in rows:
                if r["kind"] != "gauge" or r["labels"].get(attr) is None or p.get(r["key"]) is None:
                    continue
                parsed = ParsedBean(text=f"{r['name']} {r['flavor_summary'] or ''}",
                                    origin_country=r["origin_country"], process=r["process"],
                                    roast_level=r["roast_level"], is_decaf=bool(r["is_decaf"]),
                                    decaf_process=r["decaf_process"])
                e1[attr].append({"y": r["labels"][attr], "p": p[r["key"]], "nbr": r["nbr"]["new"][attr],
                                 "bucket": support_bucket(attr, parsed, tag_to_cat, tag_ko)})
        e2 = {}
        for attr in ATTRS:
            e2[attr] = [{"y": zs["truth"][attr], "p": zs["pred"][attr], "nbr": zb["pred"][attr],
                         "bucket": zs["bucket"][attr]}
                        for zs, zb in zip(z_ship, z_base) if zs["truth"][attr] is not None]
        rules = {"answer all (shipped)": lambda x: True,
                 "cue or neighbour value": lambda x: x["bucket"] == "cue" or x["nbr"] is not None,
                 "cue or note words": lambda x: x["bucket"] in ("cue", "notes"),
                 "cue or |model - neighbour| <= 1": lambda x: x["bucket"] == "cue" or (
                     x["nbr"] is not None and abs(x["p"] - x["nbr"]) <= 1)}
        report["abstention"] = {}
        for attr in ("sweetness", "acidity"):
            report["abstention"][attr] = {}
            for name, keep in rules.items():
                row = {}
                for lab, data in (("e1", e1[attr]), ("e2", [x for x in e2[attr] if x["p"] is not None])):
                    ans = [x for x in data if keep(x)]
                    row[lab] = {"coverage": round(len(ans) / len(data), R),
                                **attr_metrics([(x["y"], x["p"]) for x in ans])}
                report["abstention"][attr][name] = row

        # ---- calibrated confidence: grouped-CV residuals by support bucket (shipped predictors, answered only) ----
        answered = {"acidity": e1["acidity"],
                    "sweetness": [x for x in e1["sweetness"] if rules[ABSTAIN_RULE](x)],
                    "body": [x for x in e1["body"] if x["p"] is not None]}
        report["calibration"] = {"rule": f"high: within+-1 >= {CONF_HIGH} and Spearman >= {SPEARMAN_HIGH}; "
                                         f"medium: within+-1 >= {CONF_MEDIUM} and Spearman > 0; else low. Buckets with n < "
                                         f"{MIN_BUCKET} use the pooled '*' level; 'cue' is always high.",
                                 "e1": {}, "levels": {}}
        for attr, data in answered.items():
            by = defaultdict(list)
            for x in data:
                by[x["bucket"]].append((x["y"], x["p"]))
                by["*"].append((x["y"], x["p"]))
            stats = {b: attr_metrics(v) for b, v in sorted(by.items())}
            report["calibration"]["e1"][attr] = stats
            report["calibration"]["levels"][attr] = {b: level(m) for b, m in stats.items()
                                                     if b != "cue" and (b == "*" or m["n"] >= MIN_BUCKET)}
        report["calibration"]["e2_check"] = {
            attr: {b: attr_metrics([(x["y"], x["p"]) for x in e2[attr] if x["p"] is not None and x["bucket"] == b])
                   for b in sorted({x["bucket"] for x in e2[attr]})} for attr in ATTRS}
        report["e2_shipped"] = {a: attr_metrics([(z["truth"][a], z["pred"][a]) for z in z_ship
                                                 if z["truth"][a] is not None and z["pred"][a] is not None])
                                for a in ATTRS}
    finally:
        repo.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1)[:12000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
