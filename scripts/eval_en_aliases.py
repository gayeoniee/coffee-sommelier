"""English note aliases in the shared note mapper (pipeline.enrich EN_TAG_ALIASES + wheel-name plurals), before -> after.
docs/adr/0019-english-note-aliases.md.

"Before" is the same code with the aliases switched off (pipeline.enrich.EN_TAG_ALIASES = {} -- rule_tags then matches
the wheel names only, byte-identical to the pre-0019 mapper; checked by tests/test_enrich.py).

  guest    runtime text_tags on ~16 English / mixed Korean-English card-like texts (hand-written expectations,
           written by the same person as the aliases -- optimistic)
  e2       Zenodo panel (evaluation only), the open analyze path of scripts/eval_zenodo_panel.py on the full cards:
           tags before text cues (what phase2_zenodo_external.json scores) and after (what the card shows), predicted
           with the old / new mapper and scored against truth read with the old / new mapper
  dryrun   the open beans' stored labels if re-enriched now (pipeline.enrich rules + the cached LLM output, no LLM
           call, nothing written): current code vs. English aliases off vs. what coffee_open holds
  full     the same dry run for the full variant's coffeereview_kaggle rows (reported only; the full DB is not touched)

Writes data/eval/open/phase8_en_aliases.json only. The committed file was produced BEFORE the local coffee_open was
re-enriched and reloaded (ADR 0019): its dry run and E2 2x2 describe that pre-reload state; re-running it afterwards
shows a dry run with nothing left to change.

    uv run python scripts/eval_en_aliases.py
"""
import contextlib
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import enrich, settings  # noqa: E402
from pipeline.records import CoffeeRecord, ReviewRecord, TaxonomyNode, read_jsonl  # noqa: E402

OUT = settings.DATA_DIR / "eval" / "open" / "phase8_en_aliases.json"
OPEN_URL = "postgresql://coffee:coffee@localhost:5432/coffee_open"
OPEN_SOURCES = ("roasters_kr", "shopify", "shopify_gauged", "cqi")  # roasterdb excluded, ADR 0024

# (text, the tags a reader expects) -- written for this ADR
GUEST = [
    ("chocolate caramel wet nut", {"chocolate", "caramelized", "nutty"}),
    ("earthy, spicy, full body", {"musty/earthy", "brown spice"}),
    ("Ethiopia Yirgacheffe washed, jasmine, bergamot, stone fruit", {"jasmine", "citrus fruit", "peach"}),
    ("Kenya AA blackcurrant, citrusy, winey", {"berry", "citrus fruit", "winey"}),
    ("Colombia Huila honey process, caramel, red apple, nuts", {"caramelized", "apple", "nutty"}),
    ("Sumatra Mandheling earthy cedar dark chocolate", {"musty/earthy", "woody", "dark chocolate"}),
    ("Guatemala Antigua chocolatey, toffee, almond", {"chocolate", "caramelized", "almonds"}),
    ("Costa Rica Tarrazu tropical, mango, passion fruit", {"other fruit"}),
    ("Brazil Santos nutty, cacao, low acidity", {"nutty", "cocoa"}),
    ("브라질 세라도 내추럴 caramel nuts", {"caramelized", "nutty"}),
    ("에티오피아 구지 내추럴 blueberries, strawberry jam", {"blueberry", "strawberry"}),
    ("케냐 키리냐가 black currant grapefruit", {"berry", "grapefruit"}),
    ("Decaf Colombia sugarcane EA, toffee, apricot", {"caramelized", "peach"}),
    ("Panama Geisha orange blossom, peachy, earl grey", {"floral", "peach", "black tea"}),
    ("House espresso blend smoky, spice, molasses", {"smoky", "brown spice", "molasses"}),
    ("Honduras decaf mountain water caramel", {"caramelized"}),
]


@contextlib.contextmanager
def aliases_off():
    saved = enrich.EN_TAG_ALIASES
    enrich.EN_TAG_ALIASES = {}
    try:
        yield
    finally:
        enrich.EN_TAG_ALIASES = saved


