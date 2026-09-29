"""Open variant, after ADR 0017: beans that still show ONE tag because even the tagged neighbours disagree, and the
precision ADR 0017's fill gave up. docs/adr/0018-open-tag-cooccurrence.md.

Candidates (all licence-clean: labels from roasters_kr/shopify/shopify_gauged notes only -- never coffeereview,
RoasterDB or Zenodo; no LLM):
  thin-vote fill (ADR 0017's step, before the guest's words are merged):
    none      the plain vote (before ADR 0017)
    fill      ADR 0017 (shipped): vote < 2 tags -> top up to 2 from the tagged-only vote (RoasterDB out)
    fill_echo the same, but the guest's own tags count toward the 2
    fill_s4   the tagged-only vote needs >= 4 of 10 neighbours (not 3)
    fill_agree  a fill tag also needs co-occurrence support from a guest tag (n(A,B) >= 2, share >= 0.15)
    fill_cat  a fill tag's SCA category must be one of the guest tags' categories
    fill_comb score = tagged-vote share + co-occurrence share (best guest tag), kept at >= COMB_MIN
  co-occurrence fill (after the guest's words): top the shown tags up to T from P(B | guest tag A) over the open
  beans with notes, gate (support n(A), pair n(A,B), share, lift) -- a grid; "given" = the guest's tags (echo) or
  every shown tag.
Situations (leak-free query embeddings, as ADR 0016/0017):
  free    note-free card -> the note-free feature tag model, else the (filled) vote; co-occurrence has no guest tag
  notes1  card + the bean's FIRST note word (ADR 0017's decision table)
  notesall  card + ALL the bean's note words (a guest who copied the whole card)
Scored on the tags shown beyond the guest's own (echo excluded -- circular); truth = bean tags minus echo.
E1: grouped leave-one-roaster-out over the 277 licence-clean tagged beans; the neighbour pools AND the co-occurrence
table leave the target's roaster out. E2: Zenodo Q-grader panel (evaluation only), table from all 277.
Metrics: micro precision/recall/F1, SCA category F1, tags shown per bean, share of beans showing <= 1 tag.

Writes data/eval/open/phase7_open_tag_cooc.json only.

    uv run python scripts/eval_open_tag_cooc.py
"""
import json
import re
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.predict import LIFT, MAX_TAGS, TAG_SHARE, fill_tags, predict_from_neighbors  # noqa: E402
from app.core.tagcooc import (  # noqa: E402
    COOC_MIN_LIFT, COOC_MIN_PAIR, COOC_MIN_SHARE, COOC_MIN_SUPPORT, COOC_TARGET, TagCooc,
)
from app.core.textcues import text_tags  # noqa: E402
from pipeline import settings  # noqa: E402
from pipeline.enrich import rule_tags  # noqa: E402
from scripts.build_tag_cooc import bean_tag_rows, parent_map  # noqa: E402
from scripts.eval_open_tag_fill import NOTES_CACHE, POOLS, neighbours, ordered  # noqa: E402
from scripts.eval_open_tags import CACHE, X_of, TagLR, feat_vec, load, pick_threshold  # noqa: E402
from scripts.eval_zenodo_panel import (  # noqa: E402
    EMB_CACHE as ZENODO_CACHE, XLSX, CachedEmbedder, download, read_xlsx_rows, samples_from_rows, tag_metrics,
)
from scripts.train_feature_model import OPEN_URL  # noqa: E402

OUT = settings.DATA_DIR / "eval" / "open" / "phase7_open_tag_cooc.json"
ALLNOTES_CACHE = settings.RAW_DIR / "open_tag_eval" / "allnote_query_embeddings.jsonl"
FILLS = ("none", "fill", "fill_echo", "fill_s4", "fill_agree", "fill_s4|agree", "fill_1", "fill_gen", "fill_cat",
         "fill_comb")
