"""Phase 2 evaluation: hard-constraint violations, leave-one-out prediction accuracy, learning convergence,
and latency benchmarks for the ADRs. Results go to data/eval/phase2_<name>.json."""
import asyncio
import hashlib
import json
import sys
import time
from collections import Counter

import yaml

from app.core.explain import GUARD_REJECTS, explain_messages
from app.core.predict import predict_from_neighbors
from app.core.scoring import mmr_top_k, passes, score_item
from app.core.simulate import simulate_convergence
from app.models import ATTRS, Item, Prediction, Profile
from pipeline import settings
from pipeline.llm import embed_model

# Knowledge-base variants (sources left out) for the open-data comparison.
#   full      = everything, incl. the licence-restricted coffeereview (Kaggle) data
#   open      = open-licence sources only; roasters_kr is left out too, so "open" means exactly what it did
#               before the Korean roastery data existed and its numbers stay comparable
#   open_plus = open + Korean roastery facts (roasters_kr)
VARIANTS: dict[str, tuple[str, ...]] = {
    "full": (),
    "open": ("coffeereview_kaggle", "roasters_kr"),
    "open_plus": ("coffeereview_kaggle",),
}
OPEN_LICENSE_EXCLUDE = VARIANTS["open"]
# Fixed LOO targets for compare3: open-licence beans whose acidity/body are human ratings (CQI Q-grader cupping
# scores). Never roaster beans (their attributes are LLM estimates from note words, not ratings) and never
# coffeereview (not open), so the same targets exist in every variant and only the neighbour pool changes.
LOO_TARGET_SOURCES = ("cqi",)
# Never LOO targets in any command: facts-only roaster beans have no human ratings. Keeps the plain `loo` target
# sample identical to the one drawn before roasters_kr was loaded.
NEVER_LOO_TARGETS = ("roasters_kr",)
# Second, SEPARATE fixed target set for compare3's body accuracy (docs/adr/0010-body-heaviness.md): CQI's body
# is None for all 200 acidity targets above, so compare3 can never score body against them (n=0, structurally,
# not a bug). coffeereview_kaggle beans are the only open-licence source with a human-written mouthfeel
# description a body label can be checked against (the LLM heaviness relabel in data/enriched/
# body_heaviness.jsonl), so this draws 200 of THOSE (seed 42, filtered to ones that got a body label) instead.
# coffeereview_kaggle is already excluded from the "open"/"open_plus" neighbour pools (VARIANTS above), so for
# those two variants these targets can never leak into their own neighbours; "full" includes coffeereview as a
# source but each target is still excluded from being its own neighbour (exclude_id), same as any other LOO
# target. These labels are an EVALUATION-ONLY reference: coffeereview_kaggle coffees are never served by the
# open builds and never used to train the attribute model for them (config/attr_model_open.json) -- see
# scripts/competition/build_open_db.sh. ("coffeereview 라벨은 채점 기준으로만 사용, 앱·학습에는 미사용.")
BODY_TARGET_SOURCES = ("coffeereview_kaggle",)

PERSONAS = [
    ("디카페인+산미", Profile(caffeine_rule="decaf_only", milk_ok=True, acidity=4.5, body=2.5, sweetness=3,
                          flavor_weights={"fruity": 0.6, "floral": 0.5})),
    ("저카페인+우유X", Profile(caffeine_rule="low", milk_ok=False, acidity=2, body=4, sweetness=3,
                           flavor_weights={"nutty/cocoa": 0.6})),
    ("제한없음", Profile()),
    ("디카페인+우유X+단맛", Profile(caffeine_rule="decaf_only", milk_ok=False, acidity=2, body=3, sweetness=4.5,
                              flavor_weights={"sweet": 0.6})),
]
# Deliberately NOT the scoring module's list: an independent check. Hand labels first (every menu name in the
# DB was labelled by a person), this marker list only for names the labels don't cover.
MILK_LABELS: dict[str, bool] = yaml.safe_load(
    (settings.CURATED_DIR / "menu_milk_labels.yaml").read_text(encoding="utf-8"))
MILK_MARKERS = ("라떼", "우유", "밀크", "크림", "카푸치노", "플랫화이트", "모카", "프라푸치노", "latte", "milk", "cream",
                "cappuccino", "mocha", "frappuccino")


def has_milk(name: str) -> bool:
    if name in MILK_LABELS:
        return MILK_LABELS[name]
    n = "".join(name.lower().split())
    return any(m in n for m in MILK_MARKERS)


