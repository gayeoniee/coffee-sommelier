"""Relabel `coffees.body` from a quality sub-score to actual mouthfeel HEAVINESS for coffeereview beans
(docs/adr/0010-body-heaviness.md).

The defect: pipeline/normalize/datasets.py::normalize_coffeereview quintiles the coffeereview "body" column,
which is a 1-10 QUALITY sub-score (how good the body is), not how heavy/light the coffee feels. That inverts
reality by origin (Ethiopia's delicate body scores *better* than Indonesia's heavy one) and flattens real
variation within a single profile (Mandheling beans, which are famously heavy-bodied, average out near the
catalog median because some are merely well-executed and some aren't).

This script re-derives body as a heaviness judgement straight from each bean's own review text: it pulls the
sentence(s) that actually talk about mouthfeel/texture/body and asks an LLM (task "judge_explain2",
openai/gpt-oss-20b -- see config/models.yaml) to place them on an anchored 1-5 scale:
  1 = light, tea-like, delicate      4 = full, syrupy, creamy
  2 = light-medium                   5 = heavy, viscous, dense
  3 = medium
Null when the text says nothing about weight -- most coffeereview blurbs plainly do (that is what the "body"
sub-score is standing in for today), but the fallback (first 400 chars) still gives the model root to work
with for text that doesn't use those three keywords.

Output: data/enriched/body_heaviness.jsonl (appended, one line per {key, hash, snippet_used, body, model} --
"hash" is sha1(key + the extracted snippet), so re-running only re-asks beans whose extracted text changed).
pipeline/enrich.py's run_enrich() merges this over the quintile body for coffeereview_kaggle beans; CQI beans
get body = None (no review text to judge; the CQI "Body" column is a cupping quality score, not a fact we can
re-derive from text -- see docs/adr/0010); roasters_kr gets Korean heaviness cues in the existing
ko_rule_tags path instead of an LLM call (few beans, terse note-list text).

Usage:
    uv run python scripts/relabel_body.py                  # full run, batches of 10
    uv run python scripts/relabel_body.py --limit 3         # smoke test: only the first 3 batches
    uv run python scripts/relabel_body.py --batch-size 10 --rpm 40
"""
import argparse
import hashlib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import settings  # noqa: E402
from pipeline.enrich import coffee_texts, ends_torn, read_json_lines  # noqa: E402
from pipeline.llm import LLMError, client_for, extract_json  # noqa: E402
from pipeline.records import CoffeeRecord, ReviewRecord, read_jsonl  # noqa: E402

TASK = "judge_explain2"
SOURCE = "coffeereview_kaggle"
FALLBACK_CHARS = 400
BATCH_SIZE = 10
DEFAULT_RPM = 60  # client-side cap; NVIDIA also 429s us into pipeline.llm's own backoff if we're too fast
DEFAULT_WORKERS = 8  # concurrent in-flight requests -- gpt-oss-20b batch calls run ~10-15s each

_KEYWORD_SENTENCE = re.compile(r"[^.!?\n]*\b(mouthfeel|body|texture)\b[^.!?\n]*[.!?]?", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

SYSTEM = ("You are a coffee cupping judge. You score BODY -- how light or heavy a coffee feels in the "
          "mouth (mouthfeel weight) -- never the quality or rating of the coffee. Reply with one JSON "
          "object only, no other text.")
PROMPT = """Score the BODY (mouthfeel weight, not quality) of each numbered coffee's tasting note below.

Anchored 1-5 scale:
1 = light, tea-like, delicate
2 = light-medium
3 = medium
4 = full, syrupy, creamy
5 = heavy, viscous, dense
Use null when the text gives no evidence about how light or heavy the coffee feels.

Notes:
{notes}

Return one JSON object with EXACTLY these keys, each mapping to {{"body": 1|2|3|4|5|null}}: {keys}"""


def extract_snippet(text: str) -> str:
    """The mouthfeel/body/texture sentence(s) in `text`, or its first FALLBACK_CHARS chars if none mention
    those words. Multiple matching sentences are kept in order and joined with a space."""
    hits = [m.group(0).strip() for m in _KEYWORD_SENTENCE.finditer(text)]
    if hits:
        return " ".join(dict.fromkeys(hits))  # de-dupe while preserving order
    return text[:FALLBACK_CHARS]


def targets(enriched_dir: Path, norm_dir: Path) -> list[tuple[CoffeeRecord, str]]:
    coffees = read_jsonl(enriched_dir / "coffees.jsonl", CoffeeRecord)
    texts = coffee_texts(read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord))
    out = []
    for c in coffees:
        if c.source != SOURCE:
            continue
        text = texts.get(c.key)
        if text:
            out.append((c, extract_snippet(text)))
    return out


def load_cache(path: Path) -> dict[str, dict]:
    rows, _ = read_json_lines(path)
    return {r["key"]: r for r in rows}  # later lines win


