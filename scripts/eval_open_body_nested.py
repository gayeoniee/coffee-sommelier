"""Honest E1 for the shipped body weight ("+A wA=1"): nested weight selection.
(docs/adr/0021-sweetness-abstention-and-body-nested-check.md, goal C.)

ADR 0020 picked wA (0.25, 0.5, 1) for the "+A" body recipe (shopify_gauged roaster profiles join the gauges,
weight wA) by comparing all three on the SAME 3-roaster grouped leave-one-out E1 folds and reporting the winner's
own score (wA=1, within+-1 0.781) -- optimistic, because the candidate was chosen ON the folds it is then scored
on. This script re-runs E1 with NESTED selection: for each outer roaster fold, wA is chosen using only the other
two roasters (inner leave-one-out over them), then the chosen weight is refit on all non-held-out data and scored
on the held-out roaster -- the outer fold never influences its own candidate's selection.

Rule (ADR 0021): keep the shipped weight unless nested selection clearly favours another -- "clearly" checked
against E2 (Zenodo), never the outer E1 folds a second time.

Writes data/eval/open/phase10_open_body_nested.json only.

    uv run python scripts/eval_open_body_nested.py
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.featuremodel import FeatureModel  # noqa: E402
from scripts.ablate_open_labels import CONFIGS, fit, load_rows, matrix, pick_alpha, training_rows  # noqa: E402
from scripts.ablate_open_labels import final_spec  # noqa: E402
from scripts.eval_open_v3 import zenodo  # noqa: E402
from scripts.eval_zenodo_panel import (  # noqa: E402
    XLSX, CachedEmbedder, attr_metrics, download, read_xlsx_rows, samples_from_rows,
)
from scripts.train_feature_model import OPEN_URL  # noqa: E402

ATTR = "body"
CANDIDATES = {"wA=0.25": "~A wA=0.25", "wA=0.5": "+A", "wA=1 (shipped)": "+A wA=1"}


def fit_predict(rows, data, test_roaster, c, gauge_roasters) -> dict[str, float]:
    tr = training_rows(rows, ATTR, test_roaster, c, gauge_roasters)
    X, _ = matrix([t[0] for t in tr], ATTR, c["pool"], False, c["bf"])
    y = np.array([t[1] for t in tr])
    w = np.array([t[2] for t in tr])
    grp = np.array([t[0]["roaster"] for t in tr])
    kinds = [t[0]["kind"] for t in tr]
    alpha = pick_alpha(X, y, w, grp, kinds, c["c"])
    m, _ = fit(X, y, w, grp, alpha, c["c"])
    test = [r for r in data if r["roaster"] == test_roaster]
    Xt, _ = matrix(test, ATTR, c["pool"], False, c["bf"])
    pred = np.clip(m.predict(Xt), 1, 5)
    return {r["key"]: float(v) for r, v in zip(test, pred)}


def main() -> int:
    with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
        rows = load_rows(conn)
    data = [r for r in rows if r["kind"] == "gauge" and r["labels"].get(ATTR) is not None]
    y_all = {r["key"]: r["labels"][ATTR] for r in data}
    roasters = sorted({r["roaster"] for r in data})
    gauge_roasters = frozenset(roasters)

    # naive: same-fold selection, as ADR 0020 did -- report every candidate's own grouped-CV score
    naive = {}
    for name, cname in CANDIDATES.items():
        preds = {}
        for g in roasters:
            preds.update(fit_predict(rows, data, g, CONFIGS[cname], gauge_roasters))
        naive[name] = attr_metrics([(y_all[k], v) for k, v in preds.items()])

    # nested: outer fold g held fully out (including from inner candidate selection); inner LOO over the other
    # two roasters (only, since there are 3 total) picks the weight, then it's refit on all non-g data and scored
    # on g. Rows of roaster g never enter a candidate's selection score.
    chosen_by_fold, nested_preds = {}, {}
    for g in roasters:
        inner_roasters = [r for r in roasters if r != g]
        rows_no_g = [r for r in rows if not (r["kind"] == "gauge" and r["roaster"] == g)]
        inner_scores = {}
        for name, cname in CANDIDATES.items():
            c = CONFIGS[cname]
            ip = {}
            for ig in inner_roasters:
                ip.update(fit_predict(rows_no_g, data, ig, c, gauge_roasters))
            inner_scores[name] = attr_metrics([(y_all[k], v) for k, v in ip.items()])
        best = max(inner_scores, key=lambda n: (inner_scores[n]["within1"], -inner_scores[n]["mae"]))
        chosen_by_fold[g] = {"chosen": best, "inner_scores": inner_scores}
        nested_preds.update(fit_predict(rows, data, g, CONFIGS[CANDIDATES[best]], gauge_roasters))
    nested = attr_metrics([(y_all[k], v) for k, v in nested_preds.items()])

    # E2 check for each candidate weight (never used to pick the nested winner -- reported alongside per ADR 0021)
    embed = CachedEmbedder()
    samples = samples_from_rows(read_xlsx_rows(download(XLSX)))
    from app.repo import Repo
    repo = Repo(OPEN_URL)
    e2 = {}
    try:
        tag_to_cat, tag_ko = repo.taxonomy()
        tax = (tag_to_cat, tag_ko, repo.tag_base_rates())
        shipped = json.loads((ROOT / "config" / "feature_model_open.json").read_text(encoding="utf-8"))
        for name, cname in CANDIDATES.items():
            spec = final_spec(rows, ATTR, cname)
            fm = FeatureModel.from_doc({"attrs": {**shipped["attrs"], "body": spec}})
            z = zenodo(fm, repo, tax, embed, samples)
            pairs = [(zs["truth"][ATTR], zs["pred"][ATTR]) for zs in z
                    if zs["truth"][ATTR] is not None and zs["pred"][ATTR] is not None]
            e2[name] = attr_metrics(pairs)
    finally:
        repo.close()

    report = {"generated_at": datetime.now(UTC).isoformat(), "roasters": roasters,
              "naive_same_fold_selection": naive, "nested_selection_by_fold": chosen_by_fold,
              "nested_unbiased_e1": nested, "reported_adr0020_e1": {"within1": 0.781, "mae": 0.693},
              "e2_by_candidate": e2}
    out = ROOT / "data" / "eval" / "open" / "phase10_open_body_nested.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("naive:", {k: (v["within1"], v["mae"]) for k, v in naive.items()})
    print("nested chosen per fold:", {g: v["chosen"] for g, v in chosen_by_fold.items()})
    print("nested unbiased E1:", nested)
    print("E2 by candidate:", {k: (v["within1"], v["mae"]) for k, v in e2.items()})
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