def independent_ok(profile: Profile, item: Item, raw: dict | None, brand_decaf_available: bool) -> bool:
    name = raw["name"] if raw else item.name
    decaf_capable = (raw["is_decaf"] or raw["decaf_option"]) if raw else brand_decaf_available
    served_decaf = (raw["is_decaf"] or (raw["decaf_option"] and item.order_decaf)) if raw         else (brand_decaf_available and item.order_decaf)
    caffeine = raw["caffeine_mg"] if raw else None
    if profile.caffeine_rule == "decaf_only" and not served_decaf:
        return False
    # an official "디카페인" SKU with real caffeine (chocolate, tea shots) is not decaf for these guests
    if raw and raw["is_decaf"] and caffeine is not None and (
            (profile.caffeine_rule == "decaf_only" and caffeine > 30) or (profile.caffeine_rule == "low" and caffeine > 100)):
        return False
    if profile.caffeine_rule == "low" and not (decaf_capable or (caffeine is not None and caffeine <= 100)):
        return False
    return profile.milk_ok or not has_milk(name)


DECAF_SHOWN_MAX_MG = 30     # a decaf-only guest's card may show more only as a flagged estimate (or not at all)


def caffeine_display_ok(profile: Profile, item: Item) -> bool:
    """Independent of the repo's estimate logic: a decaf-only card never shows a caffeine figure above
    DECAF_SHOWN_MAX_MG unless it is flagged (caffeine_mg_note), and an order-decaf card always carries the flag --
    unflagged, its figure would read as the caffeine the guest actually gets."""
    if profile.caffeine_rule != "decaf_only":
        return True
    if item.order_decaf and not item.caffeine_mg_note:
        return False
    return item.caffeine_mg is None or item.caffeine_mg <= DECAF_SHOWN_MAX_MG or bool(item.caffeine_mg_note)


def violation_rate(repo) -> dict:
    tag_to_cat, _ = repo.taxonomy()
    checked = violations = 0
    details = []
    for label, p in PERSONAS:
        for b in repo.list_brands():
            items = repo.brand_items(b["key"], p.caffeine_rule)
            top = mmr_top_k([(i, score_item(p, i, tag_to_cat)) for i in items if passes(p, i)[0]], tag_to_cat)
            for i, _ in top:
                checked += 1
                raw = repo.raw_menu(i.menu_item_id) if i.menu_item_id is not None else None
                if not independent_ok(p, i, raw, b["decaf_available"]):
                    violations += 1
                    details.append({"persona": label, "brand": b["key"], "item": i.name})
                elif not caffeine_display_ok(p, i):
                    violations += 1
                    details.append({"persona": label, "brand": b["key"], "item": i.name,
                                    "shown_caffeine_mg": i.caffeine_mg})
    return {"checked": checked, "violations": violations, "rate": violations / checked if checked else 0.0,
            "details": details}


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return (p, r, f1)


def tag_prf(truth: set, pred: set) -> tuple[float, float, float]:
    """Precision/recall/F1 for one truth/pred tag-set pair. `loo_accuracy` sums TP/FP/FN across all targets
    itself (micro average over the total counts) rather than averaging per-item calls to this function."""
    return _prf(len(truth & pred), len(pred - truth), len(truth - pred))


LOO_TAGFREE_QUERY_EMBEDDINGS = settings.EVAL_DIR / "loo_tagfree_query_embeddings.jsonl"


def _load_tagfree_query_embeddings() -> dict[int, list[float]]:
    """Cached tag-free query embeddings for the fixed n=200/seed=42 LOO targets
    (scripts/train_tag_model.py writes this for exactly that id set; see docs/adr/0008-learned-tag-model.md).
    Missing file -> {} and callers fall back to the stored (possibly tag-leaked) embedding, same as before."""
    if not LOO_TAGFREE_QUERY_EMBEDDINGS.exists():
        return {}
    out = {}
    for line in LOO_TAGFREE_QUERY_EMBEDDINGS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["id"]] = row["vector"]
    return out


