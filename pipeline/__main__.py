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
    gl = sub.add_parser("gold-label", help="fill empty gold labels with the judge model")
    gl.add_argument("--judge", default="judge", help="task name in config/models.yaml")
    gl.add_argument("--file", default="gold_enrich.csv", help="CSV name, relative to the eval dir")
    gsc = sub.add_parser("gold-score", help="score enrich output against the labelled gold set")
    gsc.add_argument("--file", default="gold_enrich.csv", help="CSV name, relative to the eval dir")
    ga = sub.add_parser("gold-agree", help="inter-judge agreement between two labelled gold sets")
    ga.add_argument("--a", default="gold_enrich.csv", help="first judge's CSV name, relative to the eval dir")
    ga.add_argument("--b", default="gold_enrich_judge2.csv", help="second judge's CSV name, relative to the eval dir")
    sub.add_parser("roasters-kr", help="collect facts-only Korean roastery bean data (open-data variant)")
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
        embedder = embedder_for()
        stats["embed"] = run_embed(settings.ENRICHED_DIR, settings.NORMALIZED_DIR,
                                   settings.embedded_dir(embedder.target.model), embedder, batch=embedder.target.batch)
        stats["embed"].update(model=embedder.target.model, requests=embedder.requests)
    for stage, s in stats.items():
        print(f"[{stage}] {json.dumps(s, ensure_ascii=False)}")
    if "load" in stages:
        from pipeline.db import apply_schema, connect
        from pipeline.llm import embed_model
        from pipeline.load import run_load
        from pipeline.report import build_report

        with connect() as conn:
            apply_schema(conn)
            stats["load"] = run_load(conn, settings.NORMALIZED_DIR, settings.ENRICHED_DIR,
                                     settings.embedded_dir(embed_model()))
            md = build_report(conn, stats)
        print(f"[load] {json.dumps(stats['load'], ensure_ascii=False)}")
        settings.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = settings.REPORTS_DIR / f"quality_{dt.date.today().isoformat()}.md"
        path.write_text(md, encoding="utf-8")
        print(md)
        print(f"report: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Korean names on a cp949 Windows console
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
        from pipeline.gold import clone_unlabelled, label_gold
        from pipeline.llm import client_for
        from pipeline.records import TaxonomyNode, read_jsonl

        path = settings.EVAL_DIR / a.file
        if a.file != "gold_enrich.csv" and not path.exists():
            clone_unlabelled(settings.EVAL_DIR / "gold_enrich.csv", path)
        vocab = tag_vocab(read_jsonl(settings.NORMALIZED_DIR / "taxonomy.jsonl", TaxonomyNode))
        n = label_gold(path, client_for(a.judge), vocab)
        print(f"labelled {n} rows")
        return 0
    if a.cmd == "gold-score":
        from pipeline.gold import score_gold
        path = settings.EVAL_DIR / a.file
        scores = score_gold(path)
        out_name = "gold_scores.json" if a.file == "gold_enrich.csv" else f"{path.stem}_scores.json"
        (settings.EVAL_DIR / out_name).write_text(json.dumps(scores, indent=2), encoding="utf-8")
        print(json.dumps(scores, indent=2))
        return 0
    if a.cmd == "gold-agree":
        from pipeline.gold import agreement
        result = agreement(settings.EVAL_DIR / a.a, settings.EVAL_DIR / a.b)
        (settings.EVAL_DIR / "gold_agreement.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0
    if a.cmd == "roasters-kr":
        from pipeline.collect.roasters_kr import run_roasters_kr_collect
        from pipeline.http import PoliteClient

        http = PoliteClient(delay=2.0)
        stats, out_path = run_roasters_kr_collect(http, settings.RAW_DIR, dt.date.today().isoformat())
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        print(f"wrote {stats['total']['count']} records to {out_path}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