COMB_MIN = 0.5
AGREE = (2, 0.15)                      # fill_agree: n(A, B) >= 2 and share >= 0.15
# co-occurrence grid: target, support n(A), pair n(A,B), share, lift
GRID = list(product((2, 3), (3, 5, 8), (2, 3), (0.2, 0.3, 0.4), (1.0, 1.2, 1.5)))
DEFAULT = (COOC_TARGET, COOC_MIN_SUPPORT, COOC_MIN_PAIR, COOC_MIN_SHARE, COOC_MIN_LIFT)
BEFORE = "fill"                        # ADR 0017, current main
AFTER = ("fill", "default")            # shipped (ADR 0018): ADR 0017's fill, then the co-occurrence top-up at DEFAULT
SITUATIONS = ("free", "notes1", "notesall")


def notes_of(summary: str | None) -> list[str]:
    return [n.strip().split(">")[-1].strip() for n in (summary or "").replace(";", ",").split(",") if n.strip()]


# ---- per-bean state -----------------------------------------------------------------------------------------------
def bean_state(near_all, near_tag, base_rates, echo: list[str], model_tags: list[str] | None) -> dict:
    """Everything the fill variants need for one query: the plain vote, the tagged-only vote (and its >= 4/10
    version), the tagged neighbours' tag counts, the guest's tags, the note-free model's tags (free input only)."""
    pv = [t.lower() for t in predict_from_neighbors(near_all, base_rates=base_rates).tags]
    tv = [t.lower() for t in predict_from_neighbors(near_tag, base_rates=base_rates).tags]
    tv4 = [t.lower() for t in predict_from_neighbors(near_tag, base_rates=base_rates, tag_share=0.4).tags]
    counts = defaultdict(int)
    for n in near_tag:
        for t in {x.lower() for x in n.tags}:
            counts[t] += 1
    return {"pv": pv, "tv": tv, "tv4": tv4, "counts": dict(counts), "k_tag": len(near_tag), "echo": echo,
            "model": model_tags, "base_rates": base_rates}


def cooc_share(cooc: TagCooc, given: list[str], b: str) -> tuple[float, int]:
    best = (0.0, 0)
    for a in given:
        n_a = cooc.tag_count.get(a, 0)
        if n_a:
            n_ab = cooc.pair_count.get(a, {}).get(b, 0)
            best = max(best, (n_ab / n_a, n_ab))
    return best


def agrees(cooc: TagCooc, echo: list[str], b: str) -> bool:
    share, n_ab = cooc_share(cooc, echo, b)
    return n_ab >= AGREE[0] and share >= AGREE[1]


def run_fill(st: dict, variant: str, cooc: TagCooc, tag_to_cat: dict[str, str]) -> list[str]:
    """The predicted tags BEFORE the guest's words are merged (analyze_bean: vote -> fill -> feature tag model)."""
    pv, tv, echo = st["pv"], st["tv"], st["echo"]
    if variant == "none":
        out = pv
    elif variant == "fill":
        out = fill_tags(pv, tv)
    elif variant == "fill_echo":
        need = max(0, 2 - len(set(echo) | set(pv)))
        out = fill_tags(pv, [t for t in tv if t not in echo], min_tags=len(pv) + need)
    elif variant == "fill_s4":
        out = fill_tags(pv, st["tv4"])
    elif variant == "fill_agree":
        ok = [t for t in tv if not echo or agrees(cooc, echo, t)]
        out = fill_tags(pv, ok)
    elif variant == "fill_s4|agree":
        ok = [t for t in tv if t in st["tv4"] or not echo or agrees(cooc, echo, t)]
        out = fill_tags(pv, ok)
    elif variant == "fill_1":
        out = fill_tags(pv, tv, min_tags=min(2, len(pv) + 1))
    elif variant == "fill_gen":
        gen = list(dict.fromkeys(cooc.parent.get(t, t) for t in tv))
        out = fill_tags(pv, gen)
    elif variant == "fill_cat":
        cats = {tag_to_cat.get(t) for t in echo}
        out = fill_tags(pv, [t for t in tv if not echo or tag_to_cat.get(t) in cats])
    elif variant == "fill_comb":
        k = max(st["k_tag"], 1)
        br = st["base_rates"]
        scored = []
        for b in set(st["counts"]) | {b for a in echo for b in cooc.pair_count.get(a, {})}:
            vs = st["counts"].get(b, 0) / k
            cs = cooc_share(cooc, echo, b)[0] if echo else 0.0
            if vs + cs >= max(COMB_MIN, LIFT * br.get(b, 0.0)) and (vs >= 0.2 or cs >= 0.2):
                scored.append((vs + cs, b))
        out = fill_tags(pv, [b for _, b in sorted(scored, key=lambda x: (-x[0], x[1]))])
    else:
        raise ValueError(variant)
    if st["model"]:                    # free input: the note-free tag model replaces the (filled) vote
        out = st["model"]
    return out


