"""Train the learned per-attribute (acidity/body/sweetness) regressor that replaces the neighbour-average
attribute prediction for unknown beans (docs/adr/0009-learned-attribute-model.md). Sibling script to
scripts/train_tag_model.py -- same tag-free-embedding discipline (docs/adr/0008-learned-tag-model.md) so
training features match what app/graphs/analyze_bean.py ever embeds at runtime (user-typed text, never
flavor_tags).

Two variants:
  --variant full  (default): trains on every active source, ships config/attr_model.json.
  --variant open: trains ONLY on open-licence sources (cqi, roasters_kr, shopify -- excludes
    coffeereview_kaggle AND roasterdb), ships config/attr_model_open.json. If an attribute has fewer than
    MIN_OPEN_LABELS training labels (sweetness usually does), it is left out of the shipped file entirely --
    app/core/attrmodel.py's predict() returns None for it and the caller falls back to the neighbour average.

Each attribute independently picks ridge regression or a small MLP (128 hidden units) by 5-fold CV MAE over
its own training pool (rows with that attribute present); the two attributes need not agree.

Fixed held-out targets: `repo.random_coffee_ids_for_loo(200, seed=42, exclude_sources=("roasters_kr",),
sources=<variant's only_sources>)` -- for --variant full this is BYTE-IDENTICAL to scripts/train_tag_model.py's
draw, so this script reuses data/eval/loo_tagfree_query_embeddings.jsonl instead of re-embedding those 200.
--variant open draws its own 200 from the open-source pool (mostly cqi) since the pool differs; those get
freshly embedded (few enough to be cheap) and are NOT the same ids as the full variant's held-out set.

Outputs:
  - data/embedded/<embed model>/tagfree_query.jsonl  -- SAME cache file scripts/train_tag_model.py writes
    (key+hash keyed); this script extends it to cover every active bean with an attribute present, not just
    tagged ones (untagged beans, e.g. CQI, never contributed a training row to the tag model). Not checked in.
  - config/attr_model.json / attr_model_open.json (or .json.gz if >2MB) -- weights + metadata.
  - data/eval/phase2_attr_model.json / phase2_attr_model_open.json -- CV table, held-out table vs the
    neighbour average, same leak-free query embeddings for both.

Usage:
    uv run python scripts/train_attr_model.py --variant full
    uv run python scripts/train_attr_model.py --variant open
    uv run python scripts/train_attr_model.py --variant full --no-embed   # cached embeddings only
    uv run python scripts/train_attr_model.py --variant full --out-dir <tmp> --pin-holdout
        # retrain candidate: config/phase2 go to <tmp>, held-out ids pinned to data/eval/loo_tagfree_query_
        # embeddings.jsonl (full variant only -- the draw the shipped model was scored on)
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
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold
from sklearn.neural_network import MLPRegressor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.predict import predict_from_neighbors  # noqa: E402
from app.models import ATTRS  # noqa: E402
from app.repo import Repo  # noqa: E402
from pipeline import settings  # noqa: E402
from pipeline.embed import embedding_text  # noqa: E402
from pipeline.llm import embed_model, embedder_for  # noqa: E402
from pipeline.records import CoffeeRecord  # noqa: E402

N_HOLDOUT, HOLDOUT_SEED = 200, 42            # same draw as scripts/train_tag_model.py for --variant full
NEVER_LOO_TARGETS = ("roasters_kr",)         # facts-only beans; never a target, in either variant
OPEN_SOURCES = ("cqi", "roasters_kr", "shopify")   # Goal B(1): open-licence attribute training sources
MIN_OPEN_LABELS = 300                        # below this, an attribute is left out of the open model entirely
HIDDEN_SIZE = 128
K_NEIGHBORS = 10
SIZE_LIMIT_BYTES = 2 * 1024 * 1024
ROUND = 5

VARIANTS = {
    "full": {"only_sources": (), "config_name": "attr_model.json", "phase2_name": "phase2_attr_model.json"},
    "open": {"only_sources": OPEN_SOURCES, "config_name": "attr_model_open.json",
             "phase2_name": "phase2_attr_model_open.json"},
}


def fetch_rows(repo: Repo, only_sources: tuple[str, ...]) -> tuple[list[dict], dict[int, str]]:
    extra = " AND source = ANY(%(only)s)" if only_sources else ""
    rows = repo._all(
        "SELECT id, key, name, origin_country, process, roast_level, is_decaf, decaf_process,"
        " flavor_tags, flavor_summary, source, acidity, body, sweetness FROM coffees"
        f" WHERE active AND embedding IS NOT NULL{extra}"
        " AND (acidity IS NOT NULL OR body IS NOT NULL OR sweetness IS NOT NULL) ORDER BY id",
        {"only": list(only_sources)})
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


def pinned_holdout_ids() -> list[int]:
    """Held-out ids in drawn order from data/eval/loo_tagfree_query_embeddings.jsonl (full variant's draw)."""
    path = settings.EVAL_DIR / "loo_tagfree_query_embeddings.jsonl"
    return [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_loo_query_cache() -> dict[int, list[float]]:
    path = settings.EVAL_DIR / "loo_tagfree_query_embeddings.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["id"]] = row["vector"]
    return out


# ---- model selection: ridge vs MLP by 5-fold CV MAE ----------------------------------------------------------

def make_mlp() -> MLPRegressor:
    return MLPRegressor(hidden_layer_sizes=(HIDDEN_SIZE,), max_iter=500, early_stopping=True, random_state=0)


def cv_mae(model_fn, X: np.ndarray, y: np.ndarray, n_splits: int = 5, seed: int = 0) -> float:
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=seed)
    errs = []
    for tr, te in kf.split(X):
        m = model_fn()
        m.fit(X[tr], y[tr])
        pred = m.predict(X[te])
        errs.extend(abs(pred - y[te]))
    return float(np.mean(errs))


def pick_model(X: np.ndarray, y: np.ndarray) -> tuple[str, float, dict]:
    ridge_mae = cv_mae(lambda: Ridge(alpha=1.0), X, y)
    mlp_mae = cv_mae(make_mlp, X, y)
    cv = {"ridge_mae": round(ridge_mae, 4), "mlp_mae": round(mlp_mae, 4)}
    kind = "ridge" if ridge_mae <= mlp_mae else "mlp"
    cv["picked"] = kind
    return kind, (ridge_mae if kind == "ridge" else mlp_mae), cv


def fit_final(kind: str, X: np.ndarray, y: np.ndarray):
    model = Ridge(alpha=1.0) if kind == "ridge" else make_mlp()
    model.fit(X, y)
    return model


def to_spec(kind: str, model) -> dict:
    if kind == "ridge":
        return {"type": "ridge", "W": [round(float(w), ROUND) for w in model.coef_],
                "b": round(float(model.intercept_), ROUND)}
    W1, W2 = model.coefs_
    b1, b2 = model.intercepts_
    assert model.out_activation_ == "identity", f"expected identity output, got {model.out_activation_}"
    return {"type": "mlp", "hidden_size": HIDDEN_SIZE,
            "W1": [[round(float(x), ROUND) for x in row] for row in W1],
            "b1": [round(float(x), ROUND) for x in b1],
            "W2": [[round(float(x), ROUND) for x in row] for row in W2],
            "b2": [round(float(x), ROUND) for x in b2]}


def predict_spec(kind: str, spec: dict, x: list[float]) -> float:
    """Pure-python forward pass mirroring app/core/attrmodel.py, used only to sanity-check the held-out
    comparison against the exact numbers app/core/attrmodel.py will produce from this same JSON."""
    if kind == "ridge":
        s = spec["b"]
        for xi, wi in zip(x, spec["W"]):
            s += xi * wi
        return s
    hidden = [max(0.0, v) for v in _forward_layer(x, spec["W1"], spec["b1"])]
    out = _forward_layer(hidden, spec["W2"], spec["b2"])
    return out[0]


def _forward_layer(x: list[float], w: list[list[float]], b: list[float]) -> list[float]:
    out = list(b)
    for xi, row in zip(x, w):
        for j, wij in enumerate(row):
            out[j] += xi * wij
    return out


def write_config(config_path: Path, embed_model_name: str, attrs: dict) -> Path:
    doc = {"embed_model": embed_model_name, "trained_at": datetime.now(timezone.utc).isoformat(), "attrs": attrs}
    config_path.write_text(json.dumps(doc), encoding="utf-8")
    size = config_path.stat().st_size
    gz_path = config_path.with_suffix(config_path.suffix + ".gz")
    if size <= SIZE_LIMIT_BYTES:
        gz_path.unlink(missing_ok=True)
        print(f"wrote {config_path} ({size / 1024:.0f} KiB)")
        return config_path
    with gzip.open(gz_path, "wt", encoding="utf-8") as f:
        json.dump(doc, f)
    config_path.unlink()
    gz_size = gz_path.stat().st_size
    print(f"{config_path.name} was {size / 1024:.0f} KiB (over the 2MB budget); gzipped to "
         f"{gz_path} ({gz_size / 1024:.0f} KiB)")
    return gz_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=("full", "open"), default="full")
    ap.add_argument("--no-embed", action="store_true", help="reuse cached embeddings only, no NVIDIA API calls")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="write the config/phase2 outputs here instead of config/ and data/eval/")
    ap.add_argument("--pin-holdout", action="store_true",
                    help="(full variant) use the held-out ids in data/eval/loo_tagfree_query_embeddings.jsonl")
    args = ap.parse_args()
    if args.pin_holdout and args.variant != "full":
        ap.error("--pin-holdout only applies to --variant full")
    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
    spec = VARIANTS[args.variant]
    only_sources = spec["only_sources"]

    repo = Repo(settings.DATABASE_URL)
    try:
        rows, review_text = fetch_rows(repo, only_sources)
        by_id = {r["id"]: r for r in rows}
        print(f"variant={args.variant}: {len(rows)} active beans with an attribute present"
             f"{f' (sources={only_sources})' if only_sources else ''}")

        holdout_ids = pinned_holdout_ids() if args.pin_holdout else repo.random_coffee_ids_for_loo(
            N_HOLDOUT, HOLDOUT_SEED, exclude_sources=NEVER_LOO_TARGETS, sources=only_sources)
        holdout_set = set(holdout_ids)
        print(f"{len(holdout_ids)} fixed held-out targets excluded from training")

        embedder = embedder_for()
        model_name = embed_model()
        cache_path = settings.embedded_dir(model_name) / "tagfree_query.jsonl"

        train_rows = [r for r in rows if r["id"] not in holdout_set]
        train_items = [(r["key"], r["id"], tagfree_text(r, review_text)) for r in train_rows]
        train_cache = embed_missing(cache_path, train_items, embedder, args.batch, allow_embed=not args.no_embed)

        # held-out targets: reuse the shared loo cache when this draw matches it exactly (--variant full always
        # does, byte-identical to scripts/train_tag_model.py's draw); otherwise embed the (few) missing ones.
        loo_cache = load_loo_query_cache()
        reused = [cid for cid in holdout_ids if cid in loo_cache]
        print(f"{len(reused)}/{len(holdout_ids)} held-out targets reused from data/eval/loo_tagfree_query_"
             f"embeddings.jsonl (no new API calls for those)")
        need_embed_ids = [cid for cid in holdout_ids if cid not in loo_cache]
        holdout_items = [(by_id[cid]["key"], cid, tagfree_text(by_id[cid], review_text)) for cid in need_embed_ids]
        holdout_cache = embed_missing(cache_path, holdout_items, embedder, args.batch,
                                      allow_embed=not args.no_embed) if holdout_items else {}
        print(f"total embedding API requests this run: {embedder.requests}")

        holdout_vec: dict[int, list[float]] = dict(loo_cache)
        for key, cid, _ in holdout_items:
            holdout_vec[cid] = holdout_cache[key]["vector"]
        holdout_vec = {cid: holdout_vec[cid] for cid in holdout_ids}

        attrs_out: dict = {}
        phase2_attrs: dict = {}
        for a in ATTRS:
            a_train_rows = [r for r in train_rows if r[a] is not None]
            n_labels = len(a_train_rows)
            print(f"\n--- {a}: {n_labels} training labels ---")
            if args.variant == "open" and n_labels < MIN_OPEN_LABELS:
                print(f"  skipped: {n_labels} < MIN_OPEN_LABELS={MIN_OPEN_LABELS}; open model ships no {a} "
                     f"(falls back to the neighbour average)")
                phase2_attrs[a] = {"shipped": False, "n_labels": n_labels, "reason": "too few open-licence labels"}
                continue
            X = np.array([train_cache[r["key"]]["vector"] for r in a_train_rows], dtype=np.float64)
            y = np.array([r[a] for r in a_train_rows], dtype=np.float64)
            kind, cv_mae_best, cv = pick_model(X, y)
            print(f"  CV: {cv}")
            model = fit_final(kind, X, y)
            attr_spec = to_spec(kind, model)
            attrs_out[a] = {**attr_spec, "n_train": n_labels, "cv": cv}

            # held-out comparison: model vs neighbour average, SAME leak-free query embeddings for both
            model_errs, model_within1 = [], 0
            neigh_errs, neigh_within1 = [], 0
            n_scored = 0
            for cid in holdout_ids:
                truth = (by_id.get(cid) or {}).get(a)
                if truth is None:
                    continue
                vec = holdout_vec[cid]
                model_pred = min(5.0, max(1.0, predict_spec(kind, attr_spec, vec)))
                # NO source restriction here: this mirrors production (app/graphs/analyze_bean.py never
                # restricts the neighbour pool by source either), so the baseline is the real neighbour-average
                # behaviour, not an artificially narrowed one.
                near = repo.neighbors(vec, k=K_NEIGHBORS, exclude_id=cid)
                neigh_pred = getattr(predict_from_neighbors(near), a)
                n_scored += 1
                model_errs.append(abs(model_pred - truth))
                model_within1 += abs(model_pred - truth) <= 1
                if neigh_pred is not None:
                    neigh_errs.append(abs(neigh_pred - truth))
                    neigh_within1 += abs(neigh_pred - truth) <= 1
            phase2_attrs[a] = {
                "shipped": True, "n_labels": n_labels, "model_type": kind, "cv": cv,
                "holdout": {
                    "n_scored": n_scored,
                    "model": {"mae": round(float(np.mean(model_errs)), 4) if model_errs else None,
                             "within1": round(model_within1 / n_scored, 4) if n_scored else None},
                    "neighbor_avg": {"n": len(neigh_errs),
                                    "mae": round(float(np.mean(neigh_errs)), 4) if neigh_errs else None,
                                    "within1": round(neigh_within1 / len(neigh_errs), 4) if neigh_errs else None},
                    "note": "both scored on the SAME tag-free (leak-free) query embeddings of the fixed "
                            "held-out targets; neighbor_avg is predict_from_neighbors's weighted average "
                            "(production defaults, k=10) over the production neighbour pool.",
                },
            }
            print(f"  held-out (n={n_scored}): model_mae={phase2_attrs[a]['holdout']['model']['mae']} "
                 f"neighbor_mae={phase2_attrs[a]['holdout']['neighbor_avg']['mae']}")

        config_path = (args.out_dir or settings.CONFIG_DIR) / spec["config_name"]
        cfg_out = write_config(config_path, model_name, attrs_out) if attrs_out else None
        if cfg_out is None:
            print("no attribute cleared the label threshold; not writing a config file")

        phase2 = {
            "variant": args.variant, "only_sources": list(only_sources), "embed_model": model_name,
            "holdout_n": len(holdout_ids), "holdout_seed": HOLDOUT_SEED,
            "config_path": (cfg_out.relative_to(ROOT).as_posix() if cfg_out.is_relative_to(ROOT) else str(cfg_out))
                           if cfg_out else None,
            "holdout_pinned": args.pin_holdout,
            "attrs": phase2_attrs,
        }
        phase2_path = (args.out_dir or settings.EVAL_DIR) / spec["phase2_name"]
        phase2_path.write_text(json.dumps(phase2, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {phase2_path}")
    finally:
        repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
