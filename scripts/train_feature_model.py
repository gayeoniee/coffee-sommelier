"""Train + evaluate the interpretable open-data feature model for acidity/body/sweetness
(docs/adr/0011-roaster-gauges-feature-model.md; inference/feature extraction: app/core/featuremodel.py).

Labels: Korean roasters' own published intensity gauges (pipeline/collect/roasters_kr.py gauge_* fields,
read at full half-step precision from data/raw/roasters_kr/beans.jsonl -- the DB keeps rounded integers).
Optionally CQI's acidity quintile as a weak auxiliary label (it is a cupping QUALITY score, not intensity):
evaluated with and without.

Evaluation: grouped K-fold by roaster (leave-one-roaster-out -- no roaster is ever in both train and test),
on gauged beans only. Every candidate is compared, on the same folds and targets, with
  - global mean (of the training folds' labels),
  - per-origin mean (training-fold mean for the bean's country, >= 2 labels, else global),
  - open neighbour average: app.core.predict.predict_from_neighbors over the k=10 nearest coffee_open beans
    by stored embedding (origin/process pre-filter like app/repo.py), EXCLUDING the target's own roaster --
    the same "no roaster on both sides" rule. A bean whose neighbours carry < 3 labels gets the training
    global mean (the production app would show no value); coverage is reported.
Models: ridge (alpha chosen by an inner leave-one-roaster-out over the training roasters) and a small
gradient-boosted tree, each with and without the neighbour average as one extra feature.

Ship rule (ADR 0011, per attribute): the ridge variant ships only if its grouped-CV within+-1 beats the open
neighbour baseline by >= 0.05 (GBT is reported for reference; only the linear model ships, since the evidence line
explains a prediction by per-feature contributions). The shipped model is refit on every labelled bean.

This script is the ONLY writer of config/feature_model_open.json, and it writes the canonical shipped config:
SHIPPED_RECIPES below fixes, per attribute, the recipe the ADRs decided (ADR 0011 gauges-only ridge; ADR 0013 the
acidity "+B" weak-label recipe, refit through scripts/ablate_open_labels.py's own final_spec so the two scripts share
one code path; ADR 0014 external check). The CV here and in ablate_open_labels.py only report -- a changed decision
is made by editing SHIPPED_RECIPES (and an ADR), never as a side effect of re-running a script. The config carries
no timestamp, so re-running on the same DB reproduces it byte-for-byte.

A sanity reference (evaluation-only, never trained on): the final ridge applied to coffeereview beans in the
FULL `coffee` DB, compared with their acidity (review sub-score quintile) and body (LLM heaviness, ADR 0010)
labels -- different label semantics, so Spearman correlation matters more than MAE there.

Follow-up (docs/adr/0013-open-labels-weak-supervision.md): scripts/ablate_open_labels.py re-runs this CV with
extra open labels and reports (data/eval/open/phase4_open_labels.json only). Since shopify_gauged is loaded into
coffee_open, the neighbour pool here includes it (ADR 0013 reports the pool-without numbers as "base").

Usage:
    uv run python scripts/train_feature_model.py            # CV + reference + eval + canonical config
    uv run python scripts/train_feature_model.py --no-ship  # CV + reference + eval only
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.featuremodel import FEATURES, bean_features  # noqa: E402
from app.core.flavors import build_tag_to_category, load_tag_ko_extra, merge_tag_ko  # noqa: E402
from app.core.predict import predict_from_neighbors  # noqa: E402
from app.core.textcues import text_tags  # noqa: E402
from app.models import ATTRS, Neighbor  # noqa: E402
from app.repo import MIN_FILTERED_NEIGHBORS  # noqa: E402
from pipeline import settings  # noqa: E402

OPEN_URL = "postgresql://coffee:coffee@localhost:5432/coffee_open"
FULL_URL = "postgresql://coffee:coffee@localhost:5432/coffee"
BEANS = settings.RAW_DIR / "roasters_kr" / "beans.jsonl"
K = 10
ALPHAS = (0.3, 1.0, 3.0, 10.0, 30.0)
SHIP_MARGIN = 0.05
CQI_WEIGHT = 0.2          # auxiliary CQI rows count 1/5 of a gauge row
GBT = dict(n_estimators=150, max_depth=2, learning_rate=0.05, subsample=0.8, random_state=0)
R = 4
# The shipped recipe per attribute (body ships none). ("gauges", _) = final_fit below, gauges only, ridge with
# the neighbour feature iff uses_nbr; ("ablation", name) = scripts/ablate_open_labels.py CONFIGS[name] via its
# final_spec. Decisions: ADR 0011 (acidity/sweetness), ADR 0013 (acidity -> "+B"), ADR 0014 (sweetness kept after
# the Zenodo panel check).
# ADR 0020 (after the ADR 0019 relabel): sweetness "+C" (per-roaster gauge offsets) replaces the gauges-only ridge,
# whose refit lost the Zenodo panel (answered +-1 44.2% -> 35.3%); body ships "+A wA=1" (overseas shops' body marks at
# full weight) instead of the neighbour average -- both measured by scripts/eval_open_recipes.py
SHIPPED_RECIPES = {"acidity": ("ablation", "+B"), "body": ("ablation", "+A wA=1"), "sweetness": ("ablation", "+C")}
# ADR 0016: the note-free flavor-tag model ("feat" in scripts/eval_open_tags.py) ships in the same config file
SHIP_TAG_MODEL = True
# ADR 0016: sweetness abstains without support (E1 coverage 51%, answered +-1 and MAE better on E1 and E2)
ABSTAIN = ("sweetness",)
# ADR 0021: after the ADR 0019 relabel, the ADR 0016 rule's E1 coverage fell to 48.6% (below the 50% floor) because
# fewer beans have >=3 labelled neighbours post-relabel. A similarity-weighted neighbour count (same per-neighbour
# weight as the neighbour average itself) answers a few more beans whose neighbours are fewer but closer, without
# the accuracy loss of just lowering the raw count to 2 (scripts/eval_open_sweetness_abstain.py ->
# data/eval/open/phase10_open_sweetness.json): E1 coverage 51.4% (was 48.6%), +-1 0.676 (was 0.686, within noise),
# E2 (secondary check) improves 0.497 -> 0.558 answered +-1. Cue text still overrides regardless.
# ADR 0024: excluding roasterdb from coffee_open thins the neighbour pool further, so t=1.4 alone fell back
# below the 50% floor (E1 coverage 48.6%). The weight_sweep in phase10_open_sweetness.json is flat from
# t=1.0 to t=1.3 (coverage 58.3%, +-1 0.667, MAE 0.913 at every one of those thresholds -- the eligible
# beans' weighted neighbour sums cluster below 1.4), so 1.3 is the tightest threshold that still clears the
# floor; re-tuned 1.4 -> 1.3.
ABSTAIN_MIN_WEIGHT = {"sweetness": 1.3}
CONFIG_PATH = settings.CONFIG_DIR / "feature_model_open.json"


def taxonomy(conn) -> tuple[dict[str, str], dict[str, str]]:
    rows = conn.execute("SELECT key, level, name_en, name_ko FROM flavor_taxonomy").fetchall()
    tag_to_cat = build_tag_to_category((r["key"], r["level"], r["name_en"]) for r in rows)
    tag_ko = merge_tag_ko({r["name_en"].lower(): r["name_ko"] for r in rows if r["name_ko"]}, load_tag_ko_extra())
    return tag_to_cat, tag_ko


def note_categories(text: str | None, tag_to_cat, tag_ko) -> list[str]:
    return [tag_to_cat[t] for t in text_tags(text or "", tag_to_cat, tag_ko) if t in tag_to_cat]


def neighbours(conn, emb: str, cid: int, roaster: str | None, origin, process) -> list[Neighbor]:
    """app/repo.py Repo.neighbors, plus: never the target's own roaster."""
    def run(extra: str):
        with conn.transaction():
            conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            conn.execute("SET LOCAL hnsw.ef_search = 200")
            rows = conn.execute(
                "SELECT id, name, acidity, body, sweetness, flavor_tags, 1 - (embedding <=> %(v)s::vector) AS sim"
                " FROM coffees WHERE active AND embedding IS NOT NULL AND id <> %(ex)s"
                " AND roaster IS DISTINCT FROM %(r)s" + extra +
                " ORDER BY embedding <=> %(v)s::vector, id LIMIT %(k)s",
                {"v": emb, "ex": cid, "r": roaster, "k": K, "o": origin, "p": process}).fetchall()
        return [Neighbor(r["id"], r["name"], float(r["sim"]), r["acidity"], r["body"], r["sweetness"],
                         tuple(r["flavor_tags"] or ())) for r in sorted(rows, key=lambda r: (-r["sim"], r["id"]))]
    if origin or process:
        hits = run((" AND origin_country = %(o)s" if origin else "") + (" AND process = %(p)s" if process else ""))
        if len(hits) >= MIN_FILTERED_NEIGHBORS:
            return hits
    return run("")