def shown_tags(st: dict, fill: list[str], cooc: TagCooc | None, params, given_mode: str = "echo") -> list[str]:
    """with_text_cues (guest's words first, cap MAX_TAGS), then the co-occurrence top-up."""
    shown = list(dict.fromkeys([*st["echo"], *fill]))[:MAX_TAGS]
    if cooc is None or params is None:
        return shown
    target, sup, pair, share, lift = params
    given = st["echo"] if given_mode == "echo" else shown
    if not st["echo"] or len(shown) >= target:
        return shown
    add = cooc.candidates(given, shown, min_support=sup, min_pair=pair, min_share=share, min_lift=lift)
    return [*shown, *(b for b, *_ in add[:target - len(shown)])]


def rescue(st: dict, shown: list[str], cooc: TagCooc, min_count: int = 2) -> list[str]:
    """Still <= 1 tag: add the tagged-neighbour tag with the most votes if >= `min_count` of them agree (rank ties by
    rarity, like the vote), never one on the same SCA branch as the shown tag."""
    if len(shown) > 1:
        return shown
    br = st["base_rates"]
    cands = [(c * np.log(1 / max(br.get(t, 0.0), 1e-6)), t) for t, c in st["counts"].items()
             if c >= min_count and t not in shown and not any(cooc.related(t, x) for x in shown)]
    return [*shown, max(cands)[1]] if cands else shown


def metrics(truths: list[set], echoes: list[list[str]], shown: list[list[str]], tag_to_cat,
            base: list[list[str]] | None = None) -> dict:
    """tag_metrics on the shown tags beyond the echo; with `base` (the same beans without the co-occurrence step)
    also the precision of the co-occurrence-added tags alone, at tag and SCA-category level."""
    fill = [set(s) - set(e) for s, e in zip(shown, echoes)]
    m = tag_metrics([(t - set(e), f) for t, e, f in zip(truths, echoes, fill)], tag_to_cat)
    m["tags_shown"] = round(sum(len(s) for s in shown) / len(shown), 3)
    m["share_le1"] = round(sum(1 for s in shown if len(s) <= 1) / len(shown), 4)
    m["fill_per_bean"] = round(sum(len(f) for f in fill) / len(fill), 3)
    if base is not None:
        added = [(t - set(e), set(s) - set(b)) for t, e, s, b in zip(truths, echoes, shown, base)]
        n = sum(len(a) for _, a in added)
        m["cooc_added"] = n
        m["cooc_added_precision"] = round(sum(len(t & a) for t, a in added) / n, 4) if n else None
        m["cooc_added_cat_precision"] = round(sum(
            1 for t, a in added for x in a if tag_to_cat.get(x) in {tag_to_cat.get(y) for y in t}) / n, 4) if n else None
    return m