def prf(rows: list[tuple[set, set]]) -> dict:
    tp = sum(len(t & p) for t, p in rows)
    fp = sum(len(p - t) for t, p in rows)
    fn = sum(len(t - p) for t, p in rows)
    return {"precision": round(tp / (tp + fp), 4) if tp + fp else 0.0, "recall": round(tp / (tp + fn), 4) if tp + fn
            else 0.0, "tags_per_text": round(sum(len(p) for _, p in rows) / len(rows), 3),
            "texts_with_tag": sum(1 for _, p in rows if p)}


def guest(tag_to_cat, tag_ko) -> dict:
    from app.core.textcues import text_tags
    out = {"rows": []}
    for mode in ("before", "after"):
        ctx = aliases_off() if mode == "before" else contextlib.nullcontext()
        with ctx:
            preds = [set(text_tags(t, tag_to_cat, tag_ko, free_text=True)) for t, _ in GUEST]
        out[mode] = prf([(e, p) for (_, e), p in zip(GUEST, preds)])
        for i, p in enumerate(preds):
            if mode == "before":
                out["rows"].append({"text": GUEST[i][0], "expected": sorted(GUEST[i][1]), "before": sorted(p)})
            else:
                out["rows"][i]["after"] = sorted(p)
    return out


def e2(tag_to_cat) -> dict:
    from app.core.featuremodel import FeatureModel
    from app.core.tagcooc import TagCooc
    from app.repo import Repo
    from scripts.eval_zenodo_panel import XLSX, CachedEmbedder, download, predict, read_xlsx_rows, samples_from_rows
    from scripts.eval_zenodo_panel import tag_metrics
    samples = samples_from_rows(read_xlsx_rows(download(XLSX)))
    embed = CachedEmbedder()
    fm = FeatureModel.load(settings.CONFIG_DIR / "feature_model_open.json")
    cooc = TagCooc.load(settings.CONFIG_DIR / "tag_cooc_open.json")
    vocab = list(tag_to_cat)
    repo = Repo(OPEN_URL)
    try:
        t2c, tko = repo.taxonomy()
        tax = (t2c, tko, repo.tag_base_rates())
        preds, truths = {}, {}
        for mode in ("old", "new"):
            ctx = aliases_off() if mode == "old" else contextlib.nullcontext()
            with ctx:
                truths[mode] = [{t.lower() for t in enrich.rule_tags(s["tag_text"], vocab, limit=12)} for s in samples]
                rows = []
                for s in samples:
                    pred, before = predict(s, embed(s["text"]), repo, tax, None, None, fm, True, cooc)
                    rows.append(({t.lower() for t in before}, {t.lower() for t in pred.tags}))
                preds[mode] = rows
    finally:
        repo.close()
    out = {"n_samples": len(samples),
           "truth_tags_per_sample": {m: round(sum(len(t) for t in truths[m]) / len(samples), 3) for m in truths},
           "samples_with_truth": {m: sum(1 for t in truths[m] if t) for m in truths},
           "truth_tag_counts": {m: dict(Counter(t for ts in truths[m] for t in ts).most_common(25)) for m in truths}}
    for pm in ("old", "new"):
        for tm in ("old", "new"):
            keep = [i for i, t in enumerate(truths[tm]) if t]
            out[f"pred_{pm}_truth_{tm}"] = {
                "before_text_cues": tag_metrics([(truths[tm][i], preds[pm][i][0]) for i in keep], tag_to_cat),
                "shown": tag_metrics([(truths[tm][i], preds[pm][i][1]) for i in keep], tag_to_cat),
                "shown_per_sample": round(sum(len(preds[pm][i][1]) for i in keep) / len(keep), 3)}
    return out