def build_messages(batch: list[tuple[str, str]]) -> list[dict]:
    notes = "\n".join(f"{i + 1}. [{key}] {snippet[:1200]}" for i, (key, snippet) in enumerate(batch))
    keys = json.dumps([key for key, _ in batch])
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT.format(notes=notes, keys=keys)}]


def coerce_body(v) -> int | None:
    if v is None:
        return None
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return None
    return iv if 1 <= iv <= 5 else None


class Throttle:
    """Client-side request-rate cap shared across worker threads (like pipeline.embed.py's Embedder._throttle,
    generalised to more than one caller at a time)."""

    def __init__(self, rpm: float):
        self.gap = 60.0 / rpm if rpm else 0.0
        self._lock = threading.Lock()
        self._last: float | None = None

    def wait(self) -> None:
        if not self.gap:
            return
        with self._lock:
            now = time.monotonic()
            if self._last is not None:
                remaining = self.gap - (now - self._last)
                if remaining > 0:
                    time.sleep(remaining)
            self._last = time.monotonic()


def _lenient_json(raw: str) -> dict:
    """extract_json(), but tolerant of a literal (unescaped) control character inside a string value --
    gpt-oss-20b occasionally emits a raw newline mid-string when a snippet itself contained one. Strict JSON
    forbids that; `strict=False` accepts it the same way most real-world JSON parsers do."""
    try:
        return extract_json(raw)
    except ValueError:
        import re
        m = re.search(r"\{.*\}", re.sub(r"<think>.*?</think>", "", raw, flags=re.S), re.S)
        if not m:
            raise
        return json.loads(m.group(0), strict=False)


def process_batch(chunk: list[tuple[str, str, str]], client, throttle: Throttle) -> list[dict]:
    """One batch (<= batch_size beans): one chat request -> one cache entry per bean. A failed request marks
    every bean in the batch "failed" (body=None) rather than blocking the rest of the run."""
    throttle.wait()
    messages = build_messages([(k, s) for k, s, _ in chunk])
    try:
        parsed = _lenient_json(client.chat(messages))
    except (LLMError, ValueError) as e:
        return [{"key": key, "hash": h, "status": "failed", "error": str(e), "snippet": snippet[:200],
                 "body": None, "model": None} for key, snippet, h in chunk]
    out = []
    for key, snippet, h in chunk:
        raw_v = (parsed.get(key) or {}).get("body") if isinstance(parsed.get(key), dict) else None
        out.append({"key": key, "hash": h, "status": "ok", "snippet": snippet[:200],
                    "body": coerce_body(raw_v), "model": client.last_model})
    return out


def run(enriched_dir: Path, batch_size: int, rpm: float, limit: int | None, workers: int) -> dict[str, int]:
    items = targets(enriched_dir, settings.NORMALIZED_DIR)
    cache_path = enriched_dir / "body_heaviness.jsonl"
    cache = load_cache(cache_path)

    todo = []
    for c, snippet in items:
        h = hashlib.sha1(f"{c.key}\n{snippet}".encode("utf-8")).hexdigest()
        entry = cache.get(c.key)
        if entry is not None and entry.get("hash") == h and entry.get("status") == "ok":
            continue
        todo.append((c.key, snippet, h))

    stats = {"targets": len(items), "already_cached": len(items) - len(todo), "batches": 0,
              "requests": 0, "ok": 0, "null": 0, "failed": 0}
    if not todo:
        print(f"nothing to do: {stats['already_cached']}/{len(items)} already cached")
        return stats

    client = client_for(TASK)
    throttle = Throttle(rpm)
    enriched_dir.mkdir(parents=True, exist_ok=True)
    chunks = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    if limit is not None:
        chunks = chunks[:limit]
    total = len(chunks)

    with cache_path.open("a", encoding="utf-8") as f:
        if cache_path.exists() and cache_path.stat().st_size and ends_torn(cache_path):
            f.write("\n")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(process_batch, chunk, client, throttle) for chunk in chunks]
            for fut in as_completed(futures):
                entries = fut.result()
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    stats[{"failed": "failed", "ok": "ok"}[entry["status"]]] += 1
                    if entry["status"] == "ok" and entry["body"] is None:
                        stats["null"] += 1
                f.flush()
                stats["requests"] += 1
                stats["batches"] += 1
                print(f"batch {stats['batches']}/{total}: {len(entries)} beans "
                      f"({stats['requests']} requests so far)", flush=True)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    ap.add_argument("--rpm", type=float, default=DEFAULT_RPM)
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="concurrent in-flight requests")
    ap.add_argument("--limit", type=int, default=None, help="stop after this many batches (smoke testing)")
    args = ap.parse_args()
    stats = run(settings.ENRICHED_DIR, args.batch_size, args.rpm, args.limit, args.workers)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