def load_gauged(conn, tag_to_cat, tag_ko) -> list[dict]:
    gauges = {}
    for line in BEANS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            gauges[f"roasters_kr:{r['key']}"] = r
    rows = conn.execute(
        "SELECT id, key, name, roaster, origin_country, process, roast_level, is_decaf, decaf_process, variety,"
        " altitude_m, flavor_summary, embedding::text AS emb FROM coffees"
        " WHERE active AND source = 'roasters_kr' ORDER BY key").fetchall()
    out = []
    for r in rows:
        g = gauges.get(r["key"])
        labels = {a: g.get(f"gauge_{a}") for a in ATTRS} if g else {}
        if not any(v is not None for v in labels.values()):
            continue
        near = neighbours(conn, r["emb"], r["id"], r["roaster"], r["origin_country"], r["process"])
        pred = predict_from_neighbors(near)
        out.append({**{k: r[k] for k in ("key", "name", "roaster", "origin_country", "process", "roast_level",
                                          "is_decaf", "decaf_process", "variety", "altitude_m", "flavor_summary")},
                    "labels": labels, "nbr": {a: getattr(pred, a) for a in ATTRS},
                    "cats": note_categories(r["flavor_summary"], tag_to_cat, tag_ko)})
    return out


def load_cqi(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT key, origin_country, process, roast_level, is_decaf, decaf_process, variety, altitude_m, acidity"
        " FROM coffees WHERE active AND source = 'cqi' AND acidity IS NOT NULL").fetchall()
    return [{**r, "flavor_summary": None, "name": "", "cats": [], "nbr": {a: None for a in ATTRS},
             "labels": {"acidity": float(r["acidity"])}, "roaster": "cqi"} for r in rows]