def loo_accuracy(repo, n: int = 200, seed: int = 42, exclude_sources: tuple[str, ...] = (),
                 target_sources: tuple[str, ...] = (), tag_model=None, attr_model=None,
                 fixed_ids: list[int] | None = None) -> dict:
    """Attribute (acidity/body/sweetness) predictions are unchanged: stored query embedding, k=10 neighbours.

    `fixed_ids`, when given, is used as the target id list verbatim instead of drawing one from
    `repo.random_coffee_ids_for_loo` -- `n`/`seed`/`target_sources` are then only carried into the output for
    context. This is how `compare3`'s separate body target set (`BODY_TARGET_SOURCES`, drawn once with
    `body_target_ids` so it is identical across variants) is scored per variant: the neighbour pool still
    varies with `exclude_sources`, only the target ids are fixed from outside.

    Tag predictions default to LEAK-FREE query embeddings when cached (see `_load_tagfree_query_embeddings`):
    pipeline/embed.py's embedding_text() folds a coffee's own already-known flavor_tags into its stored
    embedding, so scoring `repo.coffee_embedding(cid)` against `truth.tags` is partly circular (docs/adr/0008).
    This affects only the `tags`/`tags_model` blocks below (not the attribute blocks, which never touch tags).
    `tag_model`, when given (an app.core.tagmodel.TagModel), adds a `tags_model` block scored on the SAME
    query embedding as `tags`, so the two are directly comparable.

    `attr_model`, when given (an app.core.attrmodel.AttrModel), adds an `attrs_model` block: per-attribute MAE/
    within1 for the learned regressor vs the neighbour average, BOTH scored on the same leak-free query
    embedding used for tags (docs/adr/0009-learned-attribute-model.md) -- a fair comparison independent of the
    stored-embedding tag leak above, unlike the plain attribute block."""
    tag_to_cat, _ = repo.taxonomy()
    base_rates = repo.tag_base_rates()
    tagfree = _load_tagfree_query_embeddings()
    stats = {a: {"n": 0, "exact": 0, "within1": 0, "abs_err": 0.0} for a in ATTRS}
    by_conf: dict[str, dict] = {}
    neighbor_sources: Counter = Counter()
    with_tags = 0
    tag_n = tag_tp = tag_fp = tag_fn = 0
    cat_tp = cat_fp = cat_fn = 0
    model_n = model_tp = model_fp = model_fn = 0
    model_cat_tp = model_cat_fp = model_cat_fn = 0
    attrs_model_stats = {a: {"n": 0, "model_abs_err": 0.0, "model_within1": 0, "neighbor_n": 0,
                             "neighbor_abs_err": 0.0, "neighbor_within1": 0} for a in ATTRS}
    leak_free_used = 0
    if fixed_ids is not None:
        ids = fixed_ids
    else:
        not_targets = tuple(dict.fromkeys(exclude_sources + NEVER_LOO_TARGETS))
        ids = repo.random_coffee_ids_for_loo(n, seed, exclude_sources=not_targets, sources=target_sources)
    for cid in ids:
        truth = repo.get_coffee(cid)
        emb = repo.coffee_embedding(cid)
        near = repo.neighbors(emb, k=10, exclude_id=cid, exclude_sources=exclude_sources)
        neighbor_sources.update(repo.coffee_sources([x.coffee_id for x in near]).values())
        pred = predict_from_neighbors(near, base_rates=base_rates)          # attributes: unchanged
        with_tags += bool(pred.tags)
        for a in ATTRS:
            t, v = truth.attr(a), getattr(pred, a)
            if t is None or v is None:
                continue
            s = stats[a]
            s["n"] += 1
            s["exact"] += round(v) == t
            s["within1"] += abs(v - t) <= 1
            s["abs_err"] += abs(v - t)
        if truth.acidity is not None and pred.acidity is not None:
            c = by_conf.setdefault(pred.confidence, {"n": 0, "within1": 0})
            c["n"] += 1
            c["within1"] += abs(pred.acidity - truth.acidity) <= 1

        tag_vec = tagfree.get(cid)
        if tag_vec is not None:
            leak_free_used += 1
            tag_near = repo.neighbors(tag_vec, k=10, exclude_id=cid, exclude_sources=exclude_sources)
        else:
            tag_vec, tag_near = emb, near                # no cached leak-free embedding: fall back, as before
        tag_pred = predict_from_neighbors(tag_near, base_rates=base_rates)

        if attr_model is not None:
            model_values = attr_model.predict(tag_vec)
            for a in ATTRS:
                t = truth.attr(a)
                if t is None:
                    continue
                s = attrs_model_stats[a]
                neighbor_v = getattr(tag_pred, a)
                if neighbor_v is not None:
                    s["neighbor_n"] += 1
                    s["neighbor_abs_err"] += abs(neighbor_v - t)
                    s["neighbor_within1"] += abs(neighbor_v - t) <= 1
                model_v = model_values.get(a)
                if model_v is not None:
                    s["n"] += 1
                    s["model_abs_err"] += abs(model_v - t)
                    s["model_within1"] += abs(model_v - t) <= 1

        truth_tags = {t.lower() for t in truth.tags}
        if truth_tags:
            tag_n += 1
            pred_tags = {t.lower() for t in tag_pred.tags}
            tag_tp += len(truth_tags & pred_tags)
            tag_fp += len(pred_tags - truth_tags)
            tag_fn += len(truth_tags - pred_tags)
            truth_cats = {tag_to_cat[t] for t in truth_tags if t in tag_to_cat}
            pred_cats = {tag_to_cat[t] for t in pred_tags if t in tag_to_cat}
            cat_tp += len(truth_cats & pred_cats)
            cat_fp += len(pred_cats - truth_cats)
            cat_fn += len(truth_cats - pred_cats)

            if tag_model is not None:
                model_n += 1
                model_tags = {t for t, _ in tag_model.tags(tag_vec)}
                model_tp += len(truth_tags & model_tags)
                model_fp += len(model_tags - truth_tags)
                model_fn += len(truth_tags - model_tags)
                mtc = {tag_to_cat[t] for t in truth_tags if t in tag_to_cat}
                mpc = {tag_to_cat[t] for t in model_tags if t in tag_to_cat}
                model_cat_tp += len(mtc & mpc)
                model_cat_fp += len(mpc - mtc)
                model_cat_fn += len(mtc - mpc)
    def rate(d: dict, k: str) -> float | None:
        return round(d[k] / d["n"], 4) if d["n"] else None

    total_nb = sum(neighbor_sources.values())
    tag_p, tag_r, tag_f1 = _prf(tag_tp, tag_fp, tag_fn)
    _, _, cat_f1 = _prf(cat_tp, cat_fp, cat_fn)
    out = {"n": n, "seed": seed, "exclude_sources": list(exclude_sources),
          "target_sources": list(target_sources) or "all non-excluded sources",
          "targets": len(ids), "target_ids_sha1": hashlib.sha1(",".join(map(str, sorted(ids))).encode()).hexdigest(),
          "neighbor_source_share": {k: round(v / total_nb, 4) for k, v in neighbor_sources.most_common()},
          "predictions_with_tags": round(with_tags / len(ids), 4) if ids else None,
          "embedding_model": embed_model(),
          **{a: {"n": s["n"], "exact": rate(s, "exact"), "within1": rate(s, "within1"),
                "mae": round(s["abs_err"] / s["n"], 4) if s["n"] else None} for a, s in stats.items()},
          "acidity_within1_by_confidence": {k: {"n": v["n"], "within1": rate(v, "within1")} for k, v in by_conf.items()},
          "tags_query_embeddings": {"leak_free": leak_free_used, "stored_fallback": len(ids) - leak_free_used,
                                    "note": "leak-free tag-free query embeddings are the default when cached "
                                            "(data/eval/loo_tagfree_query_embeddings.jsonl, exactly the "
                                            "n=200/seed=42 targets); other target draws fall back to the "
                                            "stored (tag-including) embedding, as before docs/adr/0008."},
          "tags": {"n": tag_n, "precision": round(tag_p, 4), "recall": round(tag_r, 4), "f1": round(tag_f1, 4),
                  "category_f1": round(cat_f1, 4)}}
    if tag_model is not None:
        mp, mr, mf1 = _prf(model_tp, model_fp, model_fn)
        _, _, mcat_f1 = _prf(model_cat_tp, model_cat_fp, model_cat_fn)
        out["tags_model"] = {"n": model_n, "precision": round(mp, 4), "recall": round(mr, 4), "f1": round(mf1, 4),
                            "category_f1": round(mcat_f1, 4)}
    if attr_model is not None:
        def block(s: dict, n: int, err_key: str, within1_key: str) -> dict:
            return {"n": n, "mae": round(s[err_key] / n, 4) if n else None,
                   "within1": round(s[within1_key] / n, 4) if n else None}

        out["attrs_model"] = {
            a: {"model": block(s, s["n"], "model_abs_err", "model_within1"),
               "neighbor": block(s, s["neighbor_n"], "neighbor_abs_err", "neighbor_within1"),
               "note": "both scored on the same leak-free tag-free query embedding as `tags` above "
                       "(docs/adr/0009-learned-attribute-model.md); neighbor = predict_from_neighbors's "
                       "weighted average over that embedding's neighbours, production defaults (k=10)."}
            for a, s in attrs_model_stats.items()}
    return out


