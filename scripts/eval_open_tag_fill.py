"""Open variant: why so few flavor tags on analyzed beans, and which licence-clean fix shows more of them without losing
precision? docs/adr/0017-open-tag-fill.md.

Hypothesis: 1,546 of the 2,063 coffee_open beans are CQI rows with NO flavor tags, so the 10 embedding neighbours the
tag vote reads are mostly untagged and the vote (share >= 0.3 of k, lift gate) rarely clears -- worst when the guest
typed a note word or two: then the note-free tag model (ADR 0016) steps aside and only the diluted vote fills.

Two input situations, both scored on the SAME leak-free inputs as ADR 0016 (never the bean's own stored embedding):
  free   the note-free card "name origin process roast decaf" (what the tag predictor sees when the guest typed no
         note word). Truth = the bean's tags. Current path: note-free feature tag model, else the vote.
  notes  the same card + the bean's FIRST note word (a guest who typed one note). Text cues echo that word (the guest's
         own, always shown first); the predictor's job is the REST. Truth = the bean's tags minus the echoed ones,
         scored on the filled tags only (echoes excluded -- that would be circular). Current path: echo + vote.
Candidates for the fill (all licence-clean: no coffeereview-derived label, Zenodo never trained on):
  vote_all          current vote over the k=10 nearest of ALL beans (CQI included)
  vote_tagged       the same vote over the k nearest beans that HAVE flavor tags (pool: every tagged coffee_open bean)
  vote_tagged_clean the same, pool without roasterdb (CC BY-NC -- the portal upload leaves it out)
  feat              the shipped note-free logistic model (ADR 0016), refit per fold
  feat_pertag       feat with per-tag thresholds (inner grouped CV, per-tag F1)
  emb / emb_mlp     logistic / small MLP on the note-free query embedding (the "open tag model" route of ADR 0008)
  combos            "a+b" = a's tags, then b's to fill up to MAX_TAGS; "a|b" = a, or b when a shows nothing
  gates / caps (d)  vote_tagged@s{share floor}l{lift} (the vote's gate), vote_tagged^N (at most N vote tags), and
                    "vote_all+vote_tagged_clean^2" = the plain vote topped up to 2 tags from the tagged vote
  shipped           the runtime policy after ADR 0017 (SHIPPED_FILL; free: the tag model, else the filled vote)
E1: grouped leave-one-roaster-out over the licence-clean tagged beans (roasters_kr + shopify + shopify_gauged); the
neighbour pools always exclude the target's own roaster. E2: the Zenodo Q-grader panel (evaluation only, ADR 0014):
free = the card without its notes (as ADR 0016), notes = the card with its FIRST panel note word.
Metrics: micro tag precision/recall/F1, SCA category F1 (scripts/eval_zenodo_panel.py tag_metrics), tags shown per
bean (after the echo, capped at MAX_TAGS) and the share of beans showing >= 1 tag.

Writes data/eval/open/phase6_open_tag_fill.json only.

    uv run python scripts/eval_open_tag_fill.py
"""
import hashlib
import json
import sys
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.predict import FILL_MIN_TAGS, LIFT, MAX_TAGS, TAG_SHARE, fill_tags, predict_from_neighbors  # noqa: E402
from app.core.textcues import text_tags  # noqa: E402
from app.models import Neighbor  # noqa: E402
from app.repo import MIN_FILTERED_NEIGHBORS  # noqa: E402
from pipeline import settings  # noqa: E402
from pipeline.enrich import rule_tags  # noqa: E402
from scripts.eval_open_tags import CACHE, INNER_FOLDS, THRESHOLDS, X_of, TagLR, load, micro_f1, pick_threshold  # noqa: E402
from scripts.eval_zenodo_panel import (  # noqa: E402
    EMB_CACHE as ZENODO_CACHE, XLSX, CachedEmbedder, download, read_xlsx_rows, samples_from_rows, tag_metrics,
)
from scripts.train_feature_model import OPEN_URL  # noqa: E402