def feats(row: dict, attr: str, use_nbr: bool) -> dict[str, float]:
    return bean_features(origin_country=row["origin_country"], process=row["process"],
                         roast_level=row["roast_level"], is_decaf=bool(row["is_decaf"]),
                         decaf_process=row["decaf_process"], variety=row.get("variety"),
                         altitude_m=row.get("altitude_m"), text=row.get("name"),
                         note_categories=row["cats"], neighbor_value=row["nbr"][attr] if use_nbr else None)


def matrix(rows, attr, use_nbr) -> np.ndarray:
    names = [f for f in FEATURES if use_nbr or f != "nbr"]
    return np.array([[feats(r, attr, use_nbr).get(f, 0.0) for f in names] for r in rows]), names


def fit_ridge(X, y, w, alpha) -> Ridge:
    return Ridge(alpha=alpha).fit(X, y, sample_weight=w)


def pick_alpha(X, y, w, groups) -> float:
    """Inner leave-one-roaster-out over the training roasters (gauge rows only are scored)."""
    ug = [g for g in dict.fromkeys(groups) if g != "cqi"]
    if len(ug) < 2:
        return 3.0
    best = None
    for a in ALPHAS:
        err, n = 0.0, 0
        for g in ug:
            tr, te = groups != g, groups == g
            m = fit_ridge(X[tr], y[tr], w[tr], a)
            err += np.abs(m.predict(X[te]) - y[te]).sum()
            n += te.sum()
        if best is None or err / n < best[1]:
            best = (a, err / n)
    return best[0]


