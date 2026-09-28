"""Open-variant flavor-tag model: can a licence-clean tag predictor beat the open neighbour vote when the guest
types only origin/process/roast (no note words)? docs/adr/0016-open-variant-v3.md.

Why "no note words": the analyze path first echoes the input's own note words (app/core/predict.py
with_text_cues), so a predictor only matters when the text has none. Every E1/E2 input here is note-free.

Labels (licence-clean only): the flavor_tags of active roasters_kr + shopify + shopify_gauged beans in coffee_open
(rule-mapped from each bean's own note words at pipeline time). roasterdb stays out (licence-restricted, same as
scripts/eval_tag_model_open.py's ship pool).

Inputs, per bean: the note-free query text "name | origin | process | roast | decaf" (embedding_text without tags,
summary, reviews) -- embedded once and cached in data/raw/open_tag_eval/ -- and the interpretable features of
app/core/featuremodel.py WITHOUT note categories or neighbour value.

Candidates (per SCA tag with >= MIN_POS training positives, one-vs-rest logistic regression; prediction = tags with
p >= threshold, at most MAX_TAGS, threshold chosen by an inner grouped CV on the training roasters):
  feat   interpretable features only
  emb    note-free embedding only
  both   features + embedding
  prior  the training fold's most frequent tags (top-2)
Baseline: the open neighbour vote (app.core.predict.predict_from_neighbors, lift gate, k=10) over coffee_open
neighbours of the SAME note-free query embedding, excluding the target's own roaster.

E1: grouped leave-one-roaster-out over the tagged beans (micro tag F1 and SCA category F1, the metric of
scripts/eval_zenodo_panel.py tag_metrics). E2: the Zenodo panel (evaluation only) with the note-free card text
("producer origin | roast | decaf"), truth = the panel's aroma/bouquet/aftertaste words through rule_tags; the
model is fit on all licence-clean tagged beans; baseline = production neighbour vote on the same text.

Ship rule (ADR 0016): E1 tag F1 or category F1 >= vote + 0.05 AND E2 not below the vote. Writes
data/eval/open/phase5_open_tags.json only.

    uv run python scripts/eval_open_tags.py
"""
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.featuremodel import FEATURES, bean_features  # noqa: E402
from app.core.predict import MAX_TAGS, predict_from_neighbors  # noqa: E402
from pipeline import settings  # noqa: E402
from scripts.eval_zenodo_panel import (  # noqa: E402
    XLSX, CachedEmbedder, download, read_xlsx_rows, samples_from_rows, tag_metrics,
)
from scripts.train_feature_model import OPEN_URL, neighbours  # noqa: E402

SOURCES = ("roasters_kr", "shopify", "shopify_gauged")
MIN_POS = 5
THRESHOLDS = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5)
C = 1.0
INNER_FOLDS = 4
CACHE = settings.RAW_DIR / "open_tag_eval" / "query_embeddings.jsonl"
OUT = settings.DATA_DIR / "eval" / "open" / "phase5_open_tags.json"
FEATS_NO_NOTES = [f for f in FEATURES if not f.startswith("notes_") and f != "nbr"]
R = 4


def notefree_text(r: dict) -> str:
    parts = [r["name"], r["origin_country"], r["process"], r["roast_level"],
             f"decaf {r['decaf_process'] or ''}".strip() if r["is_decaf"] else None]
    return " | ".join(p for p in parts if p)


def feat_vec(origin, process, roast, is_decaf, decaf_process, variety, altitude, text) -> list[float]:
    f = bean_features(origin_country=origin, process=process, roast_level=roast, is_decaf=bool(is_decaf),
                      decaf_process=decaf_process, variety=variety, altitude_m=altitude, text=text)
    return [f.get(k, 0.0) for k in FEATS_NO_NOTES]


def load(conn, embed=None):
    rows = conn.execute(
        "SELECT id, key, name, roaster, source, origin_country, process, roast_level, is_decaf, decaf_process,"
        " variety, altitude_m, flavor_tags FROM coffees WHERE active AND source = ANY(%(s)s)"
        " AND cardinality(flavor_tags) > 0 ORDER BY key", {"s": list(SOURCES)}).fetchall()
    for r in rows:
        r["text"] = notefree_text(r)
        r["vec"] = embed(r["text"]) if embed else None
        r["feat"] = feat_vec(r["origin_country"], r["process"], r["roast_level"], r["is_decaf"], r["decaf_process"],
                             r["variety"], r["altitude_m"], r["name"])
        r["truth"] = {t.lower() for t in r["flavor_tags"]}
    return rows