def dry_run(sources: tuple[str, ...], stored: dict[str, list[str]] | None) -> dict:
    """Re-run enrich's tag step (rules, then the cached LLM output when rules find nothing) without calling an LLM
    or writing anything."""
    norm, enr = settings.DATA_DIR / "normalized", settings.ENRICHED_DIR
    coffees = [c for c in read_jsonl(norm / "coffees.jsonl", CoffeeRecord) if c.source in sources]
    texts = enrich.coffee_texts(read_jsonl(norm / "reviews.jsonl", ReviewRecord))
    taxonomy = read_jsonl(norm / "taxonomy.jsonl", TaxonomyNode)
    vocab, ko_vocab = enrich.tag_vocab(taxonomy), enrich.ko_tag_vocab(taxonomy)
    cache, _ = enrich._load_cache(enr / "cache.jsonl")
    enriched_now = {r["key"]: r.get("flavor_tags") or [] for r in map(json.loads, (enr / "coffees.jsonl").read_text(
        encoding="utf-8").splitlines()) if r["source"] in sources}

    def tags_of(c) -> list[str]:
        text = texts.get(c.key) or c.flavor_summary or ""
        c2 = enrich._apply_rules(c, text, vocab, ko_vocab)
        if enrich.needs_llm(c2, text):
            h = hashlib.sha1(f"{c.name}\n{text}".encode()).hexdigest()
            e = cache.get(c.key)
            if e and e["hash"] == h and e["status"] == "ok":
                c2 = enrich._merge_llm(c2, enrich.EnrichOutput(**e["output"]), set(vocab))
        return list(c2.flavor_tags)

    new = {c.key: tags_of(c) for c in coffees}
    with aliases_off():
        old = {c.key: tags_of(c) for c in coffees}
    src = {c.key: c.source for c in coffees}
    out = {}
    for s in sources:
        keys = [k for k in new if src[k] == s]
        if not keys:
            continue
        row = {"beans": len(keys),
               "tagged_before": sum(1 for k in keys if old[k]), "tagged_after": sum(1 for k in keys if new[k]),
               "changed": sum(1 for k in keys if set(old[k]) != set(new[k])),
               "gained_tags": sum(len(set(new[k]) - set(old[k])) for k in keys),
               "lost_tags": sum(len(set(old[k]) - set(new[k])) for k in keys),
               "tags_per_bean_before": round(sum(len(old[k]) for k in keys) / len(keys), 3),
               "tags_per_bean_after": round(sum(len(new[k]) for k in keys) / len(keys), 3),
               "enriched_cache_matches_before": sum(1 for k in keys if set(enriched_now.get(k, [])) == set(old[k])),
               "gained": dict(Counter(t for k in keys for t in set(new[k]) - set(old[k])).most_common(12)),
               "lost": dict(Counter(t for k in keys for t in set(old[k]) - set(new[k])).most_common(12)),
               "examples": [{"key": k, "before": old[k], "after": new[k]} for k in keys
                            if set(old[k]) != set(new[k])][:6]}
        if stored is not None:
            row["db_matches_before"] = sum(1 for k in keys if k in stored and set(stored[k]) == set(old[k]))
            row["db_matches_after"] = sum(1 for k in keys if k in stored and set(stored[k]) == set(new[k]))
            row["in_db"] = sum(1 for k in keys if k in stored)
            # what a re-enrich changes against coffee_open as loaded: the English aliases plus anything pending from
            # earlier mapper changes (roasters_kr: the ADR 0017 Korean aliases were never re-enriched)
            row["db_changed_by_reenrich"] = sum(1 for k in keys if k in stored and set(stored[k]) != set(new[k]))
            row["db_tags_per_bean"] = round(sum(len(stored.get(k, [])) for k in keys) / len(keys), 3)
        out[s] = row
    return out


def main() -> int:
    from app.repo import Repo
    repo = Repo(OPEN_URL)
    try:
        tag_to_cat, tag_ko = repo.taxonomy()
    finally:
        repo.close()
    with psycopg.connect(OPEN_URL, row_factory=dict_row) as conn:
        stored = {r["key"]: [t.lower() for t in r["flavor_tags"] or ()] for r in conn.execute(
            "SELECT key, flavor_tags FROM coffees WHERE active").fetchall()}
    report = {"generated_at": datetime.now(UTC).isoformat(),
              "n_aliases": len(enrich.EN_TAG_ALIASES),
              "guest": guest(tag_to_cat, tag_ko),
              "dryrun_open": dry_run(OPEN_SOURCES, stored),
              "dryrun_full_coffeereview": dry_run(("coffeereview_kaggle",), None),
              "e2": e2(tag_to_cat)}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("guest", "dryrun_open", "dryrun_full_coffeereview")},
                     ensure_ascii=False, indent=1)[:9000])
    e = report["e2"]
    print({k: v for k, v in e.items() if k.startswith("pred_") or k.startswith("truth_tags") or k.startswith("samples")})
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