def tag_names(repo) -> dict:
    """Fix B check: every distinct flavor tag on active coffees should have a Korean name after
    config/tag_ko_extra.yaml is merged into Repo.taxonomy() (flavor_taxonomy.name_ko, plus the extra
    file for off-wheel tags). `missing_ko` should be empty; if not, add the tag to tag_ko_extra.yaml."""
    _, tag_ko = repo.taxonomy()
    base_rates = repo.tag_base_rates()             # keys = every distinct tag on active coffees with >=1 tag
    missing = sorted(t for t in base_rates if t not in tag_ko)
    return {"total_tags": len(base_rates), "n_missing": len(missing), "missing_ko": missing}


def coverage(repo, exclude_sources: tuple[str, ...] = ()) -> dict:
    return {"exclude_sources": list(exclude_sources), **repo.coverage_counts(exclude_sources=exclude_sources)}


def rank_decaf(profile: Profile, beans: list[tuple[Item, str]], tag_to_cat: dict[str, str], k: int = 5) -> dict:
    """Candidates for a decaf-only drinker among decaf beans, and the top k by the app's fit score.

    "with_evidence" = beans that have an attribute or a flavor tag; the rest can only get the neutral 0.5."""
    ok = [(i, src) for i, src in beans if passes(profile, i)[0]]
    evid = [(i, src) for i, src in ok if i.tags or any(i.attr(a) is not None for a in ATTRS)]
    scored = sorted(((score_item(profile, i, tag_to_cat), i, src) for i, src in evid), key=lambda x: (-x[0], x[1].name))
    return {"candidates": len(ok), "with_evidence": len(evid),
            "by_source": dict(Counter(src for _, src in ok).most_common()),
            "top": [{"name": i.name, "roaster": i.brand, "source": src, "score": round(sc, 4), "acidity": i.acidity,
                     "body": i.body, "sweetness": i.sweetness, "tags": list(i.tags)} for sc, i, src in scored[:k]]}


