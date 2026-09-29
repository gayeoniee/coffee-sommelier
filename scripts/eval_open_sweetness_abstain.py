"""Open sweetness abstention after ADR 0020's relabel (docs/adr/0021-sweetness-abstention-and-body-nested-check.md).

ADR 0016 answers sweetness only with a text cue or a neighbour value (>= 3 of the k=10 neighbours, excluding the
target's own roaster, carry a sweetness label) -- everything else gets "단맛: 근거 부족" instead of a number. After
ADR 0019's relabel, E1 (roaster-held-out CV) answered share fell to 48.6%, below ADR 0016's 50% floor, because fewer
beans have >=3 labelled neighbours post-relabel -- unrelated to the sweetness recipe (ADR 0020 already covers that).

This script compares the shipped rule against alternatives on the SAME out-of-fold predictions (scripts/
ablate_open_labels.py CONFIGS["+C"], the shipped recipe, via scripts/eval_open_v3.py's oof()):

  cue or neighbour value (shipped)        answer with a text cue, or the neighbour average exists (>=3 raw)
  cue or facts (roast/origin known)       + roast_level or origin_country known -- far too permissive (see results)
  cue or raw neighbour count >= 2         lower the raw MIN_NEIGHBORS-style cutoff by one
  cue or weighted neighbour count >= t    app.core.featuremodel.neighbour_support_weight (SHIPPED, ADR 0021, t=1.4):
                                          the same per-neighbour weight as the neighbour average itself (similarity,
                                          floored at 0.01), so two very close neighbours can outweigh three distant
                                          ones -- a softer, continuous version of the same "how many of my neighbours
                                          know this attribute" question, rather than a different question.

E1 uses the real out-of-fold predictions and support signals (bucket via app.core.featuremodel.support_bucket, raw/
weighted neighbour counts via a direct neighbour query per bean, ablate_open_labels.neighbours -- own roaster
excluded, same as production's "no roaster on both sides" rule for training). E2 runs the actual shipped runtime
path (app.core.featuremodel.with_feature_model via scripts/eval_zenodo_panel.py's predict() mirror) with each
candidate's abstain config, so the E2 numbers are the real behaviour, not a re-implementation -- only a secondary
check (ADR 0020's addendum: the panel's sweetness follows a different convention than the roasters' gauges).

Writes data/eval/open/phase10_open_sweetness.json only.

    uv run python scripts/eval_open_sweetness_abstain.py
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.featuremodel import FeatureModel, neighbour_support_weight, support_bucket  # noqa: E402
from app.core.parse import ParsedBean, parse_bean_text  # noqa: E402
from pipeline import settings  # noqa: E402
from scripts.ablate_open_labels import CONFIGS, neighbours as raw_neighbours  # noqa: E402
from scripts.eval_open_v3 import oof, rows_with_cues  # noqa: E402
from scripts.eval_zenodo_panel import (  # noqa: E402
    XLSX, CachedEmbedder, attr_metrics, download, predict as ez_predict, read_xlsx_rows, samples_from_rows,
)
from scripts.train_feature_model import ABSTAIN_MIN_WEIGHT, OPEN_URL, taxonomy  # noqa: E402

OUT = settings.DATA_DIR / "eval" / "open" / "phase10_open_sweetness.json"
SHIPPED_WEIGHT = ABSTAIN_MIN_WEIGHT["sweetness"]                 # 1.4 (ADR 0021)
WEIGHT_SWEEP = (1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 2.0, 2.5, 3.0)


def e1_data(conn, tag_to_cat, tag_ko) -> list[dict]:
    rows = rows_with_cues(conn)
    preds = oof(rows, "sweetness", CONFIGS["+C"])
    data = [r for r in rows if r["kind"] == "gauge" and r["labels"].get("sweetness") is not None
           and preds.get(r["key"]) is not None]
    embs = {r["key"]: r for r in conn.execute(
        "SELECT key, id, embedding::text AS emb FROM coffees WHERE key = ANY(%s)",
        ([r["key"] for r in data],)).fetchall()}
    out = []
    for r in data:
        parsed = ParsedBean(text=f"{r['name']} {r['flavor_summary'] or ''}", origin_country=r["origin_country"],
                            process=r["process"], roast_level=r["roast_level"], is_decaf=bool(r["is_decaf"]),
                            decaf_process=r["decaf_process"])
        e = embs[r["key"]]
        near = raw_neighbours(conn, e["emb"], e["id"], r["roaster"], r["origin_country"], r["process"],
                              exclude_source=None)
        out.append({"y": r["labels"]["sweetness"], "p": preds[r["key"]],
                    "bucket": support_bucket("sweetness", parsed, tag_to_cat, tag_ko),
                    "roast_known": bool(r["roast_level"]), "origin_known": bool(r["origin_country"]),
                    "count": sum(1 for n in near if n.sweetness is not None),
                    "wsum": neighbour_support_weight(near, "sweetness")})
    return out


def e2_data(repo, tax, embed, samples) -> list[dict]:
    shipped = json.loads((settings.CONFIG_DIR / "feature_model_open.json").read_text(encoding="utf-8"))
    fm_answer_all = FeatureModel.from_doc({"attrs": shipped["attrs"]})          # no abstain: always predicts
    tag_to_cat, tag_ko, _ = tax
    out = []
    for s in samples:
        if s["truth"].get("sweetness") is None:
            continue
        vec = embed(s["text"])
        pred, _ = ez_predict(s, vec, repo, tax, feature_model=fm_answer_all)
        if pred.sweetness is None:
            continue
        parsed = parse_bean_text(s["text"])
        neighbors = repo.neighbors(vec, 10, parsed.origin_country, parsed.process)
        out.append({"y": s["truth"]["sweetness"], "p": pred.sweetness,
                    "bucket": support_bucket("sweetness", parsed, tag_to_cat, tag_ko),
                    "roast_known": bool(parsed.roast_level), "origin_known": bool(parsed.origin_country),
                    "count": sum(1 for n in neighbors if n.sweetness is not None),
                    "wsum": neighbour_support_weight(neighbors, "sweetness")})
    return out


def by_rule(data: list[dict], rules: dict) -> dict:
    out = {}
    for name, keep in rules.items():
        ans = [x for x in data if keep(x)]
        out[name] = {"coverage": round(len(ans) / len(data), 4) if data else None,
                     **attr_metrics([(x["y"], x["p"]) for x in ans])}
    return out


def main() -> int:
    from app.repo import Repo
    rules = {
        "cue or neighbour value (shipped baseline)": lambda x: x["bucket"] == "cue" or x["count"] >= 3,
        "cue or facts (roast or origin known)": lambda x: (x["bucket"] == "cue" or x["count"] >= 3
                                                           or x["roast_known"] or x["origin_known"]),
        "cue or raw neighbour count >= 2": lambda x: x["bucket"] == "cue" or x["count"] >= 2,
        "cue or raw neighbour count >= 1": lambda x: x["bucket"] == "cue" or x["count"] >= 1,
        f"cue or weighted count >= {SHIPPED_WEIGHT} (SHIPPED, ADR 0021)":
            lambda x: x["bucket"] == "cue" or x["wsum"] >= SHIPPED_WEIGHT,
        "answer all": lambda x: True,
    }
    with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
        tag_to_cat, tag_ko = taxonomy(conn)
        e1 = e1_data(conn, tag_to_cat, tag_ko)
    repo = Repo(OPEN_URL)
    try:
        tax = (tag_to_cat, tag_ko, repo.tag_base_rates())
        embed = CachedEmbedder()
        samples = samples_from_rows(read_xlsx_rows(download(XLSX)))
        e2 = e2_data(repo, tax, embed, samples)
    finally:
        repo.close()
    report = {"generated_at": datetime.now(UTC).isoformat(), "shipped_weight": SHIPPED_WEIGHT,
              "e1_n": len(e1), "e2_n": len(e2),
              "e1": by_rule(e1, rules), "e2": by_rule(e2, rules),
              "weight_sweep": {"e1": {t: by_rule(e1, {"r": lambda x, t=t: x["bucket"] == "cue" or x["wsum"] >= t}
                                                 )["r"] for t in WEIGHT_SWEEP},
                               "e2": {t: by_rule(e2, {"r": lambda x, t=t: x["bucket"] == "cue" or x["wsum"] >= t}
                                                 )["r"] for t in WEIGHT_SWEEP}}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for part in ("e1", "e2"):
        print(f"--- {part} (n={report[f'{part}_n']}) ---")
        for name, m in report[part].items():
            print(f"{name:48s} coverage={m['coverage']} within1={m['within1']} mae={m['mae']}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
