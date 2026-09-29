"""Open feature-model recipes after the ADR 0019 relabel: the sweetness regression, the body candidate, and which
recipe/abstention to ship. docs/adr/0020-open-recipes-after-relabel.md.

After ADR 0019 re-enriched coffee_open (English + ADR 0017 Korean note aliases) and scripts/train_feature_model.py
refit the SAME recipes, open sweetness on the Zenodo panel (answered, "cue or neighbour value") fell 44.2% -> 35.3%.
Two things moved at once: the training note features (notes_* read from the roasters' note summaries with the new
mapper) and the panel inputs (the cards' English notes -- "caramel", "nuts", "spices" -- now reach the model as note
categories). This script separates them and scores candidates:

  sweetness specs (E2: fit on every gauge bean; E1: out-of-fold by roaster, scripts/eval_open_v3.py oof)
    prod           the config shipped before ADR 0019 (git blob, frozen coefficients; E1 = its committed CV)
    retrained      the same recipe (gauges ridge, no neighbour) refit on the relabelled DB (ADR 0019's config blob)
    prev_mapper    the same recipe, note features read with the English aliases off at training time
    no_notes       the same recipe without the notes_* features
    ablation <c>   scripts/ablate_open_labels.py configs (+Bf, +C, +A, +A+C, ~A wA=1, ~B uncentred)
  x abstention rules (scripts/eval_open_v3.py): answer all / cue or neighbour value (shipped) / cue or note words /
    cue or |model - neighbour| <= 1
  E2 is also run with the English aliases off at inference (diagnostic: how much is the input side).
  body specs: neighbour average (shipped) vs the ablation / v3 candidates, E1 and E2 (ADR 0016 rule: E1 +-1 >= +0.05
    over the neighbour average, E2 +-1 and MAE not worse).
  acidity: the shipped +B recipe, reported so a sweetness/body change can be seen not to touch it.

Writes data/eval/open/phase9_open_recipes.json only.

    uv run python scripts/eval_open_recipes.py
"""
import contextlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.featuremodel import FeatureModel, support_bucket  # noqa: E402
from app.models import ATTRS  # noqa: E402
from pipeline import enrich, settings  # noqa: E402
from scripts.ablate_open_labels import CONFIGS, final_spec, fit, load_rows, matrix, pick_alpha, training_rows  # noqa: E402
from scripts.eval_open_v3 import neighbour_oof, oof, rows_with_cues, zenodo  # noqa: E402
from scripts.eval_zenodo_panel import XLSX, CachedEmbedder, attr_metrics, download, read_xlsx_rows  # noqa: E402
from scripts.eval_zenodo_panel import samples_from_rows  # noqa: E402
from scripts.train_feature_model import OPEN_URL, R  # noqa: E402

OUT = settings.DATA_DIR / "eval" / "open" / "phase9_open_recipes.json"
PROD_REV = "25a6b4a"          # main before ADR 0019: the config production runs
ADR0019_REV = "7bbd68b"       # ADR 0019's refit of the same recipes on the relabelled DB (not shipped)
SWEET_ABLATION = ("+Bf", "+C", "+A", "+A+C", "~A wA=1", "~B uncentred")
BODY_CANDIDATES = ("~A wA=1", "+A", "+A+B+C", "+B", "+C")
RULES = {
    "answer all": lambda x: True,
    "cue or neighbour value": lambda x: x["bucket"] == "cue" or x["nbr"] is not None,
    "cue or note words": lambda x: x["bucket"] in ("cue", "notes"),
    "cue or |model - neighbour| <= 1": lambda x: x["bucket"] == "cue" or (x["nbr"] is not None
                                                                         and abs(x["p"] - x["nbr"]) <= 1),
}


@contextlib.contextmanager
def aliases_off():
    saved = enrich.EN_TAG_ALIASES
    enrich.EN_TAG_ALIASES = {}
    try:
        yield
    finally:
        enrich.EN_TAG_ALIASES = saved


def config_at(rev: str) -> dict:
    blob = subprocess.run(["git", "show", f"{rev}:config/feature_model_open.json"], cwd=ROOT,
                          capture_output=True, check=True).stdout.decode("utf-8")
    return json.loads(blob)