def decaf_probe(repo, exclude_sources: tuple[str, ...] = (), k: int = 5) -> dict:
    label, profile = PERSONAS[0]                          # 디카페인+산미: decaf_only, acidity 4.5, fruity/floral
    tag_to_cat, _ = repo.taxonomy()
    return {"persona": label, **rank_decaf(profile, repo.decaf_coffees(exclude_sources=exclude_sources), tag_to_cat, k)}


def body_target_ids(repo, n: int = 200, seed: int = 42) -> list[int]:
    """The fixed body-only target set for compare3 (see BODY_TARGET_SOURCES): coffeereview_kaggle beans that
    have a heaviness body label, drawn once so the SAME ids are scored against every variant's neighbour pool
    (mirrors how LOO_TARGET_SOURCES/CQI is drawn once for the acidity/body-by-CQI targets above)."""
    return repo.random_coffee_ids_for_loo(n, seed, sources=BODY_TARGET_SOURCES, require=("body",))


def compare3(repo, n: int = 200, seed: int = 42) -> dict:
    """full vs open vs open_plus on the same fixed LOO targets, plus coverage and the Korean decaf probe.

    Body accuracy gets its OWN fixed target set (`body_target_ids`/`BODY_TARGET_SOURCES`) because the CQI
    targets above never have a body label (docs/adr/0010-body-heaviness.md) -- `loo["body"]` here is always
    n=0, structurally; the real body comparison is `body_loo["body"]`."""
    out: dict = {
        "variants": {name: {"exclude_sources": list(xs)} for name, xs in VARIANTS.items()},
        "loo_targets": {
            "sources": list(LOO_TARGET_SOURCES), "n": n, "seed": seed,
            "why": "Held fixed across variants: open-licence beans with human-rated acidity/body (CQI cupping). "
                   "Roaster beans are never targets (no human ratings; their attributes are LLM estimates from "
                   "flavor note words); only the neighbour pool changes between variants. CQI has no sweetness "
                   "or flavor tags, so only acidity/body accuracy is measured; predictions_with_tags shows how "
                   "often the pool can suggest any flavor tags for these targets (no ground truth)."},
        "body_targets": {
            "sources": list(BODY_TARGET_SOURCES), "n": n, "seed": seed,
            "why": "CQI's body is None for every one of the acidity targets above (ADR 0010), so `loo.body` "
                   "here is always n=0 -- a structural limit, not a bug. This SEPARATE fixed set of "
                   "coffeereview_kaggle beans with an LLM-relabelled heaviness value (data/enriched/"
                   "body_heaviness.jsonl) is held fixed the same way, so `body_loo.body` reports a real "
                   "within-1/MAE per variant. coffeereview 라벨은 채점 기준으로만 사용, 앱·학습에는 미사용 -- "
                   "coffeereview_kaggle is already excluded from the open/open_plus neighbour pools "
                   "(VARIANTS), so these targets never leak into what those two variants can serve or learn "
                   "from; for `full` each target is still excluded from being its own neighbour, same as any "
                   "other LOO target."},
    }
    hashes, body_hashes = set(), set()
    body_ids = body_target_ids(repo, n, seed)
    for name, xs in VARIANTS.items():
        loo = loo_accuracy(repo, n, seed, exclude_sources=xs, target_sources=LOO_TARGET_SOURCES)
        hashes.add(loo["target_ids_sha1"])
        body_loo = loo_accuracy(repo, n, seed, exclude_sources=xs, target_sources=BODY_TARGET_SOURCES,
                                fixed_ids=body_ids)
        body_hashes.add(body_loo["target_ids_sha1"])
        out["variants"][name].update(coverage=repo.coverage_counts(exclude_sources=xs), loo=loo,
                                     body_loo=body_loo, decaf_probe=decaf_probe(repo, xs))
    out["loo_targets"]["identical_across_variants"] = len(hashes) == 1
    out["body_targets"]["identical_across_variants"] = len(body_hashes) == 1
    return out


