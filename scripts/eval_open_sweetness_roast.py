"""Does the shipped sweetness recipe ("+C", per-roaster gauge offsets) use roast level well?
(docs/adr/0021-sweetness-abstention-and-body-nested-check.md, goal B.)

The roasters' own gauges are non-monotonic in roast level (dark 4.13, medium 2.94, medium-dark 2.87, light 3.75,
medium-light 4.15, n=15/33/23/8/8) -- ADR 0020's addendum. app.core.featuremodel.FEATURES already carries roast
level as a 5-way one-hot (roast_light/medium-light/medium/medium-dark/dark), not an ordinal scale, so it CAN
represent a non-monotonic pattern; this script checks whether the shipped "+C" recipe actually estimates it well,
and whether a coarser encoding (a single dark-roast indicator, motivated by "dark roasts read as sweet") does
better on the same out-of-fold folds -- both against the neighbour pool WITH shopify_gauged loaded ("new", what
production sees), same folds/targets as scripts/ablate_open_labels.py.

Ship rule: a variant replaces the one-hot only if its E1 within+-1 beats it by >= 0.03 (ablate_open_labels.py's own
SHIP_MARGIN_SHIPPED, so this uses the same bar as any other recipe change).

Writes data/eval/open/phase10_open_sweetness_roast.json only.

    uv run python scripts/eval_open_sweetness_roast.py
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

from scripts.ablate_open_labels import CONFIGS, SHIP_MARGIN_SHIPPED, fit, load_rows, matrix, pick_alpha, training_rows  # noqa: E402
from scripts.eval_zenodo_panel import attr_metrics  # noqa: E402
from scripts.train_feature_model import OPEN_URL  # noqa: E402

ATTR = "sweetness"
ROAST_COLS = ("roast_light", "roast_medium-light", "roast_medium", "roast_medium-dark", "roast_dark")


def oof_filtered(rows, c, only=None, drop=()) -> dict[str, float]:
    """scripts.eval_open_v3.oof, but with a chosen subset of the roast one-hot columns kept (`only`, the rest of
    ROAST_COLS dropped) or dropped (`drop`) -- every other feature (origin, process, notes, ...) is unchanged."""
    data = [r for r in rows if r["kind"] == "gauge" and r["labels"].get(ATTR) is not None]
    gauge_roasters = frozenset({r["roaster"] for r in data})
    out = {}
    for g in sorted(gauge_roasters):
        tr = training_rows(rows, ATTR, g, c, gauge_roasters)
        X, names = matrix([t[0] for t in tr], ATTR, c["pool"], False, c["bf"])
        if only is not None:
            keep = [i for i, n in enumerate(names) if n not in ROAST_COLS or n in only]
        else:
            keep = [i for i, n in enumerate(names) if n not in drop]
        X = X[:, keep]
        y = np.array([t[1] for t in tr])
        w = np.array([t[2] for t in tr])
        grp = np.array([t[0]["roaster"] for t in tr])
        kinds = [t[0]["kind"] for t in tr]
        alpha = pick_alpha(X, y, w, grp, kinds, c["c"])
        m, _ = fit(X, y, w, grp, alpha, c["c"])
        test = [r for r in data if r["roaster"] == g]
        Xt, _ = matrix(test, ATTR, c["pool"], False, c["bf"])
        pred = np.clip(m.predict(Xt[:, keep]), 1, 5)
        out.update({r["key"]: float(v) for r, v in zip(test, pred)})
    return out


def main() -> int:
    with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
        rows = load_rows(conn)
    c = CONFIGS["+C"]
    data = [r for r in rows if r["kind"] == "gauge" and r["labels"].get(ATTR) is not None]
    y = {r["key"]: r["labels"][ATTR] for r in data}
    variants = {
        "shipped (5-way one-hot)": dict(),
        "drop roast entirely": dict(drop=ROAST_COLS),
        "dark-roast indicator only": dict(only=("roast_dark",)),
        "dark or medium-light indicator": dict(only=("roast_dark", "roast_medium-light")),
    }
    report = {"generated_at": datetime.now(UTC).isoformat(),
              "roaster_gauge_means_by_roast": {  # ADR 0020 addendum, for reference
                  "dark": 4.13, "medium": 2.94, "medium-dark": 2.87, "light": 3.75, "medium-light": 4.15},
              "ship_margin": SHIP_MARGIN_SHIPPED, "variants": {}}
    base_within1 = None
    for name, kw in variants.items():
        preds = oof_filtered(rows, c, **kw)
        m = attr_metrics([(y[k], v) for k, v in preds.items()])
        if name.startswith("shipped"):
            base_within1 = m["within1"]
        report["variants"][name] = {**m, "within1_gain_vs_shipped": round(m["within1"] - base_within1, 4)
                                    if base_within1 is not None else None,
                                    "would_replace_shipped": bool(base_within1 is not None
                                                                  and m["within1"] - base_within1 >= SHIP_MARGIN_SHIPPED)}
    out = ROOT / "data" / "eval" / "open" / "phase10_open_sweetness_roast.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, m in report["variants"].items():
        print(f"{name:35s} within1={m['within1']} mae={m['mae']} gain={m['within1_gain_vs_shipped']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