OUT = settings.DATA_DIR / "eval" / "open" / "phase6_open_tag_fill.json"
NOTES_CACHE = settings.RAW_DIR / "open_tag_eval" / "note_query_embeddings.jsonl"
K = 10
R = 4
# the runtime policy (app/graphs/analyze_bean.py + app/core/predict.py with_tag_fill): the plain vote, topped up to
# FILL_MIN_TAGS from the licence-clean tagged-only vote; the note-free tag model still replaces it when it fires
SHIPPED_FILL = "vote_all+vote_tagged_clean^2"
POOLS = {"vote_all": "", "vote_tagged": " AND cardinality(flavor_tags) > 0",
         "vote_tagged_clean": " AND cardinality(flavor_tags) > 0 AND source <> 'roasterdb'"}


def neighbours(conn, vec, origin, process, pool: str, k: int = K, exclude_roaster: str | None = None,
               exclude_id: int | None = None) -> list[Neighbor]:
    """app/repo.py Repo.neighbors (origin/process pre-filter, fallback to unfiltered below MIN_FILTERED_NEIGHBORS) with
    a pool filter, plus: never the target itself nor its own roaster (E1)."""
    v = "[" + ",".join(f"{x:.7g}" for x in vec) + "]"

    def run(extra: str):
        with conn.transaction():
            conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            conn.execute("SET LOCAL hnsw.ef_search = 200")
            rows = conn.execute(
                "SELECT id, name, acidity, body, sweetness, flavor_tags, 1 - (embedding <=> %(v)s::vector) AS sim"
                " FROM coffees WHERE active AND embedding IS NOT NULL AND id IS DISTINCT FROM %(ex)s"
                " AND roaster IS DISTINCT FROM %(r)s" + pool + extra +
                " ORDER BY embedding <=> %(v)s::vector, id LIMIT %(k)s",
                {"v": v, "ex": exclude_id, "r": exclude_roaster, "k": k, "o": origin, "p": process}).fetchall()
        return [Neighbor(r["id"], r["name"], float(r["sim"]), r["acidity"], r["body"], r["sweetness"],
                         tuple(r["flavor_tags"] or ())) for r in sorted(rows, key=lambda r: (-r["sim"], r["id"]))]
    if origin or process:
        hits = run((" AND origin_country = %(o)s" if origin else "") + (" AND process = %(p)s" if process else ""))
        if len(hits) >= MIN_FILTERED_NEIGHBORS:
            return hits
    return run("")


def vote(near, base_rates, tag_share: float = TAG_SHARE, lift: float = LIFT) -> list[str]:
    return [t.lower() for t in predict_from_neighbors(near, base_rates=base_rates, tag_share=tag_share, lift=lift).tags]


GRID = [(s, lift) for s in (0.3, 0.4, 0.5) for lift in (1.2, 2.0)]


def add_votes(preds: dict, nb: dict[str, list], base_rates) -> None:
    """Default votes per pool, plus the gate grid (share floor x lift, candidate (d)) for the tagged pools."""
    for pool, lists in nb.items():
        preds[pool] = [vote(near, base_rates) for near in lists]
        if pool != "vote_all":
            for share, lift in GRID:
                if (share, lift) != (TAG_SHARE, LIFT):
                    preds[f"{pool}@s{share}l{lift}"] = [vote(near, base_rates, share, lift) for near in lists]


