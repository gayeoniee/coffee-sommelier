"""Phase 2 evaluation: hard-constraint violations, leave-one-out prediction accuracy, learning convergence,
and latency benchmarks for the ADRs. Results go to data/eval/phase2_<name>.json."""
import asyncio
import json
import sys
import time

import yaml

from app.core.explain import explain_messages
from app.core.predict import predict_from_neighbors
from app.core.scoring import mmr_top_k, passes, score_item
from app.core.simulate import simulate_convergence
from app.models import ATTRS, Item, Profile
from pipeline import settings

OPEN_LICENSE_EXCLUDE = ("coffeereview_kaggle",)   # licence-restricted source; excluded for the "open" comparison

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
    if profile.caffeine_rule == "low" and not (decaf_capable or (caffeine is not None and caffeine <= 100)):
        return False
    return profile.milk_ok or not has_milk(name)


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
    return {"checked": checked, "violations": violations, "rate": violations / checked if checked else 0.0,
            "details": details}


def loo_accuracy(repo, n: int = 200, seed: int = 42, exclude_sources: tuple[str, ...] = ()) -> dict:
    stats = {a: {"n": 0, "exact": 0, "within1": 0} for a in ATTRS}
    by_conf: dict[str, dict] = {}
    for cid in repo.random_coffee_ids_for_loo(n, seed, exclude_sources=exclude_sources):
        truth = repo.get_coffee(cid)
        pred = predict_from_neighbors(
            repo.neighbors(repo.coffee_embedding(cid), k=10, exclude_id=cid, exclude_sources=exclude_sources))
        for a in ATTRS:
            t, v = truth.attr(a), getattr(pred, a)
            if t is None or v is None:
                continue
            s = stats[a]
            s["n"] += 1
            s["exact"] += round(v) == t
            s["within1"] += abs(v - t) <= 1
        if truth.acidity is not None and pred.acidity is not None:
            c = by_conf.setdefault(pred.confidence, {"n": 0, "within1": 0})
            c["n"] += 1
            c["within1"] += abs(pred.acidity - truth.acidity) <= 1
    rate = lambda d, k: round(d[k] / d["n"], 4) if d["n"] else None  # noqa: E731
    return {"n": n, "seed": seed, "exclude_sources": list(exclude_sources),
            "embedding_model": settings.load_config("models.yaml")["tasks"]["embed"]["model"],
            **{a: {"n": s["n"], "exact": rate(s, "exact"), "within1": rate(s, "within1")} for a, s in stats.items()},
            "acidity_within1_by_confidence": {k: {"n": v["n"], "within1": rate(v, "within1")} for k, v in by_conf.items()}}


def coverage(repo, exclude_sources: tuple[str, ...] = ()) -> dict:
    return {"exclude_sources": list(exclude_sources), **repo.coverage_counts(exclude_sources=exclude_sources)}


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

    seq, sequential, par, parallel = asyncio.run(run())
    return {"model": settings.load_config("models.yaml")["tasks"][EXPLAIN_TASK]["model"],
            "sequential_total_s": round(sequential, 2), "parallel_total_s": round(parallel, 2),
            "first_token_s": [round(f, 2) for f, _ in seq],
            "full_answer_s": [round(t, 2) for _, t in seq]}


def main(argv: list[str]) -> int:
    from app.repo import Repo
    names = argv or ["all"]
    order = ["violations", "loo", "loo_open", "coverage", "coverage_open", "convergence", "bench"]
    targets = order if names == ["all"] else names
    repo = Repo(settings.DATABASE_URL)
    fns = {
        "violations": violation_rate,
        "loo": loo_accuracy,
        "loo_open": lambda r: loo_accuracy(r, exclude_sources=OPEN_LICENSE_EXCLUDE),
        "coverage": coverage,
        "coverage_open": lambda r: coverage(r, exclude_sources=OPEN_LICENSE_EXCLUDE),
        "convergence": convergence,
        "bench": bench,
    }
    try:
        for name in targets:
            result = fns[name](repo)
            out = settings.EVAL_DIR / f"phase2_{name}.json"
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[{name}] {json.dumps(result, ensure_ascii=False)[:400]}")
    finally:
        repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