def evaluate(states: list[dict], truths: list[set], coocs: list[TagCooc], tag_to_cat) -> dict:
    """{fill variant: {"no_cooc": m, "default": m, "given_shown": m, "grid": {key: m}}}"""
    out = {}
    echoes = [st["echo"] for st in states]
    for v in FILLS:
        fills = [run_fill(st, v, c, tag_to_cat) for st, c in zip(states, coocs)]
        base = [shown_tags(st, f, None, None) for st, f in zip(states, fills)]

        def with_cooc(params, mode="echo", fills=fills, base=base):
            shown = [shown_tags(st, f, c, params, mode) for st, f, c in zip(states, fills, coocs)]
            return metrics(truths, echoes, shown, tag_to_cat, base)
        res = {"no_cooc": metrics(truths, echoes, base, tag_to_cat), "default": with_cooc(DEFAULT),
               "given_shown": with_cooc(DEFAULT, "shown"),
               "rescue": metrics(truths, echoes, [rescue(st, b, c) for st, b, c in zip(states, base, coocs)],
                                 tag_to_cat, base),
               "default+rescue": metrics(truths, echoes, [rescue(st, shown_tags(st, f, c, DEFAULT), c) for st, f, c
                                                          in zip(states, fills, coocs)], tag_to_cat, base)}
        if v in ("fill", "fill_echo", "fill_agree", "fill_comb", "fill_s4", "fill_s4|agree", "fill_1"):
            res["grid"] = {"T{}_n{}_p{}_s{}_l{}".format(*g): with_cooc(g) for g in GRID}
        out[v] = res
    return out


# ---- E1 -----------------------------------------------------------------------------------------------------------
def e1(conn, rows, embeds, base_rates, tag_to_cat, tag_ko, table_rows, parent) -> dict:
    groups = np.array([r["roaster"] for r in rows])
    truths = [r["truth"] for r in rows]
    Xf = X_of(rows, "feat")
    model = [None] * len(rows)
    for g in sorted(set(groups)):
        tr, te = np.nonzero(groups != g)[0], np.nonzero(groups == g)[0]
        thr = pick_threshold(Xf[tr], [truths[i] for i in tr], groups[tr])
        m = TagLR(Xf[tr], [truths[i] for i in tr])
        for i, p in zip(te, ordered(m, m.proba(Xf[te]), thr)):
            model[i] = p
    fold_cooc = {g: TagCooc.from_beans([t["tags"] for t in table_rows if t["roaster"] != g], parent)
                 for g in set(groups)}
    from app.core.parse import parse_bean_text
    out = {}
    for sit in SITUATIONS:
        idx, texts, echoes = [], [], []
        for i, r in enumerate(rows):
            card = r["text"].replace(" | ", " ")
            if sit == "free":
                text = r["text"]
            else:
                notes = notes_of(r["flavor_summary"])
                if not notes:
                    continue
                text = f"{card} {notes[0]}" if sit == "notes1" else f"{card} {', '.join(notes)}"
            echo = list(dict.fromkeys(t.lower() for t in text_tags(text, tag_to_cat, tag_ko, free_text=True)))
            if sit != "free" and not echo:
                continue
            if sit == "notes1" and not truths[i] - set(echo):
                continue                                     # ADR 0017's notes set (something left to fill)
            idx.append(i)
            texts.append(text)
            echoes.append(echo)
        states = []
        for i, text, echo in zip(idx, texts, echoes):
            r = rows[i]
            if sit == "free":
                vec, origin, process = r["vec"], r["origin_country"], r["process"]
            else:
                p = parse_bean_text(text)
                vec, origin, process = embeds[sit](text), p.origin_country, p.process
            near_all = neighbours(conn, vec, origin, process, POOLS["vote_all"], exclude_roaster=r["roaster"],
                                  exclude_id=r["id"])
            near_tag = neighbours(conn, vec, origin, process, POOLS["vote_tagged_clean"],
                                  exclude_roaster=r["roaster"], exclude_id=r["id"])
            states.append(bean_state(near_all, near_tag, base_rates, echo,
                                      model[i] if sit == "free" and not echo else None))
        res = evaluate(states, [truths[i] for i in idx], [fold_cooc[rows[i]["roaster"]] for i in idx], tag_to_cat)
        out[sit] = {"n": len(idx), "example": texts[1] if len(texts) > 1 else None, "table": res,
                    "samples": samples(states, [texts[j] for j in range(len(idx))], [truths[i] for i in idx],
                                       [fold_cooc[rows[i]["roaster"]] for i in idx], tag_to_cat)}
    return out