# ---- tag models ---------------------------------------------------------------------------------------------------
class PerTagLR(TagLR):
    """TagLR with one threshold per tag (inner grouped CV, the threshold maximising that tag's F1; a tag whose best F1
    is 0 keeps the global threshold)."""

    def fit_thresholds(self, X, truths, groups, fallback: float):
        from sklearn.model_selection import GroupKFold
        P = np.zeros((len(X), len(self.tags)))
        for tr, te in GroupKFold(n_splits=min(INNER_FOLDS, len(set(groups)))).split(X, groups=groups):
            m = TagLR(X[tr], [truths[i] for i in tr])
            Pm = m.proba(X[te])
            for j, t in enumerate(self.tags):
                if t in m.tags:
                    P[te, j] = Pm[:, m.tags.index(t)]
        self.thr = []
        for j, t in enumerate(self.tags):
            y = np.array([t in s for s in truths])
            best = (0.0, fallback)
            for thr in THRESHOLDS:
                pred = P[:, j] >= thr
                tp = int((pred & y).sum())
                f = 2 * tp / (pred.sum() + y.sum()) if tp else 0.0
                if f > best[0]:
                    best = (f, thr)
            self.thr.append(best[1])
        return self

    def decide_pertag(self, P) -> list[list[str]]:
        out = []
        for p in P:
            order = [i for i in np.argsort(-p) if p[i] >= self.thr[i]][:MAX_TAGS]
            out.append([self.tags[i] for i in order])
        return out


class TagMLP:
    """sklearn MLPClassifier (one hidden layer) over the note-free embedding, tags with >= 5 positives."""

    def __init__(self, X, truths):
        from sklearn.neural_network import MLPClassifier
        counts = Counter(t for s in truths for t in s)
        self.tags = sorted(t for t, n in counts.items() if n >= 5)
        Y = np.array([[t in s for t in self.tags] for s in truths], int)
        self.m = MLPClassifier(hidden_layer_sizes=(64,), alpha=1e-2, solver="lbfgs", max_iter=300, random_state=0).fit(X, Y)

    def proba(self, X):
        return self.m.predict_proba(X)

    decide = TagLR.decide


def ordered(m, P, thr) -> list[list[str]]:
    """Like TagLR.decide but keeps the probability order (tags shown first matter when capping at MAX_TAGS)."""
    out = []
    for p in P:
        out.append([m.tags[i] for i in np.argsort(-p) if p[i] >= thr][:MAX_TAGS])
    return out


def pick_mlp_threshold(X, truths, groups) -> float:
    from sklearn.model_selection import GroupKFold
    folds = []
    for tr, te in GroupKFold(n_splits=min(INNER_FOLDS, len(set(groups)))).split(X, groups=groups):
        m = TagMLP(X[tr], [truths[i] for i in tr])
        folds.append((m, m.proba(X[te]), [truths[i] for i in te]))
    return max(THRESHOLDS, key=lambda thr: micro_f1([(t, set(p)) for m, P, ts in folds
                                                     for t, p in zip(ts, ordered(m, P, thr))]))


def fit_models(X_feat, X_emb, truths, groups):
    """-> {name: predict(X_feat_rows, X_emb_rows) -> list[list[str]]} fitted on the given training rows."""
    thr_f = pick_threshold(X_feat, truths, groups)
    mf = TagLR(X_feat, truths)
    mp = PerTagLR(X_feat, truths).fit_thresholds(X_feat, truths, groups, thr_f)
    thr_e = pick_threshold(X_emb, truths, groups)
    me = TagLR(X_emb, truths)
    thr_m = pick_mlp_threshold(X_emb, truths, groups)
    mm = TagMLP(X_emb, truths)
    return {"feat": lambda F, E: ordered(mf, mf.proba(F), thr_f),
            "feat_pertag": lambda F, E: mp.decide_pertag(mp.proba(F)),
            "emb": lambda F, E: ordered(me, me.proba(E), thr_e),
            "emb_mlp": lambda F, E: ordered(mm, mm.proba(E), thr_m)}, {"feat": thr_f, "emb": thr_e, "emb_mlp": thr_m}