def X_of(rows, kind: str) -> np.ndarray:
    if kind == "feat":
        return np.array([r["feat"] for r in rows], float)
    if kind == "emb":
        return np.array([r["vec"] for r in rows], float)
    return np.hstack([np.array([r["feat"] for r in rows], float), np.array([r["vec"] for r in rows], float)])


class TagLR:
    """One-vs-rest logistic regression over the tags with >= MIN_POS positives."""

    def __init__(self, X, truths):
        counts = Counter(t for s in truths for t in s)
        self.tags = sorted(t for t, n in counts.items() if n >= MIN_POS)
        self.models = {}
        for t in self.tags:
            y = np.array([t in s for s in truths], int)
            self.models[t] = LogisticRegression(C=C, max_iter=2000).fit(X, y)

    def proba(self, X) -> np.ndarray:
        return np.column_stack([self.models[t].predict_proba(X)[:, 1] for t in self.tags]) if self.tags else \
            np.zeros((len(X), 0))

    def decide(self, P, thr) -> list[set]:
        out = []
        for p in P:
            order = [i for i in np.argsort(-p) if p[i] >= thr][:MAX_TAGS]
            out.append({self.tags[i] for i in order})
        return out


def micro_f1(pairs) -> float:
    tp = sum(len(t & p) for t, p in pairs)
    fp = sum(len(p - t) for t, p in pairs)
    fn = sum(len(t - p) for t, p in pairs)
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def pick_threshold(X, truths, groups) -> float:
    from sklearn.model_selection import GroupKFold
    probs = {}
    for k, (tr, te) in enumerate(GroupKFold(n_splits=min(INNER_FOLDS, len(set(groups)))).split(X, groups=groups)):
        m = TagLR(X[tr], [truths[i] for i in tr])
        probs[k] = (m, m.proba(X[te]), [truths[i] for i in te])
    best = None
    for thr in THRESHOLDS:
        pairs = [(t, p) for m, P, ts in probs.values() for t, p in zip(ts, m.decide(P, thr))]
        f = micro_f1(pairs)
        if best is None or f > best[1]:
            best = (thr, f)
    return best[0]


def e1(rows, conn, base_rates, tag_to_cat) -> dict:
    groups = np.array([r["roaster"] for r in rows])
    truths = [r["truth"] for r in rows]
    preds = defaultdict(lambda: [None] * len(rows))
    thr_by = defaultdict(list)
    for i, r in enumerate(rows):   # neighbour vote, own roaster excluded
        near = neighbours(conn, str(r["vec"]), r["id"], r["roaster"], r["origin_country"], r["process"])
        preds["neighbour_vote"][i] = {t.lower() for t in predict_from_neighbors(near, base_rates=base_rates).tags}
    for g in sorted(set(groups)):
        tr, te = np.nonzero(groups != g)[0], np.nonzero(groups == g)[0]
        ttr = [truths[i] for i in tr]
        top2 = {t for t, _ in Counter(t for s in ttr for t in s).most_common(2)}
        for i in te:
            preds["prior_top2"][i] = top2
        for kind in ("feat", "emb", "both"):
            X = X_of(rows, kind)
            thr = pick_threshold(X[tr], ttr, groups[tr])
            thr_by[kind].append(thr)
            m = TagLR(X[tr], ttr)
            for i, p in zip(te, m.decide(m.proba(X[te]), thr)):
                preds[kind][i] = p
    table = {k: tag_metrics(list(zip(truths, v)), tag_to_cat) for k, v in preds.items()}
    per_roaster = {g: {k: tag_metrics([(truths[i], v[i]) for i in np.nonzero(groups == g)[0]], tag_to_cat)["f1"]
                       for k, v in preds.items()} for g in sorted(set(groups))}
    return {"n": len(rows), "roasters": dict(Counter(groups.tolist())), "table": table,
            "thresholds": dict(thr_by), "per_roaster_f1": per_roaster}


