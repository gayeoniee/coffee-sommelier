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


# Korean note words the SCA Korean names (data/curated/sca_ko.yaml) don't spell the same way -> SCA tag.
# Only aliases whose target is in the vocabulary are used.
KO_TAG_ALIASES = {
    "카라멜": "caramelized", "캬라멜": "caramelized", "카카오": "cocoa", "초코": "chocolate",
    "플로럴": "floral", "벚꽃": "floral", "아카시아": "floral", "국화": "floral", "꽃": "floral",
    "자스민": "jasmine", "와이니": "winey", "건자두": "prune", "흑당": "brown sugar", "브라운슈가": "brown sugar",
    "메이플": "maple syrup", "호두": "nutty", "피스타치오": "nutty", "군밤": "nutty",
    "베르가못": "citrus fruit", "유자": "citrus fruit", "금귤": "citrus fruit", "블랙커런트": "berry",
}
_HANGUL = re.compile(r"[가-힣]")
_NOTE_SPLIT = re.compile(r"[,/·;\n]+")
MAX_NOTE_CHARS = 20

# Korean roastery note-word body cue (docs/adr/0010-body-heaviness.md): roasters_kr beans are facts-only note
# lists with no review prose to judge with an LLM (scripts/relabel_body.py needs cupping-note sentences), so
# a heaviness reading straight from the note words is the only rule-based signal available before the LLM
# enrich fallback (needs_llm) would otherwise guess from the same short text anyway.
_KO_BODY_HEAVY = re.compile(r"묵직|무거운|풀\s*바디")
# "라이트 로스트"/"라이트 배전" is a roast, not a body (same lookahead as app/core/textcues.py)
_KO_BODY_LIGHT = re.compile(r"가벼운|라이트(?!\s*(?:로스|배전))|깔끔한\s*바디")


def ko_body_cue(text: str) -> int | None:
    if _KO_BODY_HEAVY.search(text or ""):
        return 4
    if _KO_BODY_LIGHT.search(text or ""):
        return 2
    return None


def ko_tag_vocab(taxonomy: list[TaxonomyNode]) -> dict[str, str]:
    """Korean term (spaces removed) -> SCA tag, from the taxonomy's Korean names plus KO_TAG_ALIASES."""
    en = set(tag_vocab(taxonomy))
    out = {"".join(n.name_ko.split()): n.name_en.lower() for n in taxonomy if n.level >= 2 and n.name_ko}
    out.update({k: v for k, v in KO_TAG_ALIASES.items() if v in en})
    return out


def is_note_list(text: str) -> bool:
    """A short comma-separated note list ("초콜릿, 건무화과, 호두"), not prose. Korean substring matching is only
    safe on these: in prose it would catch "발효" in a process description or "나무" in "커피나무"."""
    notes = [n.strip() for n in _NOTE_SPLIT.split(text or "") if n.strip()]
    return bool(notes) and all(len(n) <= MAX_NOTE_CHARS and "." not in n for n in notes)


# Words that contain a note word but are not notes: their span is blocked like a longer hit
# ("피베리" is peaberry, a bean shape — not berry).
KO_NON_NOTES = ("피베리",)


def ko_rule_tags(text: str, ko_vocab: dict[str, str]) -> list[str]:
    """Match Korean terms inside each note, longest first; a shorter term inside a longer hit is skipped
    ("블루베리" is blueberry, not also berry), and so is one inside a KO_NON_NOTES word ("피베리").
    One-letter terms (배, 꿀, 꽃) must be the whole note."""
    hits: list[tuple[tuple[int, int], str]] = []
    terms = sorted(ko_vocab, key=len, reverse=True)
    for i, note in enumerate(_NOTE_SPLIT.split(text or "")):
        n = "".join(note.split())
        if not _HANGUL.search(n):
            continue
        spans = [(m.start(), m.end()) for w in KO_NON_NOTES for m in re.finditer(w, n)]
        for term in terms:
            if len(term) == 1:
                if n == term:
                    hits.append(((i, 0), ko_vocab[term]))
                continue
            start = n.find(term)
            while start != -1 and any(s <= start and start + len(term) <= e for s, e in spans):
                start = n.find(term, start + 1)
            if start != -1:
                spans.append((start, start + len(term)))
                hits.append(((i, start), ko_vocab[term]))
    return [tag for _, tag in sorted(hits)]


def rule_tags(text: str, vocab: list[str], limit: int = 6, ko_vocab: dict[str, str] | None = None) -> list[str]:
    t = (text or "").lower()
    hits: list[tuple[int, str]] = []
    for term in sorted(vocab, key=len, reverse=True):
        m = re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", t)
        if m and not any(term in h for _, h in hits):
            hits.append((m.start(), term))
    tags = [term for _, term in sorted(hits)]
    if ko_vocab and is_note_list(text):
        for tag in ko_rule_tags(text, ko_vocab):
            if not any(tag in h for h in tags):        # same rule as above: "berry" is covered by "blueberry"
                tags.append(tag)
    return tags[:limit]