def combine(preds: dict[str, list], echo: list[set] | None = None) -> None:
    """Add the combo candidates in place ("a+b" fill, "a|b" fallback, "current")."""
    n = len(next(iter(preds.values())))
    pairs = [("feat", "vote_tagged"), ("feat", "vote_tagged_clean"), ("vote_tagged", "feat"),
             ("vote_tagged_clean", "feat")]
    for a, b in pairs:
        preds[f"{a}+{b}"] = [list(dict.fromkeys([*preds[a][i], *preds[b][i]]))[:MAX_TAGS] for i in range(n)]
        preds[f"{a}|{b}"] = [preds[a][i] or preds[b][i] for i in range(n)]
    # (d) fill caps: at most N tags from the vote (its own rank order: share x log(1/base rate))
    for base in ("vote_tagged", "vote_tagged_clean"):
        for cap in (1, 2, 3):
            preds[f"{base}^{cap}"] = [p[:cap] for p in preds[base]]
        preds[f"vote_all+{base}^2"] = [fill_tags(a, b) for a, b in zip(preds["vote_all"], preds[base])]


def score(truths: list[set], preds: dict[str, list], tag_to_cat, echo: list[set] | None = None) -> dict:
    """tag_metrics on the filled tags (minus echoes) plus tags shown per bean / share with >= 1 tag, where the shown
    list is the echo first, then the fill, capped at MAX_TAGS (app/core/predict.py with_text_cues)."""
    echo = echo or [set() for _ in truths]
    out = {}
    for k, v in preds.items():
        shown = [list(dict.fromkeys([*sorted(e), *p]))[:MAX_TAGS] for e, p in zip(echo, v)]
        fill = [set(s) - e for s, e in zip(shown, echo)]
        m = tag_metrics(list(zip(truths, fill)), tag_to_cat)
        m["tags_shown"] = round(sum(len(s) for s in shown) / len(shown), 3)
        m["share_tagged"] = round(sum(1 for s in shown if s) / len(shown), 4)
        m["fill_per_bean"] = round(sum(len(f) for f in fill) / len(fill), 3)
        out[k] = m
    return out


# ---- E1 -----------------------------------------------------------------------------------------------------------
def first_note(summary: str | None) -> str | None:
    notes = [n.strip() for n in (summary or "").replace(";", ",").split(",") if n.strip()]
    return notes[0].split(">")[-1].strip() if notes else None