def score(pred, y) -> dict:
    e = np.abs(np.asarray(pred) - np.asarray(y))
    return {"n": int(len(y)), "mae": round(float(e.mean()), R), "within1": round(float((e <= 1).mean()), R)}


def grouped_cv(rows: list[dict], attr: str, cqi: list[dict] | None) -> dict:
    data = [r for r in rows if r["labels"].get(attr) is not None]
    roasters = sorted({r["roaster"] for r in data})
    y_all = np.array([r["labels"][attr] for r in data])
    preds = defaultdict(lambda: np.zeros(len(data)))
    nbr_cov = 0
    alphas = defaultdict(list)
    for g in roasters:
        te = np.array([r["roaster"] == g for r in data])
        train = [r for r, t in zip(data, te) if not t]
        test = [r for r, t in zip(data, te) if t]
        ytr = np.array([r["labels"][attr] for r in train])
        gmean = ytr.mean()
        # baselines
        preds["global_mean"][te] = gmean
        by_c = defaultdict(list)
        for r in train:
            by_c[r["origin_country"]].append(r["labels"][attr])
        preds["origin_mean"][te] = [np.mean(by_c[r["origin_country"]]) if r["origin_country"] and
                                    len(by_c[r["origin_country"]]) >= 2 else gmean for r in test]
        nb = [r["nbr"][attr] for r in test]
        nbr_cov += sum(v is not None for v in nb)
        preds["neighbor_avg"][te] = [v if v is not None else gmean for v in nb]
        # models
        for use_nbr in (False, True):
            tag = "+nbr" if use_nbr else ""
            for aux in ((False, True) if cqi else (False,)):
                trainx = train + (cqi if aux else [])
                Xtr, _ = matrix(trainx, attr, use_nbr)
                ytr2 = np.array([r["labels"][attr] for r in trainx])
                w = np.array([CQI_WEIGHT if r["roaster"] == "cqi" else 1.0 for r in trainx])
                grp = np.array([r["roaster"] for r in trainx])
                Xte, _ = matrix(test, attr, use_nbr)
                a = pick_alpha(Xtr, ytr2, w, grp)
                name = f"ridge{tag}{'+cqi' if aux else ''}"
                alphas[name].append(a)
                preds[name][te] = np.clip(fit_ridge(Xtr, ytr2, w, a).predict(Xte), 1, 5)
                gb = GradientBoostingRegressor(**GBT).fit(Xtr, ytr2, sample_weight=w)
                preds[f"gbt{tag}{'+cqi' if aux else ''}"][te] = np.clip(gb.predict(Xte), 1, 5)
    table = {k: score(v, y_all) for k, v in preds.items()}
    per_roaster = {g: {k: score(v[np.array([r["roaster"] == g for r in data])],
                                y_all[np.array([r["roaster"] == g for r in data])])
                       for k, v in preds.items() if k in ("neighbor_avg", "global_mean", "ridge", "ridge+nbr")}
                   for g in roasters}
    return {"n": len(data), "roasters": {g: sum(r["roaster"] == g for r in data) for g in roasters},
            "neighbor_coverage": round(nbr_cov / len(data), R), "table": table,
            "alphas": {k: v for k, v in alphas.items()}, "per_roaster": per_roaster}