def no_notes_oof_and_spec(rows, attr="sweetness"):
    """The base recipe with every notes_* column dropped (fold-wise and on all rows)."""
    c = CONFIGS["base"]
    data = [r for r in rows if r["kind"] == "gauge" and r["labels"].get(attr) is not None]

    def fit_on(tr):
        X, names = matrix([t[0] for t in tr], attr, c["pool"], False, c["bf"])
        keep = [i for i, n in enumerate(names) if not n.startswith("notes_")]
        X = X[:, keep]
        y = np.array([t[1] for t in tr])
        w = np.array([t[2] for t in tr])
        grp = np.array([t[0]["roaster"] for t in tr])
        kinds = [t[0]["kind"] for t in tr]
        alpha = pick_alpha(X, y, w, grp, kinds, c["c"])
        m, _ = fit(X, y, w, grp, alpha, c["c"])
        return m, keep, [names[i] for i in keep], alpha, len(y)

    out = {}
    for g in sorted({r["roaster"] for r in data}):
        m, keep, _, _, _ = fit_on(training_rows(rows, attr, g, c))
        test = [r for r in data if r["roaster"] == g]
        Xt = matrix(test, attr, c["pool"], False, c["bf"])[0][:, keep]
        out.update({r["key"]: float(v) for r, v in zip(test, np.clip(m.predict(Xt), 1, 5))})
    m, _, names, alpha, n = fit_on(training_rows(rows, attr, None, c))
    spec = {"type": "ridge", "alpha": alpha, "intercept": round(float(m.intercept_), R),
            "weights": {k: round(float(v), R) for k, v in zip(names, m.coef_) if abs(v) >= 1e-4},
            "n_train": n, "uses_nbr": False}
    return out, spec


def e1_rows(rows, attr, preds, tag_to_cat, tag_ko):
    from app.core.parse import ParsedBean
    out = []
    for r in rows:
        if r["kind"] != "gauge" or r["labels"].get(attr) is None or preds.get(r["key"]) is None:
            continue
        parsed = ParsedBean(text=f"{r['name']} {r['flavor_summary'] or ''}", origin_country=r["origin_country"],
                            process=r["process"], roast_level=r["roast_level"], is_decaf=bool(r["is_decaf"]),
                            decaf_process=r["decaf_process"])
        out.append({"y": r["labels"][attr], "p": preds[r["key"]], "nbr": r["nbr"]["new"][attr],
                    "bucket": support_bucket(attr, parsed, tag_to_cat, tag_ko)})
    return out


def by_rule(data) -> dict:
    out = {}
    for name, keep in RULES.items():
        ans = [x for x in data if x["p"] is not None and keep(x)]
        out[name] = {"coverage": round(len(ans) / len(data), R) if data else None,
                     **attr_metrics([(x["y"], x["p"]) for x in ans])}
    return out


