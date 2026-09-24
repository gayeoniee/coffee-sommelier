import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from pipeline.llm import LLMError
from pipeline.records import CoffeeRecord, ReviewRecord, TaxonomyNode, read_jsonl, write_jsonl
from pipeline.rules import detect_decaf


class EnrichOutput(BaseModel):
    flavor_tags: list[str] = Field(default_factory=list)
    acidity: int | None = Field(default=None, ge=1, le=5)
    body: int | None = Field(default=None, ge=1, le=5)
    sweetness: int | None = Field(default=None, ge=1, le=5)


SYSTEM = "You extract structured coffee flavor data from tasting notes. Reply with one JSON object only."
PROMPT = """Coffee: {name}
Tasting text:
{text}

Allowed flavor tags (SCA flavor wheel): {vocab}

Return JSON: {{"flavor_tags": [up to 6 tags from the allowed list], "acidity": 1-5 or null, "body": 1-5 or null, "sweetness": 1-5 or null}}
Scale: 1 = very low, 3 = moderate, 5 = very high. Use null when the text gives no evidence."""


def tag_vocab(taxonomy: list[TaxonomyNode]) -> list[str]:
    return sorted({n.name_en.lower() for n in taxonomy if n.level >= 2})


def rule_tags(text: str, vocab: list[str], limit: int = 6) -> list[str]:
    t = (text or "").lower()
    hits: list[tuple[int, str]] = []
    for term in sorted(vocab, key=len, reverse=True):
        m = re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", t)
        if m and not any(term in h for _, h in hits):
            hits.append((m.start(), term))
    return [term for _, term in sorted(hits)][:limit]


def coffee_texts(reviews: list[ReviewRecord]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in reviews:
        out[r.coffee_key] = f"{out[r.coffee_key]}\n{r.text}" if r.coffee_key in out else r.text
    return out


def needs_llm(c: CoffeeRecord, text: str) -> bool:
    return bool(text) and (not c.flavor_tags or c.acidity is None or c.body is None)


def _apply_rules(c: CoffeeRecord, text: str, vocab: list[str]) -> CoffeeRecord:
    update: dict = {}
    if not c.flavor_tags and text:
        update["flavor_tags"] = rule_tags(text, vocab)
    if not c.is_decaf:
        is_decaf, process = detect_decaf(c.name, text)
        if is_decaf:
            update.update(is_decaf=True, decaf_process=process)
    return c.model_copy(update=update)


def _merge_llm(c: CoffeeRecord, o: EnrichOutput, vocab: set[str]) -> CoffeeRecord:
    update = {k: getattr(o, k) for k in ("acidity", "body", "sweetness") if getattr(c, k) is None and getattr(o, k)}
    if not c.flavor_tags:
        update["flavor_tags"] = [t.lower() for t in o.flavor_tags if t.lower() in vocab][:6]
    return c.model_copy(update=update)


def read_json_lines(path: Path) -> tuple[list[dict], int]:
    """Parse a JSONL file, skipping lines torn by an interrupted write. Returns (rows, skipped)."""
    if not path.exists():
        return [], 0
    rows, skipped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                skipped += 1
    return rows, skipped


def _load_cache(path: Path) -> tuple[dict[str, dict], int]:
    rows, skipped = read_json_lines(path)
    return {e["key"]: e for e in rows}, skipped  # later lines win


def ends_torn(path: Path) -> bool:
    """True when the file is non-empty and its last byte is not a newline (an interrupted append)."""
    with path.open("rb") as f:
        f.seek(0, 2)
        if f.tell() == 0:
            return False
        f.seek(-1, 2)
        return f.read(1) != b"\n"


def run_enrich(norm_dir: Path, out_dir: Path, client, limit: int | None = None, retry_failed: bool = False) -> dict[str, int]:
    coffees = read_jsonl(norm_dir / "coffees.jsonl", CoffeeRecord)
    texts = coffee_texts(read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord))
    vocab = tag_vocab(read_jsonl(norm_dir / "taxonomy.jsonl", TaxonomyNode))
    vocab_set = set(vocab)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "cache.jsonl"
    cache, skipped = _load_cache(cache_path)
    stats = {"coffees": 0, "llm_calls": 0, "llm_ok": 0, "llm_failed": 0, "cache_torn_lines": skipped}
    enriched = []
    with cache_path.open("a", encoding="utf-8") as cache_file:
        if ends_torn(cache_path):
            cache_file.write("\n")  # terminate a torn last line so the next entry starts on its own line
        for c in coffees:
            text = texts.get(c.key) or c.flavor_summary or ""
            c = _apply_rules(c, text, vocab)
            if needs_llm(c, text):
                h = hashlib.sha1(f"{c.name}\n{text}".encode("utf-8")).hexdigest()
                entry = cache.get(c.key)
                fresh = entry is not None and entry["hash"] == h
                if not (fresh and (entry["status"] == "ok" or not retry_failed)) and (limit is None or stats["llm_calls"] < limit):
                    stats["llm_calls"] += 1
                    messages = [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": PROMPT.format(name=c.name, text=text[:3000], vocab=", ".join(vocab))}]
                    try:
                        o = client.chat_json(messages, EnrichOutput)
                        entry = {"key": c.key, "hash": h, "status": "ok", "output": o.model_dump(), "model": client.last_model}
                        stats["llm_ok"] += 1
                    except LLMError as e:
                        entry = {"key": c.key, "hash": h, "status": "failed", "error": str(e), "model": None}
                        stats["llm_failed"] += 1
                    cache[c.key] = entry
                    cache_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    cache_file.flush()
                if entry and entry["hash"] == h and entry["status"] == "ok":
                    c = _merge_llm(c, EnrichOutput(**entry["output"]), vocab_set)
            enriched.append(c)
    stats["coffees"] = write_jsonl(out_dir / "coffees.jsonl", enriched)
    return stats
