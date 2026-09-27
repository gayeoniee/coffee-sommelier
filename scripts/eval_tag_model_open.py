"""Goal B2 (docs/adr/0009-learned-attribute-model.md): measure whether a licence-clean, CATEGORY-level flavor
model is feasible for the open-data deployment, and ship config/tag_model_open.json only if it clearly beats
the neighbour vote.

Individual flavor tags are hopeless at this scale (the tag model itself needed >=30 positives per label over
7,327 training beans -- docs/adr/0008-learned-tag-model.md); the open-licence tagged pool is ~150 beans, so
this only tries the coarser 7-way SCA top-level category (app.core.flavors.PREFERENCE_CHIPS) as a multi-label
target -- reusing app/core/tagmodel.py's TagModel class unchanged (it's generic over its label list; "tags"
here are category names, not flavor words).

Measurement pool (feasibility only, per docs/adr/0009 Goal B2): roasters_kr + shopify + roasterdb, ~150 tagged
active beans. roasterdb is licence-restricted for shipping (same status as coffeereview_kaggle -- excluded from
scripts/train_attr_model.py's --variant open too) but included here to give the 5-fold CV enough data to say
anything at all; the SHIPPED model (if any) is retrained on roasters_kr + shopify only.

Decision: ship config/tag_model_open.json only if the CV category F1 (over the ~150-bean measurement pool)
beats the neighbour vote's category F1 on the SAME beans by >=0.05. Otherwise: report the numbers, ship
nothing (app.core.tagmodel.TagModel.load() then finds no tag_model_open.json and the open deployment keeps
using the neighbour vote, as it always has).

Usage:
    uv run python scripts/eval_tag_model_open.py
    uv run python scripts/eval_tag_model_open.py --no-embed
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.model_selection import KFold
from sklearn.neural_network import MLPClassifier

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.flavors import PREFERENCE_CHIPS  # noqa: E402
from app.core.predict import predict_from_neighbors  # noqa: E402
from app.repo import Repo  # noqa: E402
from pipeline import settings  # noqa: E402
from pipeline.embed import embedding_text  # noqa: E402
from pipeline.llm import embed_model, embedder_for  # noqa: E402
from pipeline.records import CoffeeRecord  # noqa: E402

MEASURE_SOURCES = ("roasters_kr", "shopify", "roasterdb")   # feasibility measurement pool
SHIP_SOURCES = ("roasters_kr", "shopify")                    # licence-clean: what a shipped model would train on
MIN_SHIP_MARGIN = 0.05
THRESHOLDS = (0.25, 0.3, 0.35, 0.4, 0.45, 0.5)
HIDDEN_SIZE = 128
K_NEIGHBORS = 10
ROUND = 5

CONFIG_OUT = settings.CONFIG_DIR / "tag_model_open.json"
PHASE2_OUT = settings.EVAL_DIR / "phase2_tag_model_open_feasibility.json"


def fetch_rows(repo: Repo, sources: tuple[str, ...]) -> tuple[list[dict], dict[int, str]]:
    rows = repo._all(
        "SELECT id, key, name, origin_country, process, roast_level, is_decaf, decaf_process,"
        " flavor_tags, flavor_summary, source FROM coffees"
        " WHERE active AND embedding IS NOT NULL AND source = ANY(%(xs)s) AND cardinality(flavor_tags) > 0"
        " ORDER BY id", {"xs": list(sources)})
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


def load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["key"]] = row
    return out


def embed_missing(cache_path: Path, items: list[tuple[str, int, str]], embedder, batch: int, allow_embed: bool
                  ) -> dict[str, dict]:
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
    return cache


def row_categories(row: dict, tag_to_cat: dict[str, str]) -> set[str]:
    return {tag_to_cat[t.lower()] for t in (row["flavor_tags"] or []) if tag_to_cat.get(t.lower()) in PREFERENCE_CHIPS}


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


class Scorer:
    def __init__(self):
        self.tp = self.fp = self.fn = 0
        self.n = 0

    def add(self, truth: set[str], pred: set[str]) -> None:
        self.n += 1
        self.tp += len(truth & pred)
        self.fp += len(pred - truth)
        self.fn += len(truth - pred)

    def result(self) -> dict:
        p, r, f1 = prf(self.tp, self.fp, self.fn)
        return {"n": self.n, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def cv_eval(X: np.ndarray, Y: np.ndarray, cats: list[str], n_splits: int = 5, seed: int = 0) -> dict:
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=seed)
    probs_oof = np.zeros_like(Y, dtype=np.float64)
    for tr, te in kf.split(X):
        model = MLPClassifier(hidden_layer_sizes=(HIDDEN_SIZE,), max_iter=300, early_stopping=True, random_state=0)
        model.fit(X[tr], Y[tr])
        probs_oof[te] = model.predict_proba(X[te])
    truths = [{cats[i] for i in np.nonzero(Y[r])[0]} for r in range(len(Y))]

    def score(thr: float) -> dict:
        scorer = Scorer()
        for i, truth in enumerate(truths):
            pred = {cats[j] for j in np.nonzero(probs_oof[i] >= thr)[0]}
            scorer.add(truth, pred)
        return scorer.result()

    by_threshold = {str(t): score(t) for t in THRESHOLDS}
    best_thr = max(THRESHOLDS, key=lambda t: by_threshold[str(t)]["f1"])
    return {"by_threshold": by_threshold, "best_threshold": best_thr, "best_f1": by_threshold[str(best_thr)]["f1"]}


def vote_baseline(repo: Repo, rows: list[dict], vecs: dict[int, list[float]], tag_to_cat: dict[str, str],
                  base_rates: dict[str, float]) -> dict:
    scorer = Scorer()
    for r in rows:
        cid = r["id"]
        truth = row_categories(r, tag_to_cat)
        if not truth:
            continue
        near = repo.neighbors(vecs[cid], k=K_NEIGHBORS, exclude_id=cid)     # full production pool, no restriction
        pred = predict_from_neighbors(near, base_rates=base_rates)
        pred_cats = {tag_to_cat[t.lower()] for t in pred.tags if tag_to_cat.get(t.lower()) in PREFERENCE_CHIPS}
        scorer.add(truth, pred_cats)
    return scorer.result()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    repo = Repo(settings.DATABASE_URL)
    try:
        tag_to_cat, tag_ko = repo.taxonomy()
        base_rates = repo.tag_base_rates()
        rows, review_text = fetch_rows(repo, MEASURE_SOURCES)
        rows = [r for r in rows if row_categories(r, tag_to_cat)]     # need >=1 of the 7 categories to score
        print(f"{len(rows)} tagged beans across {MEASURE_SOURCES} with >=1 of the 7 preference categories")

        embedder = embedder_for()
        model_name = embed_model()
        cache_path = settings.embedded_dir(model_name) / "tagfree_query.jsonl"
        items = [(r["key"], r["id"], tagfree_text(r, review_text)) for r in rows]
        cache = embed_missing(cache_path, items, embedder, args.batch, allow_embed=not args.no_embed)
        vecs = {r["id"]: cache[r["key"]]["vector"] for r in rows}

        cats = list(PREFERENCE_CHIPS)
        cat_idx = {c: i for i, c in enumerate(cats)}
        X = np.array([vecs[r["id"]] for r in rows], dtype=np.float64)
        Y = np.zeros((len(rows), len(cats)), dtype=np.int8)
        for i, r in enumerate(rows):
            for c in row_categories(r, tag_to_cat):
                Y[i, cat_idx[c]] = 1

        print("\n--- 5-fold CV: category-level classifier over the measurement pool ---")
        cv = cv_eval(X, Y, cats)
        print(f"CV by_threshold: {cv['by_threshold']}")
        print(f"best CV category F1 = {cv['best_f1']} @ threshold {cv['best_threshold']}")

        print("\n--- neighbour vote baseline: SAME beans, production defaults, full catalogue neighbours ---")
        vote = vote_baseline(repo, rows, vecs, tag_to_cat, base_rates)
        print(f"vote category F1 = {vote['f1']}")

        margin = round(cv["best_f1"] - vote["f1"], 4)
        ship = margin >= MIN_SHIP_MARGIN
        print(f"\nmargin (CV - vote) = {margin}; {'SHIP' if ship else 'DO NOT SHIP'} "
             f"(threshold: >= {MIN_SHIP_MARGIN})")

        result = {
            "categories": cats, "measure_sources": list(MEASURE_SOURCES), "ship_sources": list(SHIP_SOURCES),
            "n_measured": len(rows), "embed_model": model_name, "cv": cv, "neighbor_vote": vote,
            "margin": margin, "min_ship_margin": MIN_SHIP_MARGIN, "shipped": ship,
            "note": "measurement pool includes roasterdb (licence-restricted for shipping, same status as "
                    "coffeereview_kaggle) purely to give the 5-fold CV enough data to be meaningful; a shipped "
                    "model is retrained on ship_sources only. See docs/adr/0009-learned-attribute-model.md "
                    "Goal B2.",
        }

        if ship:
            ship_rows = [r for r in rows if r["source"] in SHIP_SOURCES]
            print(f"\nretraining the shippable model on {len(ship_rows)} beans (sources={SHIP_SOURCES})")
            Xs = np.array([vecs[r["id"]] for r in ship_rows], dtype=np.float64)
            Ys = np.zeros((len(ship_rows), len(cats)), dtype=np.int8)
            for i, r in enumerate(ship_rows):
                for c in row_categories(r, tag_to_cat):
                    Ys[i, cat_idx[c]] = 1
            model = MLPClassifier(hidden_layer_sizes=(HIDDEN_SIZE,), max_iter=300, early_stopping=True,
                                  random_state=0)
            model.fit(Xs, Ys)
            W1, W2 = model.coefs_
            b1, b2 = model.intercepts_
            assert model.out_activation_ == "logistic"
            doc = {"embed_model": model_name, "trained_at": datetime.now(timezone.utc).isoformat(),
                  "n_train": len(ship_rows), "hidden_size": HIDDEN_SIZE, "tags": cats,
                  "threshold": cv["best_threshold"], "activation": {"hidden": "relu", "output": "sigmoid"},
                  "W1": [[round(float(x), ROUND) for x in row] for row in W1],
                  "b1": [round(float(x), ROUND) for x in b1],
                  "W2": [[round(float(x), ROUND) for x in row] for row in W2],
                  "b2": [round(float(x), ROUND) for x in b2],
                  "note": "CATEGORY-level model (labels are the 7 SCA preference categories, not individual "
                          "flavor tags) -- docs/adr/0009-learned-attribute-model.md Goal B2. Same JSON shape "
                          "as config/tag_model.json; app/core/tagmodel.py's TagModel class loads it unchanged."}
            CONFIG_OUT.write_text(json.dumps(doc), encoding="utf-8")
            print(f"wrote {CONFIG_OUT} ({CONFIG_OUT.stat().st_size / 1024:.1f} KiB)")
            result["config_path"] = CONFIG_OUT.relative_to(ROOT).as_posix()
        else:
            CONFIG_OUT.unlink(missing_ok=True)
            print("not shipping: config/tag_model_open.json removed if it existed")

        PHASE2_OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {PHASE2_OUT}")
    finally:
        repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