def main() -> int:
    from app.repo import Repo
    shipped = json.loads((settings.CONFIG_DIR / "feature_model_open.json").read_text(encoding="utf-8"))
    prod = config_at(PROD_REV)
    refit = config_at(ADR0019_REV)
    with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
        rows = rows_with_cues(conn)
        with aliases_off():
            rows_prev = load_rows(conn)
    repo = Repo(OPEN_URL)
    tag_to_cat, tag_ko = repo.taxonomy()
    tax = (tag_to_cat, tag_ko, repo.tag_base_rates())
    embed = CachedEmbedder()
    samples = samples_from_rows(read_xlsx_rows(download(XLSX)))

    # ---- sweetness candidates: E1 out-of-fold predictions + the all-data spec ---------------------------------
    sweet = {"retrained": (oof(rows, "sweetness", CONFIGS["base"]), refit["attrs"]["sweetness"]),
             "prev_mapper": (oof(rows_prev, "sweetness", CONFIGS["base"]), final_spec(rows_prev, "sweetness", "base")),
             "no_notes": no_notes_oof_and_spec(rows)}
    for c in SWEET_ABLATION:
        sweet[c] = (oof(rows, "sweetness", CONFIGS[c]), final_spec(rows, "sweetness", c))
    body = {"neighbour_avg": (neighbour_oof(rows, "body"), None)}
    for c in BODY_CANDIDATES:
        body[c] = (oof(rows, "body", CONFIGS[c]), final_spec(rows, "body", c))

    report = {"generated_at": datetime.now(UTC).isoformat(), "prod_rev": PROD_REV, "rules": list(RULES),
              "sweetness": {}, "body": {}, "acidity": {}, "specs": {}}
    try:
        z_base = {"new": zenodo(None, repo, tax, embed, samples)}
        with aliases_off():
            z_base["old"] = zenodo(None, repo, tax, embed, samples)

        def e2(attrs: dict, attr: str, mapper: str = "new") -> list[dict]:
            fm = FeatureModel.from_doc({"attrs": attrs})
            ctx = aliases_off() if mapper == "old" else contextlib.nullcontext()
            with ctx:
                z = zenodo(fm, repo, tax, embed, samples)
            return [{"y": zs["truth"][attr], "p": zs["pred"][attr], "nbr": zb["pred"][attr], "bucket": zs["bucket"][attr]}
                    for zs, zb in zip(z, z_base[mapper]) if zs["truth"][attr] is not None]

        # sweetness
        e2_prod = {m: by_rule(e2(prod["attrs"], "sweetness", m)) for m in ("old", "new")}
        report["sweetness"]["prod"] = {"e1": "committed CV (old labels): see phase5_open_v3.json at " + PROD_REV,
                                       "e2": e2_prod["new"], "e2_prev_mapper_inference": e2_prod["old"]}
        for name, (preds, spec) in sweet.items():
            attrs = {**shipped["attrs"], "sweetness": spec}
            report["sweetness"][name] = {"e1": by_rule(e1_rows(rows, "sweetness", preds, tag_to_cat, tag_ko)),
                                         "e2": by_rule(e2(attrs, "sweetness"))}
            if name == "retrained":
                report["sweetness"][name]["e2_prev_mapper_inference"] = by_rule(e2(attrs, "sweetness", "old"))
            report["specs"][f"sweetness:{name}"] = spec
        # body (answer all; ADR 0016 rule)
        for name, (preds, spec) in body.items():
            data1 = e1_rows(rows, "body", preds, tag_to_cat, tag_ko)
            attrs = {k: v for k, v in shipped["attrs"].items() if k != "body"}
            if spec:
                attrs["body"] = spec
            report["body"][name] = {"e1": attr_metrics([(x["y"], x["p"]) for x in data1]),
                                    "e2": attr_metrics([(x["y"], x["p"]) for x in e2(attrs, "body") if x["p"] is not None])}
            if spec:
                report["specs"][f"body:{name}"] = spec
        # acidity, shipped recipe, both mappers at inference
        acid_oof = oof(rows, "acidity", CONFIGS["+B"])
        report["acidity"] = {"e1": attr_metrics([(x["y"], x["p"]) for x in e1_rows(rows, "acidity", acid_oof, tag_to_cat, tag_ko)]),
                             "e2": attr_metrics([(x["y"], x["p"]) for x in e2(shipped["attrs"], "acidity")]),
                             "e2_prod_config": attr_metrics([(x["y"], x["p"]) for x in e2(prod["attrs"], "acidity")]),
                             "e2_prod_config_prev_mapper": attr_metrics(
                                 [(x["y"], x["p"]) for x in e2(prod["attrs"], "acidity", "old")])}
    finally:
        repo.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    keys = ("coverage", "within1", "mae", "spearman")
    for name, v in report["sweetness"].items():
        for part in ("e1", "e2", "e2_prev_mapper_inference"):
            if isinstance(v.get(part), dict):
                print(f"sweet {name:14s} {part:26s} " + " | ".join(
                    f"{r[:12]}: " + " ".join(str(v[part][r].get(k)) for k in keys) for r in RULES))
    for name, v in report["body"].items():
        print(f"body {name:14s} e1 {v['e1'].get('within1')} {v['e1'].get('mae')}  e2 {v['e2'].get('within1')} {v['e2'].get('mae')}")
    print("acidity", report["acidity"])
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
