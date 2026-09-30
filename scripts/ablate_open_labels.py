"""Open-variant label ablation (docs/adr/0013-open-labels-weak-supervision.md): do more OPEN labels -- (A) Shopify
roasters' own intensity profiles, (B) weak labels from note words, (C) per-roaster bias calibration -- improve the
interpretable feature model of ADR 0011?

Evaluation is exactly ADR 0011's: the 82 Korean roaster-gauge beans, grouped leave-one-roaster-out (4 folds).
Extra labels only ever join the TRAINING side and never come from the held-out roaster (weak-labelled
roasters_kr beans of the held-out roaster are dropped from that fold). Every config runs on the same folds and
targets; the recipe per attribute is the currently shipped one (acidity/body: ridge + neighbour-average feature,
sweetness: ridge without it), alpha picked by an inner leave-one-roaster-out scored on gauge rows only.

  base   gauges only, neighbour pool without the new source = the shipped model's recorded CV (ADR 0011)
  +A     + shopify_gauged rows (weight W_A; their altitude masked -- see training_rows)
  +B     + weak labels (app/core/weaklabels.py) on every other open bean with notes (weight W_B), re-centred
         per fold on the training gauges' mean
  +Bf    the weak label as one extra feature ("weak" = weak label - 3, 0 when absent) instead of extra rows
  +C     per-roaster offsets: labels de-biased by a shrunken per-roaster mean residual (random-intercept style,
         offset = sum(resid) / (n + SHRINK)), 5 alternations; an unseen roaster gets offset 0
  +A+B, +A+C, +B+C, +A+B+C (all)
  Every "+" config uses the neighbour pool WITH shopify_gauged loaded (production). "~" rows are diagnostics:
  the old/new pool, A with altitude kept, B without re-centring, W_A 0.25/1, W_B 0.15/0.6, and "~B ext"
  (weak labels only from roasters with no gauge bean at all -- the stricter generalisation check).
Baselines on the same folds: global/per-origin mean and the open neighbour average (old and new pool).

Before B is used at all, the weak labels' agreement with the 82 gauges is measured (within+-1, MAE, Spearman)
against the per-origin-mean baseline on the same beans: B is only a candidate for an attribute if its labels, as
training uses them (re-centred), beat that baseline on +-1. Restricting the evaluation to roasters that are not
A sources changes nothing: no A store is one of the four gauge roasters (all 82 beans qualify).

Ship rule (per attribute), REPORTED only: a config would replace the shipped spec if its within+-1 beats the
shipped model (base) by >= 0.03; body (not shipped) needs >= 0.05 over the neighbour baseline. This script writes
only data/eval/open/phase4_open_labels.json -- never config/. The shipped config is written by
scripts/train_feature_model.py from its SHIPPED_RECIPES (which refits a winning config here through final_spec);
adopting a new winner means editing SHIPPED_RECIPES and re-running that script.

    uv run python scripts/ablate_open_labels.py
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row
from sklearn.linear_model import Ridge

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.featuremodel import FEATURES, bean_features  # noqa: E402
from app.core.predict import predict_from_neighbors  # noqa: E402
from app.core.textcues import text_tags  # noqa: E402
from app.core.weaklabels import tag_paths, weak_labels  # noqa: E402
from app.models import ATTRS, Neighbor  # noqa: E402
from app.repo import MIN_FILTERED_NEIGHBORS  # noqa: E402
from pipeline import settings  # noqa: E402
from pipeline.collect import latest_snapshot  # noqa: E402
from pipeline.normalize.shopify_gauged import iter_products  # noqa: E402
from scripts.train_feature_model import (  # noqa: E402
    BEANS, K, OPEN_URL, R, SHIPPED_RECIPES, score, spearman, taxonomy,
)

ALPHAS = (0.3, 1.0, 3.0, 10.0, 30.0)
W_A = 0.5            # a Shopify profile label counts half a gauge label (coarser, different convention)
W_B = 0.3            # a weak (lexicon) label counts less than a third
SHRINK = 5.0         # per-roaster offset shrinkage: n labels -> weight n / (n + 5)
SHIP_MARGIN_SHIPPED = 0.03
SHIP_MARGIN_NEIGHBOR = 0.05
USE_NBR = {"acidity": True, "body": True, "sweetness": False}   # the shipped / candidate recipe per attribute
NEW_SOURCE = "shopify_gauged"
OUT = settings.DATA_DIR / "eval" / "open" / "phase4_open_labels.json"


def neighbours(conn, emb, cid, roaster, origin, process, exclude_source: str | None) -> list[Neighbor]:
    """train_feature_model.neighbours (app/repo.py Repo.neighbors minus the target's own roaster), optionally
    without one source in the pool (the old DB, before shopify_gauged was loaded)."""
    xs = " AND source <> %(xs)s" if exclude_source else ""

    def run(extra: str):
        with conn.transaction():
            conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            conn.execute("SET LOCAL hnsw.ef_search = 200")
            rows = conn.execute(
                "SELECT id, name, acidity, body, sweetness, flavor_tags, 1 - (embedding <=> %(v)s::vector) AS sim"
                " FROM coffees WHERE active AND embedding IS NOT NULL AND id <> %(ex)s"
                " AND roaster IS DISTINCT FROM %(r)s" + xs + extra +
                " ORDER BY embedding <=> %(v)s::vector, id LIMIT %(k)s",
                {"v": emb, "ex": cid, "r": roaster, "k": K, "o": origin, "p": process, "xs": exclude_source}).fetchall()
        return [Neighbor(r["id"], r["name"], float(r["sim"]), r["acidity"], r["body"], r["sweetness"],
                         tuple(r["flavor_tags"] or ())) for r in sorted(rows, key=lambda r: (-r["sim"], r["id"]))]
    if origin or process:
        hits = run((" AND origin_country = %(o)s" if origin else "") + (" AND process = %(p)s" if process else ""))
        if len(hits) >= MIN_FILTERED_NEIGHBORS:
            return hits
    return run("")


def load_rows(conn) -> list[dict]:
    """Every active open bean that is a gauge target, a shopify_gauged label, or a weak-label candidate."""
    tag_to_cat, tag_ko = taxonomy(conn)
    paths = tag_paths((r["key"], r["level"], r["name_en"])
                      for r in conn.execute("SELECT key, level, name_en FROM flavor_taxonomy").fetchall())
    gauges = {}
    for line in BEANS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            g = {a: r.get(f"gauge_{a}") for a in ATTRS if r.get(f"gauge_{a}") is not None}
            if g:
                gauges[f"roasters_kr:{r['key']}"] = g
    shop = {key: parsed["labels"] for key, _, _, parsed in iter_products(latest_snapshot(settings.RAW_DIR, NEW_SOURCE))}
    rows = conn.execute(
        "SELECT id, key, name, roaster, source, origin_country, process, roast_level, is_decaf, decaf_process,"
        " variety, altitude_m, flavor_summary, embedding::text AS emb FROM coffees"
        " WHERE active AND source IN ('roasters_kr', 'shopify', 'shopify_gauged') ORDER BY key").fetchall()
    out = []
    for r in rows:
        tags = text_tags(r["flavor_summary"] or "", tag_to_cat, tag_ko, limit=8)
        weak = weak_labels(tags, paths, r["flavor_summary"])
        if r["key"] in gauges:
            kind, labels = "gauge", gauges[r["key"]]
        elif r["source"] == NEW_SOURCE and r["key"] in shop:
            kind, labels = "A", shop[r["key"]]
        elif weak:
            kind, labels = "B", {}
        else:
            continue
        nb = {}
        for pool, xs in (("old", NEW_SOURCE), ("new", None)):
            if kind == "A" and pool == "old":
                continue
            pred = predict_from_neighbors(neighbours(conn, r["emb"], r["id"], r["roaster"], r["origin_country"],
                                                     r["process"], xs))
            nb[pool] = {a: getattr(pred, a) for a in ATTRS}
        if kind == "A":
            nb["old"] = nb["new"]     # an A bean's own neighbours: whatever pool it lives in (never itself)
        out.append({**{k: r[k] for k in ("key", "name", "roaster", "source", "origin_country", "process",
                                          "roast_level", "is_decaf", "decaf_process", "variety", "altitude_m",
                                          "flavor_summary")},
                    "kind": kind, "labels": labels, "weak": weak, "nbr": nb,
                    "cats": [tag_to_cat[t] for t in tags if t in tag_to_cat]})
    return out


def feats(row, attr, pool, use_nbr, use_weak) -> dict[str, float]:
    f = bean_features(origin_country=row["origin_country"], process=row["process"], roast_level=row["roast_level"],
                      is_decaf=bool(row["is_decaf"]), decaf_process=row["decaf_process"], variety=row.get("variety"),
                      altitude_m=row.get("altitude_m"), text=row.get("name"), note_categories=row["cats"],
                      neighbor_value=row["nbr"][pool][attr] if use_nbr else None)
    if use_weak and row["weak"].get(attr) is not None:
        f["weak"] = round(row["weak"][attr] - 3.0, 4)
    return f


def names_for(use_nbr, use_weak) -> list[str]:
    return [f for f in FEATURES if use_nbr or f != "nbr"] + (["weak"] if use_weak else [])


def matrix(rows, attr, pool, use_nbr, use_weak):
    names = names_for(use_nbr, use_weak)
    return np.array([[feats(r, attr, pool, use_nbr, use_weak).get(f, 0.0) for f in names] for r in rows]), names


def fit(X, y, w, groups, alpha, offsets: bool):
    """Ridge; with `offsets`, alternate: fit on de-biased labels, re-estimate each roaster's shrunken mean
    residual. Returns (model, {roaster: offset})."""
    off = defaultdict(float)
    m = Ridge(alpha=alpha).fit(X, y, sample_weight=w)
    if not offsets:
        return m, {}
    for _ in range(5):
        resid = y - m.predict(X)
        new = {}
        for g in set(groups):
            sel = groups == g
            new[g] = float((w[sel] * resid[sel]).sum() / (w[sel].sum() + SHRINK))
        off = defaultdict(float, new)     # resid = y - f(x) already contains the roaster's bias
        yb = y - np.array([off[g] for g in groups])
        m = Ridge(alpha=alpha).fit(X, yb, sample_weight=w)
    return m, dict(off)


def pick_alpha(X, y, w, groups, kinds, offsets) -> float:
    """Inner leave-one-roaster-out over the training GAUGE roasters; only gauge rows are scored."""
    gauge_groups = sorted({g for g, k in zip(groups, kinds) if k == "gauge"})
    if len(gauge_groups) < 2:
        return 3.0
    best = None
    for a in ALPHAS:
        err, n = 0.0, 0
        for g in gauge_groups:
            tr, te = groups != g, groups == g
            m, _ = fit(X[tr], y[tr], w[tr], groups[tr], a, offsets)
            err += np.abs(m.predict(X[te]) - y[te]).sum()
            n += te.sum()
        if best is None or err / n < best[1]:
            best = (a, err / n)
    return best[0]


def cfg(a=False, pool="old", b=False, bf=False, c=False, wa=W_A, wb=W_B, b_ext=False, a_alt=False,
        b_center=True) -> dict:
    return dict(a=a, pool=pool, b=b, bf=bf, c=c, wa=wa, wb=wb, b_ext=b_ext, a_alt=a_alt, b_center=b_center)


# "+..." configs are ship candidates; "~..." are diagnostics / sensitivity checks, never shipped. shopify_gauged is
# loaded into coffee_open, so every candidate uses the neighbour pool WITH it ("new") -- what production sees; "base"
# alone keeps the old pool, i.e. it reproduces the shipped model's recorded CV (ADR 0011).
CONFIGS = {
    "base": cfg(),
    "+A": cfg(a=True, pool="new"),
    "+B": cfg(b=True, pool="new"),
    "+Bf": cfg(bf=True, pool="new"),
    "+C": cfg(c=True, pool="new"),
    "+A+B": cfg(a=True, b=True, pool="new"),
    "+A+C": cfg(a=True, c=True, pool="new"),
    "+B+C": cfg(b=True, c=True, pool="new"),
    "+A+B+C": cfg(a=True, b=True, c=True, pool="new"),
    # ADR 0020: the body recipe shipped after the ADR 0019 relabel -- same as the "~A wA=1" sensitivity row, promoted
    # to a named ship candidate so the shipped recipe never points at a diagnostic
    "+A wA=1": cfg(a=True, pool="new", wa=1.0),
    "~base (new pool)": cfg(pool="new"),
    "~B (old pool)": cfg(b=True),
    "~A with altitude": cfg(a=True, pool="new", a_alt=True),
    "~B uncentred": cfg(b=True, pool="new", b_center=False),
    "~A wA=0.25": cfg(a=True, pool="new", wa=0.25),
    "~A wA=1": cfg(a=True, pool="new", wa=1.0),
    "~B wB=0.15": cfg(b=True, pool="new", wb=0.15),
    "~B wB=0.6": cfg(b=True, pool="new", wb=0.6),
    "~B ext": cfg(b=True, pool="new", b_ext=True),
}


def training_rows(rows, attr, held_out, c: dict, gauge_roasters=frozenset()):
    """(row, label, weight) training rows for one fold: the other roasters' gauges, plus the configured extras --
    never a row of the held-out roaster (`b_ext`: weak labels only from roasters with no gauge beans at all).

    A rows lose their altitude unless `a_alt`: whether a page prints altitude is a property of the SHOP (Kiss the
    Hippo prints it on 61/62 beans and labels most "bright"; Volcanica/Intelligentsia never print it and label
    "low"/"comforting"), so with altitude the model learns "altitude stated -> bright" as a source marker.
    Weak labels are re-centred (`b_center`) on this fold's training gauge mean: the lexicon only knows direction,
    not the gauges' level (its mean is 3.3-3.7 where the gauges' is 2.3-3.4)."""
    out = []
    for r in rows:
        if r["kind"] == "gauge" and r["labels"].get(attr) is not None and r["roaster"] != held_out:
            out.append((r, r["labels"][attr], 1.0))
        elif c["a"] and r["kind"] == "A" and r["labels"].get(attr) is not None and r["roaster"] != held_out:
            out.append((r if c["a_alt"] else {**r, "altitude_m": None}, r["labels"][attr], c["wa"]))
        elif (c["b"] and r["kind"] == "B" and r["weak"].get(attr) is not None and r["roaster"] != held_out
              and not (c["b_ext"] and r["roaster"] in gauge_roasters)):
            out.append((r, r["weak"][attr], c["wb"]))
    if c["b"] and c["b_center"]:
        weak = [lab for r, lab, _ in out if r["kind"] == "B"]
        gauge = [lab for r, lab, _ in out if r["kind"] == "gauge"]
        if weak and gauge:
            shift = float(np.mean(weak) - np.mean(gauge))
            out = [(r, lab - shift if r["kind"] == "B" else lab, w) for r, lab, w in out]
    return out


def grouped_cv(rows, attr) -> dict:
    data = [r for r in rows if r["kind"] == "gauge" and r["labels"].get(attr) is not None]
    roasters = sorted({r["roaster"] for r in data})
    y = np.array([r["labels"][attr] for r in data])
    preds = defaultdict(lambda: np.zeros(len(data)))
    for g in roasters:
        te = np.array([r["roaster"] == g for r in data])
        test = [r for r in data if r["roaster"] == g]
        train_g = [r for r in data if r["roaster"] != g]
        gmean = np.mean([r["labels"][attr] for r in train_g])
        by_c = defaultdict(list)
        for r in train_g:
            by_c[r["origin_country"]].append(r["labels"][attr])
        preds["global_mean"][te] = gmean
        preds["origin_mean"][te] = [np.mean(by_c[r["origin_country"]]) if r["origin_country"] and
                                    len(by_c[r["origin_country"]]) >= 2 else gmean for r in test]
        for pool in ("old", "new"):
            preds[f"neighbor_avg_{pool}"][te] = [r["nbr"][pool][attr] if r["nbr"][pool][attr] is not None else gmean
                                                 for r in test]
        for name, c in CONFIGS.items():
            pool, b_feat, offsets = c["pool"], c["bf"], c["c"]
            tr = training_rows(rows, attr, g, c, frozenset(roasters))
            X, _ = matrix([t[0] for t in tr], attr, pool, USE_NBR[attr], b_feat)
            yt = np.array([t[1] for t in tr])
            w = np.array([t[2] for t in tr])
            grp = np.array([t[0]["roaster"] for t in tr])
            kinds = [t[0]["kind"] for t in tr]
            alpha = pick_alpha(X, yt, w, grp, kinds, offsets)
            m, _ = fit(X, yt, w, grp, alpha, offsets)
            Xte, _ = matrix(test, attr, pool, USE_NBR[attr], b_feat)
            preds[name][te] = np.clip(m.predict(Xte), 1, 5)
    per_roaster = {g: {k: score(v[np.array([r["roaster"] == g for r in data])],
                                y[np.array([r["roaster"] == g for r in data])]) for k, v in preds.items()}
                   for g in roasters}
    return {"n": len(data), "roasters": {g: sum(r["roaster"] == g for r in data) for g in roasters},
            "table": {k: score(v, y) for k, v in preds.items()}, "per_roaster": per_roaster}


def weak_agreement(rows) -> dict:
    """Weak labels vs the 82 gauges, next to the grouped per-origin-mean baseline on the same beans -- raw, and
    re-centred the way training uses them (shifted by mean(weak labels of the B pool) - mean(other roasters'
    gauges)). The gate for using B for an attribute: the CENTRED weak labels beat the per-origin mean on +-1."""
    out = {}
    for attr in ATTRS:
        data = [r for r in rows if r["kind"] == "gauge" and r["labels"].get(attr) is not None]
        have = [r for r in data if r["weak"].get(attr) is not None]
        if len(have) < 5:
            out[attr] = {"n": len(have)}
            continue
        y = np.array([r["labels"][attr] for r in have])
        wk = np.array([r["weak"][attr] for r in have])
        om = []
        for r in have:  # per-origin mean of the OTHER roasters' gauges (same grouping as the CV)
            others = [o["labels"][attr] for o in data if o["roaster"] != r["roaster"]]
            same = [o["labels"][attr] for o in data if o["roaster"] != r["roaster"]
                    and o["origin_country"] == r["origin_country"] and r["origin_country"]]
            om.append(np.mean(same) if len(same) >= 2 else np.mean(others))
        om = np.array(om)
        pool_mean = np.mean([r["weak"][attr] for r in rows if r["kind"] == "B" and r["weak"].get(attr) is not None])
        wc = np.array([r["weak"][attr] - (pool_mean - np.mean([o["labels"][attr] for o in data
                                                               if o["roaster"] != r["roaster"]])) for r in have])
        out[attr] = {"n": len(have), "of": len(data), "weak": {**score(wk, y), "spearman": spearman(wk, y)},
                     "weak_centred": {**score(wc, y), "spearman": spearman(wc, y)},
                     "origin_mean": {**score(om, y), "spearman": spearman(om, y)},
                     "beats_origin_mean": bool(score(wc, y)["within1"] > score(om, y)["within1"])}
    return out


def transfer(rows) -> dict:
    """Gauge-only ridge (fit on all 82, old pool) applied to the shopify_gauged labels: do the two label sources
    agree? Per store n / within+-1 / Spearman."""
    out = {}
    for attr in ATTRS:
        tr = training_rows(rows, attr, None, CONFIGS["base"])
        X, _ = matrix([t[0] for t in tr], attr, "old", USE_NBR[attr], False)
        yt = np.array([t[1] for t in tr])
        m = Ridge(alpha=3.0).fit(X, yt)
        a = [r for r in rows if r["kind"] == "A" and r["labels"].get(attr) is not None]
        if len(a) < 5:
            continue
        p = np.clip(m.predict(matrix(a, attr, "new", USE_NBR[attr], False)[0]), 1, 5)
        ya = np.array([r["labels"][attr] for r in a])
        by = {}
        for store in sorted({r["roaster"] for r in a}):
            sel = np.array([r["roaster"] == store for r in a])
            by[store] = {**score(p[sel], ya[sel]),
                         "spearman": spearman(p[sel], ya[sel]) if len(set(ya[sel])) > 1 else None}
        out[attr] = {"all": {**score(p, ya), "spearman": spearman(p, ya)}, "by_store": by}
    return out


def final_spec(rows, attr, name) -> dict:
    c = CONFIGS[name]
    pool, b_feat, offsets = c["pool"], c["bf"], c["c"]
    tr = training_rows(rows, attr, None, c)
    X, names = matrix([t[0] for t in tr], attr, pool, USE_NBR[attr], b_feat)
    y = np.array([t[1] for t in tr])
    w = np.array([t[2] for t in tr])
    grp = np.array([t[0]["roaster"] for t in tr])
    kinds = [t[0]["kind"] for t in tr]
    alpha = pick_alpha(X, y, w, grp, kinds, offsets)
    m, off = fit(X, y, w, grp, alpha, offsets)
    return {"type": "ridge", "alpha": alpha, "intercept": round(float(m.intercept_), R),
            "weights": {n: round(float(c), R) for n, c in zip(names, m.coef_) if abs(c) >= 1e-4},
            "n_train": int(len(y)), "n_gauge": sum(k == "gauge" for k in kinds), "uses_nbr": USE_NBR[attr],
            "config": name, **({"roaster_offsets": {g: round(v, R) for g, v in sorted(off.items())}} if off else {})}


def main() -> int:
    argparse.ArgumentParser(description="ADR 0013 open-label ablation; writes data/eval only").parse_args()
    with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
        rows = load_rows(conn)
    counts = defaultdict(int)
    for r in rows:
        counts[r["kind"]] += 1
    a_by_store = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["kind"] == "A":
            a_by_store[r["roaster"]]["beans"] += 1
            for a in r["labels"]:
                a_by_store[r["roaster"]][a] += 1
    b_counts = {a: sum(1 for r in rows if r["kind"] == "B" and r["weak"].get(a) is not None) for a in ATTRS}
    agree = weak_agreement(rows)
    cv = {a: grouped_cv(rows, a) for a in ATTRS}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "k": K, "w_a": W_A, "w_b": W_B,
              "offset_shrink": SHRINK, "use_nbr": USE_NBR, "rows": dict(counts),
              "a_labels_by_store": {s: dict(v) for s, v in sorted(a_by_store.items())},
              "a_labels_per_attr": {a: sum(1 for r in rows if r["kind"] == "A" and a in r["labels"]) for a in ATTRS},
              "b_labels_per_attr": b_counts, "weak_agreement": agree, "transfer_gauge_model_to_A": transfer(rows),
              "cv": cv, "ship": {}}
    for a in ATTRS:
        t = cv[a]["table"]
        # neighbour baseline as production will have it: the pool WITH the new source (it is loaded)
        base, nbr = t["base"]["within1"], t["neighbor_avg_new"]["within1"]
        cands = [c for c in CONFIGS if c.startswith("+") and ("B" not in c or agree[a].get("beats_origin_mean"))]
        best = max(cands, key=lambda c: (t[c]["within1"], -t[c]["mae"]))
        if a in SHIPPED_RECIPES:
            gain, margin, ref = round(t[best]["within1"] - base, R), SHIP_MARGIN_SHIPPED, "shipped (base)"
        else:
            gain, margin, ref = round(t[best]["within1"] - nbr, R), SHIP_MARGIN_NEIGHBOR, "neighbour average"
        report["ship"][a] = {"best": best, "vs": ref, "within1_gain": gain, "margin": margin,
                             "would_replace": gain >= margin,
                             "shipped_recipe": list(SHIPPED_RECIPES[a]) if a in SHIPPED_RECIPES else None}
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("rows", "a_labels_by_store", "b_labels_per_attr", "weak_agreement",
                                             "transfer_gauge_model_to_A")}, indent=1, ensure_ascii=False))
    for a in ATTRS:
        print(a, cv[a]["n"], {k: (v["within1"], v["mae"]) for k, v in cv[a]["table"].items()})
    print(json.dumps(report["ship"], indent=1))
    print(f"wrote {OUT} (config/ untouched: scripts/train_feature_model.py writes the shipped config)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
