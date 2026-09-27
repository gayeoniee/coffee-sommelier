"""Train the learned multi-label flavor-tag model that replaces the neighbour-vote tag prediction for
unknown beans (docs/spikes/2026-09-27-tag-model-spike.md, docs/adr/0008-learned-tag-model.md).

Two things this script fixes relative to the spike's numbers:
  1. Trains on TAG-FREE embeddings (pipeline.embed.embedding_text(..., include_tags=False)) so training
     features match what production ever sees (app/graphs/analyze_bean.py embeds user-typed text that has
     no flavor_tags segment, because the tags don't exist yet).
  2. The fixed 200 LOO targets (repo.random_coffee_ids_for_loo(200, seed=42, exclude_sources=("roasters_kr",)))
     are excluded from training and scored on their own tag-free query embeddings (no leakage either way).

Outputs:
  - data/embedded/<embed model>/tagfree_query.jsonl   -- cache of every re-embedded bean (key+hash keyed,
    reruns are free); NOT checked in (data/embedded/ is gitignored).
  - data/eval/loo_tagfree_query_embeddings.jsonl       -- the 200 held-out targets' tag-free query
    embeddings, `{"id": <coffee id>, "vector": [...]}` one per line; consumed by `app/eval.py loo_accuracy`.
  - config/tag_model.json (or .json.gz if >2MB)         -- the trained weights + metadata, loaded at API
    start-up by app/core/tagmodel.py.
  - data/eval/phase2_tag_model.json                     -- CV table, held-out table, comparison with the
    neighbour vote on the same targets/same (leak-free) embeddings.

Usage:
    uv run python scripts/train_tag_model.py             # embeds (cached) + trains + writes everything
    uv run python scripts/train_tag_model.py --no-embed  # reuse existing embedding caches only (no API calls)

Retraining later (new data, a schema change, or a different embed model): just re-run the plain command --
the embedding cache makes reruns with unchanged text free, and training is a few seconds.
"""
import argparse
import gzip
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.model_selection import KFold
from sklearn.neural_network import MLPClassifier

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.predict import predict_from_neighbors  # noqa: E402
from app.repo import Repo  # noqa: E402
from pipeline import settings  # noqa: E402
from pipeline.embed import embedding_text  # noqa: E402
from pipeline.llm import embed_model, embedder_for  # noqa: E402
from pipeline.records import CoffeeRecord  # noqa: E402

MIN_POS = 30                                # tags need >=30 positives in the training set to get a label slot
THRESHOLDS = (0.25, 0.3, 0.35, 0.4, 0.45, 0.5)
N_HOLDOUT, HOLDOUT_SEED = 200, 42            # byte-identical to app/eval.py loo_accuracy's default draw
NEVER_LOO_TARGETS = ("roasters_kr",)         # facts-only beans; excluded from training + never a target
HIDDEN_SIZE = 128
K_NEIGHBORS = 10                             # matches app/graphs/analyze_bean.py K_NEIGHBORS
SIZE_LIMIT_BYTES = 2 * 1024 * 1024
ROUND = 5

CONFIG_JSON = settings.CONFIG_DIR / "tag_model.json"
CONFIG_GZ = settings.CONFIG_DIR / "tag_model.json.gz"
PHASE2_OUT = settings.EVAL_DIR / "phase2_tag_model.json"
LOO_QUERY_CACHE = settings.EVAL_DIR / "loo_tagfree_query_embeddings.jsonl"


# ---- data loading -----------------------------------------------------------------------------------------

def fetch_rows(repo: Repo) -> tuple[list[dict], dict[int, str]]:
    rows = repo._all(
        "SELECT id, key, name, origin_country, process, roast_level, is_decaf, decaf_process,"
        " flavor_tags, flavor_summary, source FROM coffees"
        " WHERE active AND embedding IS NOT NULL AND source <> ALL(%(xs)s) ORDER BY id",
        {"xs": list(NEVER_LOO_TARGETS)})
    review_rows = repo._all("SELECT coffee_id, text FROM reviews ORDER BY coffee_id, id")
    reviews: dict[int, list[str]] = {}
    for r in review_rows:
        reviews.setdefault(r["coffee_id"], []).append(r["text"])
    return rows, {cid: "\n".join(ts) for cid, ts in reviews.items()}