def samples(states, texts, truths, coocs, tag_to_cat, n: int = 12) -> list[dict]:
    """Beans that showed <= 1 tag before (fill, no co-occurrence): what the default co-occurrence adds."""
    out = []
    for st, text, truth, c in zip(states, texts, truths, coocs):
        before = shown_tags(st, run_fill(st, BEFORE, c, tag_to_cat), None, None)
        if len(before) > 1:
            continue
        after = shown_tags(st, run_fill(st, BEFORE, c, tag_to_cat), c, DEFAULT)
        out.append({"text": text, "truth": sorted(truth), "before": before, "after": after})
        if len(out) >= n:
            break
    return out


# ---- E2 -----------------------------------------------------------------------------------------------------------
# Evaluation-only reading of the panel's English words the SCA-name mapper (pipeline.enrich rule_tags) misses --
# plurals and everyday forms ("caramel" 189x, "nuts" 148x, "berries", "citrus"), mapped like the ADR 0017 Korean
# aliases (살구 -> peach, 리치 -> other fruit). Used only for the "table_x" sensitivity rows, never at runtime.
ENGLISH_FORMS = {
    "caramel": "caramelized", "roasted nuts": "nutty", "nuts": "nutty", "nut": "nutty", "walnut": "nutty",
    "hazelnuts": "hazelnut", "almond": "almonds", "peanut": "peanuts", "red berries": "berry",
    "dark berries": "berry", "black berries": "berry", "berries": "berry", "cranberry": "berry",
    "black currant": "berry", "currants": "berry", "currant": "berry", "citrus": "citrus fruit",
    "bergamot": "citrus fruit", "tangerine": "orange", "prunes": "prune", "raisins": "raisin",
    "dried fruits": "dried fruit", "dried apricots": "peach", "apricot": "peach", "flowers": "floral",
    "grapes": "grape", "pineapple": "pinapple", "tropical fruits": "other fruit", "lychee": "other fruit",
    "passion fruit": "other fruit", "cane sugar": "brown sugar", "wood": "woody",
}


def english_forms(text: str) -> str:
    out = (text or "").lower()
    for k in sorted(ENGLISH_FORMS, key=len, reverse=True):
        out = re.sub(rf"(?<![a-z]){re.escape(k)}(?![a-z])", ENGLISH_FORMS[k], out)
    return out