def final_fit(rows, attr, use_nbr) -> tuple[dict, dict]:
    data = [r for r in rows if r["labels"].get(attr) is not None]
    X, names = matrix(data, attr, use_nbr)
    y = np.array([r["labels"][attr] for r in data])
    w = np.ones(len(y))
    alpha = pick_alpha(X, y, w, np.array([r["roaster"] for r in data]))
    m = fit_ridge(X, y, w, alpha)
    weights = {n: round(float(c), R) for n, c in zip(names, m.coef_) if abs(c) >= 1e-4}
    gb = GradientBoostingRegressor(**GBT).fit(X, y)
    importances = {n: round(float(v), R) for n, v in sorted(zip(names, gb.feature_importances_),
                                                             key=lambda kv: -kv[1]) if v >= 0.01}
    return ({"type": "ridge", "alpha": alpha, "intercept": round(float(m.intercept_), R), "weights": weights,
             "n_train": len(y), "uses_nbr": use_nbr}, importances)


def spearman(a, b) -> float:
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    return round(float(np.corrcoef(ra, rb)[0, 1]), R)


def coffeereview_reference(specs: dict, tag_to_cat, tag_ko) -> dict:
    """Apply the no-neighbour ridge (fit on all gauges) to coffeereview beans in the full DB -- evaluation only."""
    from app.core.featuremodel import FeatureModel
    fm = FeatureModel.from_doc({"attrs": specs})
    with psycopg.connect(FULL_URL, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT name, origin_country, process, roast_level, is_decaf, decaf_process, variety, altitude_m,"
            " flavor_summary, acidity, body FROM coffees WHERE active AND source = 'coffeereview_kaggle'").fetchall()
    out = {}
    for attr in ("acidity", "body"):
        if attr not in specs:
            continue
        ys, ps = [], []
        for r in rows:
            if r[attr] is None:
                continue
            f = bean_features(origin_country=r["origin_country"], process=r["process"], roast_level=r["roast_level"],
                              is_decaf=r["is_decaf"], decaf_process=r["decaf_process"], variety=r["variety"],
                              altitude_m=r["altitude_m"], text=r["name"],
                              note_categories=note_categories(r["flavor_summary"], tag_to_cat, tag_ko))
            ps.append(fm.predict({attr: f})[attr][0])
            ys.append(r[attr])
        ys, ps = np.array(ys, float), np.array(ps)
        out[attr] = {"label": "review sub-score quintile" if attr == "acidity" else "LLM heaviness (ADR 0010)",
                     "model": score(ps, ys), "global_mean": score(np.full(len(ys), ys.mean()), ys),
                     "spearman": spearman(ps, ys)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ship", action="store_true")
    args = ap.parse_args()
    with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
        tag_to_cat, tag_ko = taxonomy(conn)
        rows = load_gauged(conn, tag_to_cat, tag_ko)
        cqi = load_cqi(conn)
    cv = {a: grouped_cv(rows, a, cqi if a == "acidity" else None) for a in ATTRS}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "k": K, "ship_margin_within1": SHIP_MARGIN,
              "cqi_aux_weight": CQI_WEIGHT, "gauged_beans": len(rows),
              "labels_per_attr": {a: sum(r["labels"].get(a) is not None for r in rows) for a in ATTRS},
              "cv": cv, "ship": {}, "final": {}, "gbt_importance": {}}
    for a in ATTRS:
        t = cv[a]["table"]
        base = t["neighbor_avg"]["within1"]
        cands = [k for k in ("ridge", "ridge+nbr") if k in t]
        best = max(cands, key=lambda k: (t[k]["within1"], -t[k]["mae"]))
        gain = round(t[best]["within1"] - base, R)
        spec, imp = final_fit(rows, a, use_nbr=best.endswith("+nbr"))
        report["final"][a] = spec
        report["gbt_importance"][a] = imp
        # informational: the gauges-only ADR 0011 rule on today's pool; what ships is SHIPPED_RECIPES
        report["ship"][a] = {"candidate": best, "within1_gain_vs_neighbor": gain,
                             "passes_gauges_only_rule": gain >= SHIP_MARGIN,
                             "shipped_recipe": list(SHIPPED_RECIPES[a]) if a in SHIPPED_RECIPES else None}
    nonbr = {a: final_fit(rows, a, use_nbr=False)[0] for a in ATTRS}
    report["coffeereview_reference"] = coffeereview_reference(nonbr, tag_to_cat, tag_ko)
    out = settings.DATA_DIR / "eval" / "open" / "phase3_feature_model.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({a: {"n": cv[a]["n"], **{k: v for k, v in cv[a]["table"].items()}} for a in ATTRS}, indent=1))
    print(json.dumps(report["ship"], indent=1), json.dumps(report["coffeereview_reference"], indent=1))
    if not args.no_ship:
        CONFIG_PATH.write_text(shipped_config(rows), encoding="utf-8")
        print(f"wrote {CONFIG_PATH}:", ", ".join(f"{a}={k}:{v}" for a, (k, v) in SHIPPED_RECIPES.items()))
    return 0


