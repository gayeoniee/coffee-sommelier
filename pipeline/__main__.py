import argparse
import datetime as dt
import json
import sys

from pipeline import settings

STAGES = ["collect", "normalize", "enrich", "embed", "load"]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run pipeline stages")
    r.add_argument("--only", choices=STAGES, action="append")
    r.add_argument("--source", action="append", help="collect only these sources")
    r.add_argument("--limit", type=int, help="max LLM calls in enrich")
    r.add_argument("--retry-failed", action="store_true")
    q = sub.add_parser("query", help="similarity search")
    q.add_argument("text")
    q.add_argument("-k", type=int, default=5)
    g = q.add_mutually_exclusive_group()
    g.add_argument("--decaf", dest="decaf", action="store_const", const=True, default=None)
    g.add_argument("--no-decaf", dest="decaf", action="store_const", const=False)
    gs = sub.add_parser("gold-sample", help="sample rows for the tagging gold set")
    gs.add_argument("--n", type=int, default=50)
    gs.add_argument("--seed", type=int, default=42)
    sub.add_parser("gold-label", help="fill empty gold labels with the judge model")
    sub.add_parser("gold-score", help="score enrich output against the labelled gold set")
    return ap


def _run(a) -> int:
    stages = a.only or STAGES
    stats: dict[str, dict] = {}
    if "collect" in stages:
        from pipeline.collect import run_collect
        from pipeline.collect.registry import ALL_COLLECTORS
        from pipeline.http import PoliteClient

        cols = [c for c in ALL_COLLECTORS if not a.source or c.name in a.source]
        ms = run_collect(cols, settings.RAW_DIR, PoliteClient(), dt.date.today().isoformat())
        stats["collect"] = {m.source: ("ok" if m.ok else m.error) for m in ms}
    if "normalize" in stages:
        from pipeline.normalize import run_normalize
        stats["normalize"] = run_normalize(settings.RAW_DIR, settings.NORMALIZED_DIR, settings.CURATED_DIR)
    if "enrich" in stages:
        from pipeline.enrich import run_enrich
        from pipeline.llm import client_for
        stats["enrich"] = run_enrich(settings.NORMALIZED_DIR, settings.ENRICHED_DIR, client_for("enrich"),
                                     limit=a.limit, retry_failed=a.retry_failed)
    if "embed" in stages:
        from pipeline.embed import run_embed
        from pipeline.llm import embedder_for
        stats["embed"] = run_embed(settings.ENRICHED_DIR, settings.NORMALIZED_DIR, settings.EMBEDDED_DIR, embedder_for())
    for stage, s in stats.items():
        print(f"[{stage}] {json.dumps(s, ensure_ascii=False)}")
    if "load" in stages:
        from pipeline.db import apply_schema, connect
        from pipeline.load import run_load
        from pipeline.report import build_report

        with connect() as conn:
            apply_schema(conn)
            stats["load"] = run_load(conn, settings.NORMALIZED_DIR, settings.ENRICHED_DIR, settings.EMBEDDED_DIR)
            md = build_report(conn, stats)
        settings.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = settings.REPORTS_DIR / f"quality_{dt.date.today().isoformat()}.md"
        path.write_text(md, encoding="utf-8")
        print(md)
        print(f"report: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "run":
        return _run(a)
    if a.cmd == "query":
        from pipeline.db import connect
        from pipeline.llm import embedder_for
        from pipeline.query import similar

        with connect() as conn:
            for h in similar(conn, embedder_for(), a.text, k=a.k, decaf=a.decaf):
                print(f"{h['score']:.3f}  {h['name']} | {h['roaster']} | {h['origin_country']} | {h['process']} | "
                      f"decaf={h['is_decaf']} | acidity={h['acidity']} body={h['body']} | {', '.join(h['flavor_tags'])}")
        return 0
    if a.cmd == "gold-sample":
        from pipeline.gold import sample_gold
        n = sample_gold(settings.ENRICHED_DIR, settings.NORMALIZED_DIR, settings.EVAL_DIR / "gold_enrich.csv", n=a.n, seed=a.seed)
        print(f"wrote {n} rows to {settings.EVAL_DIR / 'gold_enrich.csv'}")
        return 0
    if a.cmd == "gold-label":
        from pipeline.enrich import tag_vocab
        from pipeline.gold import label_gold
        from pipeline.llm import client_for
        from pipeline.records import TaxonomyNode, read_jsonl

        vocab = tag_vocab(read_jsonl(settings.NORMALIZED_DIR / "taxonomy.jsonl", TaxonomyNode))
        n = label_gold(settings.EVAL_DIR / "gold_enrich.csv", client_for("judge"), vocab)
        print(f"labelled {n} rows")
        return 0
    if a.cmd == "gold-score":
        from pipeline.gold import score_gold
        scores = score_gold(settings.EVAL_DIR / "gold_enrich.csv")
        (settings.EVAL_DIR / "gold_scores.json").write_text(json.dumps(scores, indent=2), encoding="utf-8")
        print(json.dumps(scores, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