def tagfree_text(row: dict, review_text: dict[int, str]) -> str:
    c = CoffeeRecord(key=row["key"], name=row["name"], origin_country=row["origin_country"],
                     process=row["process"], roast_level=row["roast_level"], is_decaf=row["is_decaf"],
                     decaf_process=row["decaf_process"], flavor_tags=list(row["flavor_tags"] or []),
                     flavor_summary=row["flavor_summary"], source=row["source"], collected_at="")
    return embedding_text(c, review_text.get(row["id"]), include_tags=False)


# ---- embedding cache (key + text hash keyed; reruns with unchanged text are free) --------------------------

def load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["key"]] = row              # later lines win (same convention as pipeline/enrich.py cache)
    return out


def embed_missing(cache_path: Path, items: list[tuple[str, int, str]], embedder, batch: int, allow_embed: bool
                  ) -> dict[str, dict]:
    """items: (cache_key, coffee_id, tag_free_text). Returns cache_key -> {"id", "hash", "vector"}."""
    cache = load_cache(cache_path)
    todo = []
    for key, cid, text in items:
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if key in cache and cache[key]["hash"] == h:
            continue
        todo.append((key, cid, h, text))
    if todo and not allow_embed:
        raise RuntimeError(f"{len(todo)} texts have no cached embedding and --no-embed was passed "
                           f"(cache: {cache_path})")
    if todo:
        print(f"embedding {len(todo)} tag-free texts (batch={batch}) into {cache_path} ...", flush=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("a", encoding="utf-8") as f:
            for i in range(0, len(todo), batch):
                chunk = todo[i:i + batch]
                vecs = embedder.embed([t for *_, t in chunk], input_type="query")
                for (key, cid, h, _), v in zip(chunk, vecs):
                    row = {"key": key, "id": cid, "hash": h, "vector": [round(x, 6) for x in v]}
                    cache[key] = row
                    f.write(json.dumps(row) + "\n")
                f.flush()
                print(f"  {min(i + batch, len(todo))}/{len(todo)} embedded "
                     f"({embedder.requests} API requests so far)", flush=True)
    return cache


# ---- scoring (micro P/R/F1, same accumulation as app/eval.py's loo_accuracy) --------------------------------

def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


class Scorer:
    def __init__(self, tag_to_cat: dict[str, str]):
        self.tag_to_cat = tag_to_cat
        self.tp = self.fp = self.fn = 0
        self.cat_tp = self.cat_fp = self.cat_fn = 0
        self.n = 0

    def add(self, truth: set[str], pred: set[str]) -> None:
        self.n += 1
        self.tp += len(truth & pred)
        self.fp += len(pred - truth)
        self.fn += len(truth - pred)
        tc = {self.tag_to_cat[t] for t in truth if t in self.tag_to_cat}
        pc = {self.tag_to_cat[t] for t in pred if t in self.tag_to_cat}
        self.cat_tp += len(tc & pc)
        self.cat_fp += len(pc - tc)
        self.cat_fn += len(tc - pc)

    def result(self) -> dict:
        p, r, f1 = prf(self.tp, self.fp, self.fn)
        _, _, cat_f1 = prf(self.cat_tp, self.cat_fp, self.cat_fn)
        return {"n": self.n, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
                "category_f1": round(cat_f1, 4)}


def probs_to_tags(probs: np.ndarray, tags: list[str], threshold: float) -> list[set[str]]:
    return [{tags[i] for i in np.nonzero(row >= threshold)[0]} for row in probs]


# ---- model -------------------------------------------------------------------------------------------------

def make_model() -> MLPClassifier:
    return MLPClassifier(hidden_layer_sizes=(HIDDEN_SIZE,), max_iter=300, early_stopping=True, random_state=0)


def cv_eval(X: np.ndarray, Y: np.ndarray, tags: list[str], tag_to_cat: dict[str, str], n_splits: int = 5,
           seed: int = 0) -> dict:
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=seed)
    probs_oof = np.zeros_like(Y, dtype=np.float64)
    t0 = time.time()
    for fold, (tr, te) in enumerate(kf.split(X)):
        model = make_model()
        model.fit(X[tr], Y[tr])
        probs_oof[te] = model.predict_proba(X[te])
        print(f"  fold {fold + 1}/{n_splits} done ({time.time() - t0:.1f}s elapsed)", flush=True)
    truths = [{tags[i] for i in np.nonzero(Y[r])[0]} for r in range(len(Y))]

    def score(preds: list[set[str]]) -> dict:
        scorer = Scorer(tag_to_cat)
        for truth, pred in zip(truths, preds):
            scorer.add(truth, pred)
        return scorer.result()

    by_threshold = {str(thr): score(probs_to_tags(probs_oof, tags, thr)) for thr in THRESHOLDS}
    best_thr = max(THRESHOLDS, key=lambda t: by_threshold[str(t)]["f1"])
    return {"elapsed_s": round(time.time() - t0, 1), "by_threshold": by_threshold, "best_threshold": best_thr}