def coffee_texts(reviews: list[ReviewRecord]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in reviews:
        out[r.coffee_key] = f"{out[r.coffee_key]}\n{r.text}" if r.coffee_key in out else r.text
    return out


def needs_llm(c: CoffeeRecord, text: str) -> bool:
    return bool(text) and (not c.flavor_tags or c.acidity is None or c.body is None)


def _apply_rules(c: CoffeeRecord, text: str, vocab: list[str], ko_vocab: dict[str, str] | None = None) -> CoffeeRecord:
    update: dict = {}
    if not c.flavor_tags and text:
        update["flavor_tags"] = rule_tags(text, vocab, ko_vocab=ko_vocab)
    if not c.is_decaf:
        is_decaf, process = detect_decaf(c.name, text)
        if is_decaf:
            update.update(is_decaf=True, decaf_process=process)
    if c.body is None and text and c.source == "roasters_kr":
        body = ko_body_cue(text)
        if body is not None:
            update["body"] = body
            update["attr_label_source"] = {**c.attr_label_source, "body": "korean_cue"}
    return c.model_copy(update=update)


def _merge_llm(c: CoffeeRecord, o: EnrichOutput, vocab: set[str]) -> CoffeeRecord:
    update = {k: getattr(o, k) for k in ("acidity", "body", "sweetness") if getattr(c, k) is None and getattr(o, k)}
    if update:
        update["attr_label_source"] = {**c.attr_label_source, **{k: "llm_review" for k in update}}
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


def _with_source(c: CoffeeRecord, attr: str, value) -> dict[str, str]:
    """c.attr_label_source after `attr` was re-labelled by the LLM heaviness judge (or cleared)."""
    out = {k: v for k, v in c.attr_label_source.items() if k != attr}
    if value is not None:
        out[attr] = "llm_review"
    return out


def apply_body_heaviness(coffees: list[CoffeeRecord], out_dir: Path) -> tuple[list[CoffeeRecord], dict[str, int]]:
    """Replace coffees.body -- a quintile of a QUALITY sub-score, not heaviness (docs/adr/0010-body-heaviness.md)
    -- with the mouthfeel-heaviness values scripts/relabel_body.py judged from each bean's own review text.

    - coffeereview_kaggle: body <- data/enriched/body_heaviness.jsonl's value for this key. A bean the relabel
      run never attempted (no review text -> never a target) keeps its old quintile value untouched; one it DID
      attempt but couldn't score (LLM call failed, or the text said nothing about weight) becomes None -- an
      honest "no signal" beats keeping a value we know measures the wrong thing.
    - cqi: body -> None always. The CQI "Body" column is a cupping QUALITY score (same defect as coffeereview's,
      and CQI ships no review prose scripts/relabel_body.py could judge instead), so there is no trustworthy
      heaviness value to give it; app/graphs/analyze_bean.py's neighbour average/attribute model cover it instead.
    - every other source: untouched (roasters_kr's Korean cue is applied earlier, in _apply_rules).
    """
    heaviness, _ = read_json_lines(out_dir / "body_heaviness.jsonl")
    by_key = {r["key"]: r for r in heaviness}          # later lines win (append-only cache)
    stats = {"body_heaviness_applied": 0, "body_heaviness_nulled": 0, "cqi_body_nulled": 0}
    out = []
    for c in coffees:
        if c.source == "coffeereview_kaggle":
            entry = by_key.get(c.key)
            if entry is None:
                out.append(c)
                continue
            body = entry.get("body") if entry.get("status") == "ok" else None
            stats["body_heaviness_applied" if body is not None else "body_heaviness_nulled"] += 1
            out.append(c.model_copy(update={"body": body, "attr_label_source": _with_source(c, "body", body)}))
        elif c.source == "cqi":
            if c.body is not None:
                stats["cqi_body_nulled"] += 1
            out.append(c.model_copy(update={"body": None, "attr_label_source": _with_source(c, "body", None)})
                       if c.body is not None else c)
        else:
            out.append(c)
    return out, stats


def run_enrich(norm_dir: Path, out_dir: Path, client, limit: int | None = None, retry_failed: bool = False) -> dict[str, int]:
    coffees = read_jsonl(norm_dir / "coffees.jsonl", CoffeeRecord)
    texts = coffee_texts(read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord))
    taxonomy = read_jsonl(norm_dir / "taxonomy.jsonl", TaxonomyNode)
    vocab, ko_vocab = tag_vocab(taxonomy), ko_tag_vocab(taxonomy)
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
            c = _apply_rules(c, text, vocab, ko_vocab)
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
    enriched, heaviness_stats = apply_body_heaviness(enriched, out_dir)
    stats.update(heaviness_stats)
    stats["coffees"] = write_jsonl(out_dir / "coffees.jsonl", enriched)
    return stats