def e2(conn, rows, free_embed, zen_embed, base_rates, tag_to_cat, tag_ko, cooc) -> dict:
    from app.core.parse import parse_bean_text
    samples_ = samples_from_rows(read_xlsx_rows(download(XLSX)))
    vocab = list(tag_to_cat)
    truths = [{t.lower() for t in rule_tags(s["tag_text"], vocab, limit=12)} for s in samples_]
    truths_x = [{t.lower() for t in rule_tags(english_forms(s["tag_text"]), vocab, limit=12)} | t
                for s, t in zip(samples_, truths)]
    ttr = [r["truth"] for r in rows]
    Xf = X_of(rows, "feat")
    thr = pick_threshold(Xf, ttr, np.array([r["roaster"] for r in rows]))
    m = TagLR(Xf, ttr)
    out = {}
    for sit in SITUATIONS:
        idx, texts, echoes, embed = [], [], [], zen_embed
        for i, s in enumerate(samples_):
            if not truths[i]:
                continue
            parts = s["text"].split(" | ")
            base = " | ".join(p for p in parts if not p.startswith("notes:"))
            notes = next((p[len("notes: "):] for p in parts if p.startswith("notes:")), "")
            if sit == "free":
                text = base
                if not text.strip():
                    continue
            elif sit == "notes1":
                note = notes.split(",")[0].strip() if notes else ""
                if not note:
                    continue
                text = f"{base} | {note}" if base else note
            else:
                if not notes:
                    continue
                text = s["text"]
            echo = list(dict.fromkeys(t.lower() for t in text_tags(text, tag_to_cat, tag_ko, free_text=True)))
            if sit != "free" and not echo:
                continue
            if sit == "notes1" and not truths[i] - set(echo):
                continue
            idx.append(i)
            texts.append(text)
            echoes.append(echo)
        parsed = [parse_bean_text(t) for t in texts]
        states = []
        for t, p, echo in zip(texts, parsed, echoes):
            vec = (free_embed if sit == "free" else embed)(t)
            model_tags = None
            if sit == "free" and not echo:
                F = np.array([feat_vec(p.origin_country, p.process, p.roast_level, p.is_decaf, p.decaf_process, None,
                                       None, p.text)], float)
                model_tags = ordered(m, m.proba(F), thr)[0]
            near_all = neighbours(conn, vec, p.origin_country, p.process, POOLS["vote_all"])
            near_tag = neighbours(conn, vec, p.origin_country, p.process, POOLS["vote_tagged_clean"])
            states.append(bean_state(near_all, near_tag, base_rates, echo, model_tags))
        res = evaluate(states, [truths[i] for i in idx], [cooc] * len(idx), tag_to_cat)
        # sensitivity: the panel truth read with common English forms the SCA-name mapper misses (evaluation only)
        res_x = evaluate(states, [truths_x[i] for i in idx], [cooc] * len(idx), tag_to_cat)
        out[sit] = {"n": len(idx), "example": texts[0] if texts else None, "table": res, "table_x": res_x,
                    "samples": samples(states, texts, [truths[i] for i in idx], [cooc] * len(idx), tag_to_cat)}
    return out


KEYS = ("precision", "recall", "f1", "category_f1", "tags_shown", "share_le1")


def pick(tab: dict, fill: str, mode: str) -> dict:
    return {c: tab[fill][mode][c] for c in KEYS}


def main() -> int:
    warnings.filterwarnings("ignore", module="sklearn")
    from app.repo import Repo
    free_embed = CachedEmbedder(CACHE)
    embeds = {"notes1": CachedEmbedder(NOTES_CACHE), "notesall": CachedEmbedder(ALLNOTES_CACHE)}
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
        table_rows = bean_tag_rows(conn, tag_to_cat, tag_ko)
        parent = parent_map(conn)
        r1 = e1(conn, rows, embeds, base_rates, tag_to_cat, tag_ko, table_rows, parent)
        cooc = TagCooc.from_beans([t["tags"] for t in table_rows], parent)
        r2 = e2(conn, rows, free_embed, zen_embed, base_rates, tag_to_cat, tag_ko, cooc)

    summary = {}
    for name, res, key in (("e1", r1, "table"), ("e2", r2, "table"), ("e2x", r2, "table_x")):
        for sit in SITUATIONS:
            tab = res[sit][key]
            summary[f"{name}_{sit}"] = {"n": res[sit]["n"], "before": pick(tab, BEFORE, "no_cooc"),
                                        "after": pick(tab, *AFTER)}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "max_tags": MAX_TAGS,
              "vote_gate": {"share": TAG_SHARE, "lift": LIFT}, "before": BEFORE,
              "after": list(AFTER), "cooc_default": dict(zip(("target", "min_support", "min_pair", "min_share", "min_lift"), DEFAULT)),
              "table_beans": len(table_rows), "summary": summary, "e1": r1, "e2": r2,
              "embedding_requests": free_embed.requests + sum(e.requests for e in embeds.values())
              + zen_embed.requests}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, res in (("E1", r1), ("E2", r2)):
        for sit in SITUATIONS:
            print(f"== {name} {sit} n={res[sit]['n']}")
            for v, t in res[sit]["table"].items():
                for mode in ("no_cooc", "default", "rescue", "default+rescue"):
                    m = t[mode]
                    print(f"  {v:11s} {mode:12s} " + " ".join(f"{m[c]:.3f}" for c in KEYS))
    print(f"wrote {OUT} ({report['embedding_requests']} embedding requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