def e2(rows, repo, base_rates, tag_to_cat, embed) -> dict:
    from app.core.parse import parse_bean_text
    from pipeline.enrich import rule_tags
    samples = samples_from_rows(read_xlsx_rows(download(XLSX)))
    vocab = list(tag_to_cat)
    truths = [{t.lower() for t in rule_tags(s["tag_text"], vocab, limit=12)} for s in samples]
    texts = []
    for s in samples:   # the card text without its "notes: ..." part
        texts.append(" | ".join(p for p in s["text"].split(" | ") if not p.startswith("notes:")))
    keep = [i for i, t in enumerate(truths) if t and texts[i].strip()]   # a sample with only notes has no input
    parsed = [parse_bean_text(t) for t in texts]
    vecs = [embed(t) if t.strip() else None for t in texts]
    out = {"n": len(keep), "samples_without_input": sum(1 for i, t in enumerate(truths) if t and not texts[i].strip()),
           "example_input": texts[0]}
    vote = []
    for i in keep:
        near = repo.neighbors(vecs[i], 10, parsed[i].origin_country, parsed[i].process)
        vote.append({t.lower() for t in predict_from_neighbors(near, base_rates=base_rates).tags})
    out["neighbour_vote"] = tag_metrics(list(zip([truths[i] for i in keep], vote)), tag_to_cat)
    ttr = [r["truth"] for r in rows]
    top2 = {t for t, _ in Counter(t for s in ttr for t in s).most_common(2)}
    out["prior_top2"] = tag_metrics([(truths[i], top2) for i in keep], tag_to_cat)
    groups = np.array([r["roaster"] for r in rows])
    zfeat = [feat_vec(p.origin_country, p.process, p.roast_level, p.is_decaf, p.decaf_process, None, None, p.text)
             for p in parsed]
    for kind in ("feat", "emb", "both"):
        X = X_of(rows, kind)
        thr = pick_threshold(X, ttr, groups)
        m = TagLR(X, ttr)
        if kind == "feat":
            Z = np.array(zfeat, float)
        elif kind == "emb":
            Z = np.array([vecs[i] for i in keep], float)
        else:
            Z = np.hstack([np.array([zfeat[i] for i in keep], float), np.array([vecs[i] for i in keep], float)])
        if kind == "feat":
            Z = Z[keep]
        pred = m.decide(m.proba(Z), thr)
        out[kind] = {**tag_metrics(list(zip([truths[i] for i in keep], pred)), tag_to_cat), "threshold": thr,
                     "top_predicted": Counter(t for p in pred for t in p).most_common(5)}
    return out


def main() -> int:
    from app.repo import Repo
    embed = CachedEmbedder(CACHE)
    repo = Repo(OPEN_URL)
    try:
        tag_to_cat, _ = repo.taxonomy()
        base_rates = repo.tag_base_rates()
        with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
            rows = load(conn, embed)
            r1 = e1(rows, conn, base_rates, tag_to_cat)
        r2 = e2(rows, repo, base_rates, tag_to_cat, embed)
    finally:
        repo.close()
    vote1 = r1["table"]["neighbour_vote"]
    ship = {}
    for k in ("feat", "emb", "both"):
        t = r1["table"][k]
        e1_ok = t["f1"] - vote1["f1"] >= 0.05 or t["category_f1"] - vote1["category_f1"] >= 0.05
        e2_ok = r2[k]["f1"] >= r2["neighbour_vote"]["f1"] and r2[k]["category_f1"] >= r2["neighbour_vote"]["category_f1"]
        ship[k] = {"e1_gain_f1": round(t["f1"] - vote1["f1"], R),
                   "e1_gain_category_f1": round(t["category_f1"] - vote1["category_f1"], R),
                   "e2_gain_f1": round(r2[k]["f1"] - r2["neighbour_vote"]["f1"], R),
                   "e2_gain_category_f1": round(r2[k]["category_f1"] - r2["neighbour_vote"]["category_f1"], R),
                   "passes": bool(e1_ok and e2_ok)}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "sources": SOURCES, "min_pos": MIN_POS,
              "input": "note-free text (name | origin | process | roast | decaf)", "e1": r1, "e2": r2,
              "ship": ship, "embedding_requests": embed.requests,
              "cache_sha1": hashlib.sha1(CACHE.read_bytes()).hexdigest()[:12] if CACHE.exists() else None}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"e1": r1["table"], "e1_thr": r1["thresholds"]}, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in r2.items() if k != "example_input"}, ensure_ascii=False, indent=1))
    print(json.dumps(ship, indent=1))
    print(f"wrote {OUT} ({embed.requests} embedding requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def shipped_tag_spec(conn) -> dict:
    """The "tags" block of config/feature_model_open.json (written by scripts/train_feature_model.py): the "feat"
    candidate refit on every licence-clean tagged bean, threshold by grouped inner CV. Deterministic."""
    rows = load(conn)
    X = X_of(rows, "feat")
    truths = [r["truth"] for r in rows]
    thr = pick_threshold(X, truths, np.array([r["roaster"] for r in rows]))
    m = TagLR(X, truths)
    return {"type": "logistic_ovr", "features": FEATS_NO_NOTES, "threshold": thr, "max_tags": MAX_TAGS,
            "n_train": len(rows), "sources": list(SOURCES), "min_pos": MIN_POS,
            "tags": {t: {"intercept": round(float(m.models[t].intercept_[0]), R),
                         "weights": {f: round(float(w), R) for f, w in zip(FEATS_NO_NOTES, m.models[t].coef_[0])
                                     if abs(w) >= 1e-4}} for t in m.tags}}