def shipped_config(rows: list[dict]) -> str:
    """The canonical config/feature_model_open.json text for SHIPPED_RECIPES (deterministic: no timestamp)."""
    specs, weak = {}, []
    ablation_rows = None
    for a, (kind, arg) in SHIPPED_RECIPES.items():
        if kind == "gauges":
            specs[a] = final_fit(rows, a, use_nbr=arg)[0]
            continue
        from scripts.ablate_open_labels import W_B, final_spec, load_rows   # same code path as the ablation
        if ablation_rows is None:
            with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
                ablation_rows = load_rows(conn)
        specs[a] = final_spec(ablation_rows, a, arg)
        if "B" in arg.replace("Bf", ""):
            weak.append(a)
    labels = "roaster-published intensity gauges (roasters_kr)"
    if weak:
        labels += (f"; {', '.join(sorted(weak))} also trained on note-word weak labels (weight {W_B}, re-centred;"
                   " app/core/weaklabels.py, ADR 0013)")
    doc = {"labels": labels, "recipes": "scripts/train_feature_model.py SHIPPED_RECIPES (ADR 0011, 0013, 0014, 0016, 0020)",
           "eval": "data/eval/open/phase3_feature_model.json + data/eval/open/phase4_open_labels.json"
                   " + data/eval/open/phase5_open_tags.json + data/eval/open/phase5_open_v3.json",
           "attrs": {a: specs[a] for a in ATTRS if a in specs}}
    if ABSTAIN:
        doc["abstain"] = {"attrs": list(ABSTAIN),
                          "rule": "answer only with a text cue, a neighbour value (>= 3 of the k=10 neighbours carry"
                                  " the attribute, ADR 0016), or (attrs in min_weight) a similarity-weighted"
                                  " neighbour count >= the threshold (ADR 0021); otherwise None + '단맛: 근거 부족'",
                          "min_weight": ABSTAIN_MIN_WEIGHT}
    v3 = settings.DATA_DIR / "eval" / "open" / "phase5_open_v3.json"
    if v3.exists():      # calibrated per-attribute confidence from grouped-CV residuals (scripts/eval_open_v3.py)
        cal = json.loads(v3.read_text(encoding="utf-8"))["calibration"]
        doc["calibration"] = {"rule": cal["rule"], "levels": cal["levels"]}
    if SHIP_TAG_MODEL:
        from scripts.eval_open_tags import shipped_tag_spec    # the E1/E2-measured "feat" candidate (ADR 0016)
        with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
            doc["tags"] = shipped_tag_spec(conn)
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