def e1(conn, rows, note_embed, base_rates, tag_to_cat, tag_ko) -> dict:
    groups = np.array([r["roaster"] for r in rows])
    truths = [r["truth"] for r in rows]
    Xf, Xe = X_of(rows, "feat"), X_of(rows, "emb")
    model_preds = {k: [None] * len(rows) for k in ("feat", "feat_pertag", "emb", "emb_mlp")}
    thresholds = {}
    for g in sorted(set(groups)):
        tr, te = np.nonzero(groups != g)[0], np.nonzero(groups == g)[0]
        fns, thr = fit_models(Xf[tr], Xe[tr], [truths[i] for i in tr], groups[tr])
        thresholds[g] = thr
        for k, fn in fns.items():
            for i, p in zip(te, fn(Xf[te], Xe[te])):
                model_preds[k][i] = p

    # free: the note-free query
    free = dict(model_preds)
    add_votes(free, {name: [neighbours(conn, r["vec"], r["origin_country"], r["process"], pool,
                                       exclude_roaster=r["roaster"], exclude_id=r["id"]) for r in rows]
                     for name, pool in POOLS.items()}, base_rates)
    for k in (5, 20):
        free[f"vote_tagged_k{k}"] = [vote(neighbours(conn, r["vec"], r["origin_country"], r["process"],
                                                     POOLS["vote_tagged"], k=k, exclude_roaster=r["roaster"],
                                                     exclude_id=r["id"]), base_rates) for r in rows]
    combine(free)
    free["current"] = free["feat|vote_all"] = [f or v for f, v in zip(free["feat"], free["vote_all"])]
    free["shipped"] = [f or v for f, v in zip(free["feat"], free[SHIPPED_FILL])]
    r_free = score(truths, free, tag_to_cat)

    # notes: card + first note word
    from app.core.parse import parse_bean_text
    idx, texts, echoes = [], [], []
    for i, r in enumerate(rows):
        note = first_note(r["flavor_summary"])
        if not note:
            continue
        text = f"{r['text'].replace(' | ', ' ')} {note}"
        echo = {t.lower() for t in text_tags(text, tag_to_cat, tag_ko, free_text=True)}
        if echo and truths[i] - echo:
            idx.append(i)
            texts.append(text)
            echoes.append(echo)
    notes = {k: [model_preds[k][i] for i in idx] for k in model_preds}
    vecs = [note_embed(t) for t in texts]
    parsed = [parse_bean_text(t) for t in texts]
    add_votes(notes, {name: [neighbours(conn, v, p.origin_country, p.process, pool, exclude_roaster=rows[i]["roaster"],
                                        exclude_id=rows[i]["id"]) for i, p, v in zip(idx, parsed, vecs)]
                      for name, pool in POOLS.items()}, base_rates)
    combine(notes)
    notes["current"] = notes["vote_all"]
    notes["shipped"] = notes[SHIPPED_FILL]
    r_notes = score([truths[i] - e for i, e in zip(idx, echoes)], notes, tag_to_cat, echoes)
    per_roaster = {}
    for g in sorted(set(groups)):
        sel = [j for j, i in enumerate(range(len(rows))) if groups[i] == g]
        per_roaster[g] = {k: tag_metrics([(truths[j], set(free[k][j])) for j in sel], tag_to_cat)["f1"]
                          for k in ("current", "feat+vote_tagged", "vote_tagged")}
    return {"n": len(rows), "roasters": dict(Counter(groups.tolist())), "free": r_free,
            "notes": {"n": len(idx), "example": texts[0] if texts else None, "table": r_notes},
            "per_roaster_f1_free": per_roaster,
            "thresholds": {g: t for g, t in thresholds.items()}}


# ---- E2 -----------------------------------------------------------------------------------------------------------
def e2(conn, rows, free_embed, notes_embed, base_rates, tag_to_cat, tag_ko) -> dict:
    from app.core.parse import parse_bean_text
    from scripts.eval_open_tags import feat_vec
    samples = samples_from_rows(read_xlsx_rows(download(XLSX)))
    vocab = list(tag_to_cat)
    truths = [{t.lower() for t in rule_tags(s["tag_text"], vocab, limit=12)} for s in samples]
    Xf, Xe = X_of(rows, "feat"), X_of(rows, "emb")
    ttr = [r["truth"] for r in rows]
    fns, thr = fit_models(Xf, Xe, ttr, np.array([r["roaster"] for r in rows]))

    def run(texts, embed, echoes):
        parsed = [parse_bean_text(t) for t in texts]
        vecs = [embed(t) for t in texts]
        F = np.array([feat_vec(p.origin_country, p.process, p.roast_level, p.is_decaf, p.decaf_process, None, None,
                               p.text) for p in parsed], float)
        E = np.array(vecs, float)
        preds = {k: fn(F, E) for k, fn in fns.items()}
        add_votes(preds, {name: [neighbours(conn, v, p.origin_country, p.process, pool) for p, v in zip(parsed, vecs)]
                          for name, pool in POOLS.items()}, base_rates)
        combine(preds)
        return preds

    # free: the card without its "notes: ..." part (ADR 0016's E2)
    keep = []
    free_texts = []
    for i, s in enumerate(samples):
        t = " | ".join(p for p in s["text"].split(" | ") if not p.startswith("notes:"))
        if truths[i] and t.strip():
            keep.append(i)
            free_texts.append(t)
    free = run(free_texts, free_embed, None)
    free["current"] = [f or v for f, v in zip(free["feat"], free["vote_all"])]
    free["shipped"] = [f or v for f, v in zip(free["feat"], free[SHIPPED_FILL])]
    r_free = score([truths[i] for i in keep], free, tag_to_cat)

    # notes: the card with its first panel note word
    idx, texts, echoes = [], [], []
    for i, s in enumerate(samples):
        parts = s["text"].split(" | ")
        base = " | ".join(p for p in parts if not p.startswith("notes:"))
        notes = next((p[len("notes: "):] for p in parts if p.startswith("notes:")), "")
        note = notes.split(",")[0].strip() if notes else ""
        if not note or not truths[i]:
            continue
        text = f"{base} | {note}" if base else note
        echo = {t.lower() for t in text_tags(text, tag_to_cat, tag_ko, free_text=True)}
        if echo and truths[i] - echo:
            idx.append(i)
            texts.append(text)
            echoes.append(echo)
    notes = run(texts, notes_embed, echoes)
    notes["current"] = notes["vote_all"]
    notes["shipped"] = notes[SHIPPED_FILL]
    r_notes = score([truths[i] - e for i, e in zip(idx, echoes)], notes, tag_to_cat, echoes)
    return {"free": {"n": len(keep), "table": r_free}, "notes": {"n": len(idx), "example": texts[0], "table": r_notes},
            "thresholds": thr}


