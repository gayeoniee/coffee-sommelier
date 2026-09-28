"""Agreement of the CI enrich model (config/models.yaml `enrich_ci`, NVIDIA-hosted) with the local one (`enrich`,
Ollama qwen) on beans the local model already enriched -- docs/adr/0015-automated-refresh.md.

The automated refresh runs in GitHub Actions, where there is no Ollama, so new beans are enriched by `enrich_ci`
while every existing bean was enriched by qwen. This measures how interchangeable the two are on the SAME prompts:
50 beans (seed 42) drawn from the live sources the refresh actually re-enriches (roasters_kr, shopify,
shopify_gauged), whose cached qwen output (data/enriched/cache.jsonl) still matches today's prompt text.

Metrics (raw LLM output, before merging into the record):
  - tags: micro precision/recall/F1 of enrich_ci tags against qwen tags (both lower-cased, SCA vocabulary only)
  - acidity/body/sweetness: share within +-1 and exact, over beans where both models gave a value;
    plus how often exactly one model answered null (`null_disagree`, split by which one)

Usage: uv run python -m scripts.refresh.enrich_agreement [--n 50] [--seed 42]
Writes data/eval/enrich_ci_agreement.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time

from pipeline import settings
from pipeline.enrich import PROMPT, SYSTEM, EnrichOutput, coffee_texts, read_json_lines, tag_vocab
from pipeline.llm import LLMError, client_for, load_targets
from pipeline.records import CoffeeRecord, ReviewRecord, TaxonomyNode, read_jsonl

LIVE_SOURCES = ("roasters_kr", "shopify", "shopify_gauged")
ATTRS = ("acidity", "body", "sweetness")
OUT = settings.EVAL_DIR / "enrich_ci_agreement.json"


def candidates(norm_dir, cache_path, sources=LIVE_SOURCES) -> list[tuple[CoffeeRecord, str, dict]]:
    """(coffee, prompt text, cached qwen output) for live-source beans whose cache entry still matches the prompt."""
    coffees = [c for c in read_jsonl(norm_dir / "coffees.jsonl", CoffeeRecord) if c.source in sources]
    texts = coffee_texts(read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord))
    cache = {e["key"]: e for e in read_json_lines(cache_path)[0]}
    out = []
    for c in coffees:
        text = texts.get(c.key) or c.flavor_summary or ""
        e = cache.get(c.key)
        h = hashlib.sha1(f"{c.name}\n{text}".encode("utf-8")).hexdigest()
        if e and e["status"] == "ok" and e["hash"] == h:
            out.append((c, text, e["output"]))
    return out


def agreement(pairs: list[tuple[dict, dict]], vocab: set[str]) -> dict:
    """pairs = [(reference output, candidate output)] as EnrichOutput dicts."""
    tp = fp = fn = 0
    attrs = {a: {"n": 0, "within1": 0, "exact": 0, "null_disagree": 0, "reference_null_only": 0,
                 "candidate_null_only": 0} for a in ATTRS}
    for ref, cand in pairs:
        r = {t.lower() for t in ref.get("flavor_tags") or [] if t.lower() in vocab}
        c = {t.lower() for t in cand.get("flavor_tags") or [] if t.lower() in vocab}
        tp, fp, fn = tp + len(r & c), fp + len(c - r), fn + len(r - c)
        for a in ATTRS:
            x, y = ref.get(a), cand.get(a)
            s = attrs[a]
            if x is None or y is None:
                s["null_disagree"] += (x is None) != (y is None)
                s["reference_null_only"] += x is None and y is not None
                s["candidate_null_only"] += y is None and x is not None
                continue
            s["n"] += 1
            s["within1"] += abs(x - y) <= 1
            s["exact"] += x == y
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * rc / (p + rc) if p + rc else 0.0
    return {"tags": {"precision": round(p, 4), "recall": round(rc, 4), "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn},
            **{a: {**s, "within1": round(s["within1"] / s["n"], 4) if s["n"] else None,
                   "exact": round(s["exact"] / s["n"], 4) if s["n"] else None} for a, s in attrs.items()}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--task", default="enrich_ci")
    a = ap.parse_args(argv)
    taxonomy = read_jsonl(settings.NORMALIZED_DIR / "taxonomy.jsonl", TaxonomyNode)
    vocab = tag_vocab(taxonomy)
    pool = candidates(settings.NORMALIZED_DIR, settings.ENRICHED_DIR / "cache.jsonl")
    sample = random.Random(a.seed).sample(pool, min(a.n, len(pool)))
    client = client_for(a.task)
    pairs, failed, latencies = [], 0, []
    for c, text, ref in sample:
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": PROMPT.format(name=c.name, text=text[:3000], vocab=", ".join(vocab))}]
        t0 = time.perf_counter()
        try:
            out = client.chat_json(messages, EnrichOutput).model_dump()
        except LLMError as e:
            failed += 1
            print(f"failed {c.key}: {e}", file=sys.stderr)
            continue
        latencies.append(time.perf_counter() - t0)
        pairs.append((ref, out))
    latencies.sort()
    result = {
        "reference": {"task": "enrich", "model": load_targets("enrich")[0].model},
        "candidate": {"task": a.task, "model": load_targets(a.task)[0].model},
        "sources": list(LIVE_SOURCES), "pool": len(pool), "n": len(sample), "seed": a.seed,
        "scored": len(pairs), "failed": failed,
        "latency_s": {"p50": round(latencies[len(latencies) // 2], 2) if latencies else None,
                      "max": round(latencies[-1], 2) if latencies else None},
        **agreement(pairs, set(vocab)),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