# ---- weights export ------------------------------------------------------------------------------------------

def round_matrix(m: np.ndarray) -> list:
    return [[round(float(x), ROUND) for x in row] for row in m] if m.ndim == 2 else [round(float(x), ROUND) for x in m]


def write_config(model: MLPClassifier, tags: list[str], threshold: float, n_train: int, cv: dict,
                 holdout: dict) -> Path:
    W1, W2 = model.coefs_
    b1, b2 = model.intercepts_
    assert model.out_activation_ == "logistic", f"expected sigmoid output, got {model.out_activation_}"
    doc = {
        "embed_model": embed_model(), "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_train": n_train, "hidden_size": HIDDEN_SIZE, "tags": tags, "threshold": threshold,
        "activation": {"hidden": "relu", "output": "sigmoid"},
        "W1": round_matrix(W1), "b1": round_matrix(b1), "W2": round_matrix(W2), "b2": round_matrix(b2),
        "cv_metrics": cv, "holdout_metrics": holdout,
    }
    CONFIG_JSON.write_text(json.dumps(doc), encoding="utf-8")
    size = CONFIG_JSON.stat().st_size
    if size <= SIZE_LIMIT_BYTES:
        CONFIG_GZ.unlink(missing_ok=True)
        print(f"wrote {CONFIG_JSON} ({size / 1024:.0f} KiB)")
        return CONFIG_JSON
    with gzip.open(CONFIG_GZ, "wt", encoding="utf-8") as f:
        json.dump(doc, f)
    CONFIG_JSON.unlink()
    gz_size = CONFIG_GZ.stat().st_size
    print(f"{CONFIG_JSON.name} was {size / 1024:.0f} KiB (over the 2MB budget); gzipped to "
         f"{CONFIG_GZ} ({gz_size / 1024:.0f} KiB)")
    return CONFIG_GZ


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-embed", action="store_true", help="reuse cached embeddings only, no NVIDIA API calls")
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    repo = Repo(settings.DATABASE_URL)
    try:
        tag_to_cat, _ = repo.taxonomy()
        base_rates = repo.tag_base_rates()
        rows, review_text = fetch_rows(repo)
        by_id = {r["id"]: r for r in rows}
        print(f"{len(rows)} active beans (excl. {NEVER_LOO_TARGETS}) with embeddings")

        holdout_ids = repo.random_coffee_ids_for_loo(N_HOLDOUT, HOLDOUT_SEED, exclude_sources=NEVER_LOO_TARGETS)
        holdout_set = set(holdout_ids)
        print(f"{len(holdout_ids)} fixed LOO held-out targets excluded from training")

        embedder = embedder_for()
        model_name = embed_model()
        cache_path = settings.embedded_dir(model_name) / "tagfree_query.jsonl"

        # 1) tag-free embeddings for every ACTIVE TAGGED bean not in the held-out set (~7.5k pool; untagged
        #    beans never contribute a training row, so they are not embedded here).
        train_rows = [r for r in rows if r["id"] not in holdout_set]
        tagged_train_rows = [r for r in train_rows if r["flavor_tags"]]
        train_items = [(r["key"], r["id"], tagfree_text(r, review_text)) for r in tagged_train_rows]
        train_cache = embed_missing(cache_path, train_items, embedder, args.batch, allow_embed=not args.no_embed)

        # 2) tag-free embeddings for the 200 held-out targets themselves (their OWN notes, no tags) -- these are
        #    also written to data/eval/loo_tagfree_query_embeddings.jsonl for app/eval.py loo_accuracy to reuse.
        holdout_rows_all = repo._all(
            "SELECT id, key, name, origin_country, process, roast_level, is_decaf, decaf_process,"
            " flavor_tags, flavor_summary, source FROM coffees WHERE id = ANY(%(ids)s)", {"ids": holdout_ids})
        holdout_review = {cid: t for cid, t in review_text.items() if cid in holdout_set}
        holdout_items = [(r["key"], r["id"], tagfree_text(r, holdout_review)) for r in holdout_rows_all]
        holdout_cache = embed_missing(cache_path, holdout_items, embedder, args.batch, allow_embed=not args.no_embed)
        print(f"total embedding API requests this run: {embedder.requests}")

        holdout_vec = {cid: holdout_cache[key]["vector"] for key, cid, _ in holdout_items}
        LOO_QUERY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with LOO_QUERY_CACHE.open("w", encoding="utf-8") as f:
            for cid in holdout_ids:                       # fixed order = the drawn LOO order
                f.write(json.dumps({"id": cid, "vector": holdout_vec[cid]}) + "\n")
        print(f"wrote {LOO_QUERY_CACHE} ({len(holdout_ids)} rows)")

        # ---- build the training matrix: >=MIN_POS-positive tags among the tagged training rows ----
        from collections import Counter
        counts = Counter()
        for r in tagged_train_rows:
            for t in {x.lower() for x in r["flavor_tags"]}:
                counts[t] += 1
        tags = sorted(t for t, n in counts.items() if n >= MIN_POS)
        tag_idx = {t: i for i, t in enumerate(tags)}
        print(f"{len(tagged_train_rows)} tagged training beans, {len(tags)} tags with >={MIN_POS} positives")

        X = np.array([train_cache[r["key"]]["vector"] for r in tagged_train_rows], dtype=np.float64)
        Y = np.zeros((len(tagged_train_rows), len(tags)), dtype=np.int8)
        for i, r in enumerate(tagged_train_rows):
            for t in {x.lower() for x in r["flavor_tags"]}:
                if t in tag_idx:
                    Y[i, tag_idx[t]] = 1

        print("\n--- 5-fold CV over the training pool ---")
        cv = cv_eval(X, Y, tags, tag_to_cat)
        threshold = cv["best_threshold"]
        print(f"CV by_threshold: {cv['by_threshold']}")
        print(f"picked threshold={threshold} from CV (n={cv['by_threshold'][str(threshold)]['n']})")

        print("\n--- training the final model on the full (leak-free) training pool ---")
        model = make_model()
        model.fit(X, Y)

        # ---- held-out scoring: learned model vs neighbour vote, SAME leak-free query embeddings ----
        truth_by_id = {cid: {t.lower() for t in (by_id.get(cid) or {}).get("flavor_tags", []) or []}
                      for cid in holdout_ids}
        # holdout targets may include non-tagged/other-source beans outside `rows` (e.g. none here, since
        # random_coffee_ids_for_loo already excludes roasters_kr and `rows` covers every other active source);
        # fall back to a direct lookup for anything missing.
        missing = [cid for cid in holdout_ids if cid not in by_id]
        if missing:
            extra = {r["id"]: r for r in repo._all(
                "SELECT id, flavor_tags FROM coffees WHERE id = ANY(%(ids)s)", {"ids": missing})}
            for cid in missing:
                truth_by_id[cid] = {t.lower() for t in (extra.get(cid) or {}).get("flavor_tags", []) or []}

        model_scorer, vote_scorer = Scorer(tag_to_cat), Scorer(tag_to_cat)
        for cid in holdout_ids:
            truth = truth_by_id.get(cid) or set()
            if not truth:
                continue
            vec = holdout_vec[cid]
            probs = model.predict_proba(np.array([vec]))[0]
            model_tags = {tags[i] for i in np.nonzero(probs >= threshold)[0]}
            model_scorer.add(truth, model_tags)
            near = repo.neighbors(vec, k=K_NEIGHBORS, exclude_id=cid)   # same pool app/eval.py's plain `loo` uses
            pred = predict_from_neighbors(near, base_rates=base_rates)
            vote_scorer.add(truth, {t.lower() for t in pred.tags})
        holdout = {"n_scored": model_scorer.n, "model": model_scorer.result(), "neighbor_vote": vote_scorer.result(),
                  "note": "both scored on the SAME tag-free (leak-free) query embeddings of the 200 fixed LOO "
                          "targets; neighbor_vote uses production defaults (k=10, thr=0.3, lift=1.2)."}
        print(f"\nheld-out (n={holdout['n_scored']}): model={holdout['model']} vote={holdout['neighbor_vote']}")

        # ---- Korean-roaster held-out check: roasters_kr beans with flavor_tags (~39), excluded from training
        #    above (fetch_rows excludes NEVER_LOO_TARGETS) and never a target elsewhere. Their tags come from
        #    Korean note words (pipeline/enrich.py ko_rule_tags), not the coffeereview-derived rule/LLM tags the
        #    training set uses -- a second, independent check of whether the model generalizes past its label
        #    source. See docs/adr/0008-learned-tag-model.md.
        kr_rows = repo._all(
            "SELECT id, key, name, origin_country, process, roast_level, is_decaf, decaf_process,"
            " flavor_tags, flavor_summary, source FROM coffees"
            " WHERE active AND embedding IS NOT NULL AND source = 'roasters_kr' AND cardinality(flavor_tags) > 0"
            " ORDER BY id")
        print(f"\n{len(kr_rows)} roasters_kr beans with flavor_tags (Korean held-out check)")
        kr_items = [(r["key"], r["id"], tagfree_text(r, review_text)) for r in kr_rows]
        kr_cache = embed_missing(cache_path, kr_items, embedder, args.batch, allow_embed=not args.no_embed)
        kr_vec = {cid: kr_cache[key]["vector"] for key, cid, _ in kr_items}

        kr_model_scorer, kr_vote_scorer = Scorer(tag_to_cat), Scorer(tag_to_cat)
        for r in kr_rows:
            cid = r["id"]
            truth = {t.lower() for t in (r["flavor_tags"] or [])}
            if not truth:
                continue
            vec = kr_vec[cid]
            probs = model.predict_proba(np.array([vec]))[0]
            model_tags = {tags[i] for i in np.nonzero(probs >= threshold)[0]}
            kr_model_scorer.add(truth, model_tags)
            near = repo.neighbors(vec, k=K_NEIGHBORS, exclude_id=cid)
            pred = predict_from_neighbors(near, base_rates=base_rates)
            kr_vote_scorer.add(truth, {t.lower() for t in pred.tags})
        korean_roasters = {
            "n_scored": kr_model_scorer.n, "model": kr_model_scorer.result(),
            "neighbor_vote": kr_vote_scorer.result(),
            "note": "roasters_kr active beans with flavor_tags (Korean note-word rule tags, "
                    "pipeline/enrich.py ko_rule_tags), excluded from training and never a target elsewhere; "
                    "independent of the coffeereview-derived n=200 held-out set above. Tag-free query "
                    "embeddings; neighbor_vote uses production defaults (k=10, thr=0.3, lift=1.2).",
        }
        print(f"korean_roasters (n={korean_roasters['n_scored']}): model={korean_roasters['model']} "
             f"vote={korean_roasters['neighbor_vote']}")

        cfg_path = write_config(model, tags, threshold, len(tagged_train_rows), cv, holdout)

        phase2 = {
            "embed_model": model_name, "n_tagged_train": len(tagged_train_rows), "n_tags": len(tags), "tags": tags,
            "min_positives": MIN_POS, "thresholds_swept": list(THRESHOLDS), "picked_threshold": threshold,
            "holdout_n": N_HOLDOUT, "holdout_seed": HOLDOUT_SEED, "cv": cv, "holdout": holdout,
            "korean_roasters": korean_roasters,
            "config_path": cfg_path.relative_to(ROOT).as_posix(),
        }
        PHASE2_OUT.write_text(json.dumps(phase2, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {PHASE2_OUT}")
    finally:
        repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