def main() -> int:
    warnings.filterwarnings("ignore", module="sklearn")
    from app.repo import Repo
    free_embed = CachedEmbedder(CACHE)
    notes_embed = CachedEmbedder(NOTES_CACHE)
    zen_embed = CachedEmbedder(ZENODO_CACHE)
    repo = Repo(OPEN_URL)
    try:
        tag_to_cat, tag_ko = repo.taxonomy()
        base_rates = repo.tag_base_rates()
    finally:
        repo.close()
    with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
        rows = load(conn, free_embed)
        summaries = {r["id"]: r["flavor_summary"] for r in conn.execute(
            "SELECT id, flavor_summary FROM coffees WHERE id = ANY(%s)", ([r["id"] for r in rows],)).fetchall()}
        for r in rows:
            r["flavor_summary"] = summaries[r["id"]]
        r1 = e1(conn, rows, notes_embed, base_rates, tag_to_cat, tag_ko)
        r2 = e2(conn, rows, free_embed, zen_embed, base_rates, tag_to_cat, tag_ko)
    keys = ("precision", "recall", "f1", "category_f1", "tags_shown", "share_tagged")
    summary = {name: {v: {c: tab[v][c] for c in keys} for v in ("current", "shipped")}
               for name, tab in (("e1_free", r1["free"]), ("e1_notes", r1["notes"]["table"]),
                                 ("e2_free", r2["free"]["table"]), ("e2_notes", r2["notes"]["table"]))}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "k": K, "max_tags": MAX_TAGS,
              "shipped_fill": SHIPPED_FILL, "fill_min_tags": FILL_MIN_TAGS, "summary": summary,
              "pools": POOLS, "e1": r1, "e2": r2,
              "embedding_requests": free_embed.requests + notes_embed.requests + zen_embed.requests,
              "note_cache_sha1": hashlib.sha1(NOTES_CACHE.read_bytes()).hexdigest()[:12] if NOTES_CACHE.exists()
              else None}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    cols = ("precision", "recall", "f1", "category_f1", "tags_shown", "share_tagged")
    for name, tab in (("E1 free", r1["free"]), ("E1 notes", r1["notes"]["table"]), ("E2 free", r2["free"]["table"]),
                      ("E2 notes", r2["notes"]["table"])):
        print(f"== {name}")
        for k, m in sorted(tab.items(), key=lambda kv: -kv[1]["f1"]):
            if "@" in k and "+" not in k and "|" not in k and not k.startswith("vote_tagged@"):
                continue
            print(f"  {k:32s} " + " ".join(f"{m[c]:.3f}" for c in cols))
    print(f"wrote {OUT} ({report['embedding_requests']} embedding requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