def loo_repro(repo, n: int = 200, seed: int = 42) -> dict:
    """Run loo_accuracy twice and check the JSON output is byte-identical.

    What this shows: on the same index and the same queries the results are identical, and the id tie-break
    (ADR 0006) removes the dependence on physical row order among equal distances. What it does not show:
    identity across a reload — HNSW graph construction is randomised, so a rebuilt index can return a
    different candidate set; the tie-break only reduces that noise, it does not guarantee equality."""
    dumps = [json.dumps(loo_accuracy(repo, n, seed), ensure_ascii=False, sort_keys=True) for _ in range(2)]
    hashes = [hashlib.sha256(d.encode()).hexdigest() for d in dumps]
    return {"runs": 2, "identical": hashes[0] == hashes[1], "sha256": hashes}


def convergence(repo, users: int = 200, seed: int = 1) -> dict:
    tag_to_cat, _ = repo.taxonomy()
    return simulate_convergence(repo.random_coffees_with_attrs(400, seed), tag_to_cat, users=users, seed=seed)


def bench(repo) -> dict:
    """Real-LLM latency numbers for the ADRs: sequential vs parallel explanations, time to first token."""
    from app import llm
    from app.config import EXPLAIN_TASK

    items = repo.random_coffees_with_attrs(3, 7)
    profile = PERSONAS[0][1]
    msgs = [explain_messages(i, profile, 0.8) for i in items]

    async def one(m):
        start, first = time.perf_counter(), None
        async for _ in llm.astream_text(EXPLAIN_TASK, m):
            first = first or time.perf_counter() - start
        return first, time.perf_counter() - start

    async def run():
        t0 = time.perf_counter()
        seq = [await one(m) for m in msgs]
        sequential = time.perf_counter() - t0
        t1 = time.perf_counter()
        par = await asyncio.gather(*(one(m) for m in msgs))
        parallel = time.perf_counter() - t1
        return seq, sequential, par, parallel

    seq, sequential, _par, parallel = asyncio.run(run())
    return {"model": settings.load_config("models.yaml")["tasks"][EXPLAIN_TASK]["model"],
            "sequential_total_s": round(sequential, 2), "parallel_total_s": round(parallel, 2),
            "first_token_s": [round(f, 2) for f, _ in seq],
            "full_answer_s": [round(t, 2) for _, t in seq]}


EXPLAIN_CASES = settings.EVAL_DIR / "explain_cases.yaml"
JUDGES = ("judge", "judge2")          # labels in the result JSON (kept stable so saved runs stay comparable)
# label -> models.yaml task. The second explain judge has its own task: `judge2` stays the phase-1 gold-set
# labeller (nemotron), which is also the explain model and would grade itself here.
EXPLAIN_JUDGE_TASKS = {"judge": "judge", "judge2": "judge_explain2"}
JUDGE_ATTEMPTS = 3
RULE_CHECKS = ("foreign_words", "length", "numbers_grounded", "condition_mentioned", "polarity")


def load_explain_cases(path=EXPLAIN_CASES) -> list[dict]:
    """Hand-written guests × items (no DB, no personal data): each case can build explain_messages on its own."""
    profiles = dict(PERSONAS)
    out = []
    for c in yaml.safe_load(path.read_text(encoding="utf-8")):
        it = dict(c["item"])
        it["tags"] = tuple(it.get("tags") or ())
        pred = Prediction(**c["prediction"]) if c.get("prediction") else None
        out.append({"id": c["id"], "persona": c["persona"], "profile": profiles[c["persona"]], "item": Item(**it),
                    "score": float(c["score"]), "violation": c.get("violation"), "prediction": pred})
    return out


def _percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 2)


