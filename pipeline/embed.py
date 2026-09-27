import hashlib
import json
from pathlib import Path

from pipeline.enrich import coffee_texts, ends_torn, read_json_lines
from pipeline.records import CoffeeRecord, ReviewRecord, read_jsonl

REVIEW_CHARS = 1500


def embedding_text(c: CoffeeRecord, review_text: str | None, include_tags: bool = True) -> str:
    """The text a coffee's embedding is built from. `include_tags=False` drops the bean's own flavor_tags —
    used to build tag-FREE training/query embeddings for the learned tag model (app/core/tagmodel.py), since
    a genuinely unknown bean's runtime text (app/graphs/analyze_bean.py) never has tags in it either."""
    parts = [
        c.name, c.origin_country, c.process, c.roast_level,
        f"decaf {c.decaf_process or ''}".strip() if c.is_decaf else None,
        (", ".join(c.flavor_tags) or None) if include_tags else None, c.flavor_summary,
        (review_text or "")[:REVIEW_CHARS] or None,
    ]
    return " | ".join(p for p in parts if p)


def run_embed(enriched_dir: Path, norm_dir: Path, out_dir: Path, embedder, batch: int = 32) -> dict[str, int]:
    coffees = read_jsonl(enriched_dir / "coffees.jsonl", CoffeeRecord)
    texts = coffee_texts(read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord))
    path = out_dir / "embeddings.jsonl"
    partial = out_dir / "embeddings.jsonl.partial"  # batches finished by an interrupted run
    cache = {e["key"]: e for p in (path, partial) for e in read_json_lines(p)[0]}
    rows, todo = {}, []
    for c in coffees:
        text = embedding_text(c, texts.get(c.key))
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if c.key in cache and cache[c.key]["hash"] == h:
            rows[c.key] = cache[c.key]
        else:
            todo.append((c.key, h, text))
    out_dir.mkdir(parents=True, exist_ok=True)
    with partial.open("a", encoding="utf-8") as pf:
        if ends_torn(partial):
            pf.write("\n")
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            for (key, h, _), v in zip(chunk, embedder.embed([t for _, _, t in chunk])):
                rows[key] = {"key": key, "hash": h, "vector": v}
                pf.write(json.dumps(rows[key]) + "\n")
            pf.flush()  # a crash later still leaves this batch reusable
    with path.open("w", encoding="utf-8") as f:
        for c in coffees:
            f.write(json.dumps(rows[c.key]) + "\n")
    partial.unlink()
    return {"embedded": len(todo), "cached": len(coffees) - len(todo)}