def summarize_explain_quality(rows: list[dict]) -> dict:
    """Headline numbers over LLM-generated rows only (template fallbacks are counted, not scored)."""
    gen = [r for r in rows if not r["fallback"]]
    both = [r for r in gen if all(r["judges"][j] is not None for j in JUDGES)]

    def rate(k: int, n: int) -> float | None:
        return round(k / n, 4) if n else None

    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 2) if xs else None

    raw = [r for r in rows if r.get("raw_rules") is not None]
    guard = [r.get("guard") for r in rows if r.get("guard")]
    return {
        "n": len(rows), "generated": len(gen), "fallbacks": len(rows) - len(gen),
        "guard_fallbacks": sum(1 for r in rows if r["fallback"] and r.get("guard") in GUARD_REJECTS),
        "guard_events": {e: guard.count(e) for e in sorted(set(guard))},
        "raw_rule_pass": sum(all(r["raw_rules"].values()) for r in raw), "raw_n": len(raw),
        "rule_pass": sum(all(r["rules"].values()) for r in gen),
        "rule_pass_rate": rate(sum(all(r["rules"].values()) for r in gen), len(gen)),
        "rule_failures": {c: sum(not r["rules"][c] for r in gen) for c in RULE_CHECKS},
        "judged_both": len(both),
        "no_contradiction_both": sum(not any(r["judges"][j]["contradiction"] for j in JUDGES) for r in both),
        "no_contradiction_rate_both": rate(sum(not any(r["judges"][j]["contradiction"] for j in JUDGES)
                                               for r in both), len(both)),
        "no_hallucination_both": sum(not any(r["judges"][j]["hallucination"] for j in JUDGES) for r in both),
        "no_hallucination_rate_both": rate(sum(not any(r["judges"][j]["hallucination"] for j in JUDGES)
                                               for r in both), len(both)),
        "helpful_mean": {j: mean([r["judges"][j]["helpful"] for r in gen if r["judges"][j] is not None])
                         for j in JUDGES},
        "judge_agreement": {k: rate(sum(r["judges"]["judge"][k] == r["judges"]["judge2"][k] for r in both), len(both))
                            for k in ("contradiction", "hallucination")},
        "judge_failures": {j: sum(r["judges"][j] is None for r in rows) for j in JUDGES},
        "first_token_p50": _percentile([r["first_token_s"] for r in gen if r["first_token_s"] is not None], 0.5),
        "first_token_p95": _percentile([r["first_token_s"] for r in gen if r["first_token_s"] is not None], 0.95),
    }


EXPLAIN_QUALITY_OUT = settings.EVAL_DIR / "phase2_explain_quality.json"
RULE_SUMMARY_FIELDS = ("rule_pass", "rule_pass_rate", "rule_failures")


def rescore_explain_quality(doc: dict, cases: list[dict]) -> dict:
    """Re-run the deterministic rule checks over saved explanations (no LLM calls) after the checker changed.

    Only the rule fields change (each case's `rules`/`rule_pass`, the summary's RULE_SUMMARY_FIELDS); the text,
    timings and judge verdicts are the ones recorded at generation time. `rules_rescored.before` keeps the
    rule numbers as first recorded, so repeated re-scores still compare against the generation run."""
    from app.core.explain_check import check_explanation

    by_id = {c["id"]: c for c in cases}
    rows = []
    for r in doc["cases"]:
        c = by_id[r["id"]]
        msgs = explain_messages(c["item"], c["profile"], c["score"], c["prediction"], c["violation"])
        rules = check_explanation(r["text"], json.loads(msgs[1]["content"]), r["score"], r["violation"])
        rows.append({**r, "rules": rules, "rule_pass": all(rules.values())})
    fresh = summarize_explain_quality(rows)
    before = (doc.get("rules_rescored") or {}).get("before") or {k: doc["summary"][k] for k in RULE_SUMMARY_FIELDS}
    return {**doc, "summary": {**doc["summary"], **{k: fresh[k] for k in RULE_SUMMARY_FIELDS}},
            "rules_rescored": {"note": "rule checks re-run offline over the saved texts with the current "
                                       "app/core/explain_check.py (`python -m app.eval explain_recheck`); "
                                       "generation and judge fields are from the original run",
                               "before": before},
            "cases": rows}


def explain_recheck(_repo=None) -> dict:
    return rescore_explain_quality(json.loads(EXPLAIN_QUALITY_OUT.read_text(encoding="utf-8")), load_explain_cases())


def explain_quality(repo) -> dict:
    """24 hand-written cases → one real explanation each (same path and deadline as the app) → deterministic rule
    checks + two LLM judges. Costs ~72 LLM calls, so it is not part of `all`."""
    import httpx

    from app import config, llm
    from app.core.explain import finalize_explanation, template_explanation
    from app.core.explain_check import check_explanation
    from app.core.judge import Verdict, judge_messages
    from pipeline.llm import LLMError

    tag_to_cat, tag_ko = repo.taxonomy()
    tasks = settings.load_config("models.yaml")["tasks"]

    async def generate(msgs):
        start, first, parts = time.perf_counter(), None, []
        async with asyncio.timeout(config.EXPLAIN_DEADLINE_S):
            async for tok in llm.astream_text(config.EXPLAIN_TASK, msgs):
                first = first if first is not None else time.perf_counter() - start
                parts.append(tok)
        text = "".join(parts).strip()
        if not text:
            raise LLMError("empty explanation")
        return text, round(first, 2), round(time.perf_counter() - start, 2)

    async def judge(task, payload, text):
        last = None
        for _ in range(JUDGE_ATTEMPTS):
            try:
                return (await llm.achat_json(task, judge_messages(payload, text), Verdict)).model_dump(), None
            except LLMError as e:
                last = str(e)
        return None, last

    async def run():
        rows = []
        for c in load_explain_cases():
            msgs = explain_messages(c["item"], c["profile"], c["score"], c["prediction"], c["violation"],
                                    tag_to_cat=tag_to_cat, tag_ko=tag_ko)
            payload = json.loads(msgs[1]["content"])
            raw, guard = None, None
            try:
                raw, first, total = await generate(msgs)
                text, guard = finalize_explanation(raw, c["item"], c["profile"], payload, c["violation"])
                if not text:                          # same as the app: a rejected text shows the template
                    raise LLMError(f"guard: {guard}")
                fallback, error = False, None
            except (LLMError, httpx.HTTPError, TimeoutError) as e:    # same failures the app turns into its template
                text = template_explanation(c["item"], c["profile"], c["score"], tag_ko, c["violation"])
                if not raw:
                    first = total = None
                fallback, error = True, f"{type(e).__name__}: {e}"
            rules = check_explanation(text, payload, round(c["score"] * 100), c["violation"])
            raw_rules = check_explanation(raw, payload, round(c["score"] * 100), c["violation"]) if raw else None
            judges, judge_errors = {}, {}
            for j in JUDGES:
                judges[j], err = await judge(EXPLAIN_JUDGE_TASKS[j], payload, text)
                if err:
                    judge_errors[j] = err
            rows.append({"id": c["id"], "persona": c["persona"], "source": c["item"].source,
                         "score": round(c["score"] * 100), "violation": c["violation"], "fallback": fallback,
                         "error": error, "first_token_s": first, "total_s": total, "text": text, "raw_text": raw,
                         "guard": guard, "raw_rules": raw_rules, "rules": rules,
                         "rule_pass": all(rules.values()), "judges": judges, "judge_errors": judge_errors or None})
            brief = [None if v is None else (v["contradiction"], v["hallucination"], v["helpful"])
                     for v in judges.values()]
            print(f"  {c['id']}: fallback={fallback} guard={guard} rules={all(rules.values())} judges={brief}",
                  flush=True)
        return rows

    rows = asyncio.run(run())
    return {"models": {config.EXPLAIN_TASK: tasks[config.EXPLAIN_TASK]["model"],
                       **{j: tasks[t]["model"] for j, t in EXPLAIN_JUDGE_TASKS.items()}},
            "deadline_s": config.EXPLAIN_DEADLINE_S, "cases_file": "data/eval/explain_cases.yaml",
            "summary": summarize_explain_quality(rows), "cases": rows}


def main(argv: list[str]) -> int:
    from app.core.attrmodel import AttrModel
    from app.core.tagmodel import TagModel
    from app.repo import Repo
    names = argv or ["all"]
    order = ["violations", "loo", "loo_open", "coverage", "coverage_open", "compare3", "loo_repro", "convergence",
             "bench"]
    targets = order if names == ["all"] else names
    offline = {"explain_recheck"}                     # no DB, no LLM
    repo = None if set(targets) <= offline else Repo(settings.DATABASE_URL)
    # adds a `tags_model`/`attrs_model` block to plain `loo` only. Both loaders are DATA_VARIANT-aware
    # (app/core/tagmodel.py, app/core/attrmodel.py): under DATA_VARIANT=open they look for the *_open.json
    # files (tag model only if one has been shipped -- docs/adr/0009-learned-attribute-model.md Goal B2) and
    # never fall back to the coffeereview_kaggle-derived full tag model, so running this against coffee_open
    # with DATA_VARIANT=open reports the open models. `loo_open` -- the full-DB, excluded-neighbour-pool
    # experiment -- doesn't score either model block; it predates both and answers a different question.
    tag_model = TagModel.load() if repo is not None else None
    attr_model = AttrModel.load() if repo is not None else None
    outputs = {"explain_recheck": "explain_quality"}  # the re-score rewrites the saved explain_quality result
    fns = {
        "violations": violation_rate,
        "loo": lambda r: loo_accuracy(r, tag_model=tag_model, attr_model=attr_model),
        "loo_open": lambda r: loo_accuracy(r, exclude_sources=OPEN_LICENSE_EXCLUDE),
        "coverage": coverage,
        "coverage_open": lambda r: coverage(r, exclude_sources=OPEN_LICENSE_EXCLUDE),
        "tag_names": tag_names,                      # Fix B check; run by name only, not in `all`
        "compare3": compare3,
        "loo_repro": loo_repro,
        "convergence": convergence,
        "bench": bench,
        "explain_quality": explain_quality,          # real LLM calls; run by name only, not in `all`
        "explain_recheck": explain_recheck,          # offline re-score of the saved explain_quality texts
    }
    try:
        for name in targets:
            result = fns[name](repo)
            out = settings.EVAL_DIR / f"phase2_{outputs.get(name, name)}.json"
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[{name}] {json.dumps(result, ensure_ascii=False)[:400]}")
    finally:
        if repo is not None:
            repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
