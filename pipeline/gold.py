import csv
import random
from pathlib import Path

from pipeline.enrich import EnrichOutput, coffee_texts, read_json_lines, rule_tags, tag_vocab
from pipeline.llm import LLMError
from pipeline.records import CoffeeRecord, ReviewRecord, TaxonomyNode, read_jsonl

SCORES = ("acidity", "body", "sweetness")
ORIGIN_FIELDS = SCORES + ("tags",)
GOLD_COLUMNS = (["key", "name", "text"] + [f"pred_{s}" for s in SCORES] + ["pred_is_decaf", "pred_tags"]
                + [f"origin_{f}" for f in ORIGIN_FIELDS]
                + [f"gold_{s}" for s in SCORES] + ["gold_is_decaf", "gold_tags"])
MAX_DECAF = 10
TEXT_CHARS = 3000  # what enrich sends to the LLM


def _origin(source_value, enriched_value, by_rule: bool, llm_ok: bool) -> str:
    """Where a predicted value came from: source data, enrich rules, an ok LLM answer, or nowhere.
    'unknown' flags a value that none of those explains (e.g. a stale cache)."""
    if source_value:
        return "source"
    if not enriched_value:
        return "none"
    if by_rule:
        return "rule"
    return "llm" if llm_ok else "unknown"


def sample_gold(enriched_dir: Path, norm_dir: Path, out_path: Path, n: int = 50, seed: int = 42) -> int:
    if out_path.exists():
        raise FileExistsError(f"{out_path} exists; move it away before resampling (it may hold labels)")
    texts = coffee_texts(read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord))
    source = {c.key: c for c in read_jsonl(norm_dir / "coffees.jsonl", CoffeeRecord)}
    vocab = tag_vocab(read_jsonl(norm_dir / "taxonomy.jsonl", TaxonomyNode))
    cache = {e["key"]: e for e in read_json_lines(enriched_dir / "cache.jsonl")[0]}  # later lines win
    llm_ok = {k for k, e in cache.items() if e.get("status") == "ok"}
    pool = [c for c in read_jsonl(enriched_dir / "coffees.jsonl", CoffeeRecord) if texts.get(c.key)]
    rng = random.Random(seed)
    decaf = [c for c in pool if c.is_decaf]
    picked = rng.sample(decaf, min(MAX_DECAF, len(decaf), n))
    rest = [c for c in pool if not c.is_decaf]
    picked += rng.sample(rest, min(n - len(picked), len(rest)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        for c in picked:
            row = {col: "" for col in GOLD_COLUMNS}
            src = source.get(c.key)
            for s in SCORES:
                row[f"origin_{s}"] = _origin(src and getattr(src, s), getattr(c, s), False, c.key in llm_ok)
            row["origin_tags"] = _origin(src and src.flavor_tags, c.flavor_tags,
                                         bool(rule_tags(texts[c.key], vocab)), c.key in llm_ok)
            row.update(key=c.key, name=c.name, text=texts[c.key][:TEXT_CHARS],
                       pred_is_decaf="1" if c.is_decaf else "0", pred_tags="; ".join(c.flavor_tags),
                       **{f"pred_{s}": "" if getattr(c, s) is None else str(getattr(c, s)) for s in SCORES})
            w.writerow(row)
    return len(picked)


def read_gold_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def clone_unlabelled(src: Path, dst: Path) -> int:
    """Copy a gold CSV with every gold_* cell emptied, so a second judge starts from scratch."""
    if dst.exists():
        raise FileExistsError(f"{dst} exists; move it away before cloning")
    rows = read_gold_rows(src)
    for r in rows:
        for field in GOLD_FIELDS:
            r[field] = ""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _tags(s: str) -> set[str]:
    return {t.strip().lower() for t in s.split(";") if t.strip()}


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _int(cell: str | None) -> int | None:
    try:
        return int(float(cell))
    except (TypeError, ValueError, OverflowError):
        return None


def _group(items: list[tuple[str, object]]) -> dict[str, list]:
    groups: dict[str, list] = {}
    for key, value in items:
        groups.setdefault(key, []).append(value)
    return groups


def _score_stats(pairs: list[tuple[int | None, int]]) -> dict:
    exact = [1.0 if p is not None and p == g else 0.0 for p, g in pairs]
    within = [1.0 if p is not None and abs(p - g) <= 1 else 0.0 for p, g in pairs]
    return {"n": len(pairs), "exact": _mean(exact), "within1": _mean(within)}


def score_gold(path: Path) -> dict:
    rows = read_gold_rows(path)
    out: dict = {}
    by_origin: dict = {}
    for s in SCORES:
        scored = [(r.get(f"origin_{s}") or "unknown", _int(r[f"pred_{s}"]), _int(r[f"gold_{s}"])) for r in rows]
        scored = [(o, p, g) for o, p, g in scored if g is not None]  # unlabelled or unparsable gold cells
        out[s] = _score_stats([(p, g) for _, p, g in scored])
        by_origin[s] = {o: _score_stats(ps) for o, ps in _group([(o, (p, g)) for o, p, g in scored]).items()}
    dec = [(r["pred_is_decaf"].strip() == "1", r["gold_is_decaf"].strip() in ("1", "true", "yes", "y"))
           for r in rows if r["gold_is_decaf"].strip()]
    out["is_decaf"] = {"n": len(dec), "accuracy": _mean([1.0 if p == g else 0.0 for p, g in dec])}
    jac: list[tuple[str, float]] = []
    for r in rows:
        gold = _tags(r["gold_tags"])
        if gold:
            pred = _tags(r["pred_tags"])
            jac.append((r.get("origin_tags") or "unknown", len(pred & gold) / len(pred | gold)))
    out["tags"] = {"n": len(jac), "jaccard": _mean([j for _, j in jac])}
    by_origin["tags"] = {o: {"n": len(js), "jaccard": _mean(js)} for o, js in _group(jac).items()}
    out["by_origin"] = by_origin
    return out


def agreement(path_a: Path, path_b: Path) -> dict:
    """How much two independently-labelled gold sets agree, joined by key."""
    a = {r["key"]: r for r in read_gold_rows(path_a)}
    b = {r["key"]: r for r in read_gold_rows(path_b)}
    keys = [k for k in a if k in b]
    out: dict = {}
    for s in SCORES:
        pairs = [(_int(a[k][f"gold_{s}"]), _int(b[k][f"gold_{s}"])) for k in keys]
        pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
        out[s] = {"n": len(pairs),
                  "exact": _mean([1.0 if x == y else 0.0 for x, y in pairs]),
                  "within1": _mean([1.0 if abs(x - y) <= 1 else 0.0 for x, y in pairs])}
    truthy = ("1", "true", "yes", "y")
    dec = [(a[k]["gold_is_decaf"].strip(), b[k]["gold_is_decaf"].strip()) for k in keys]
    dec = [(x in truthy, y in truthy) for x, y in dec if x and y]
    out["is_decaf"] = {"n": len(dec), "agreement": _mean([1.0 if x == y else 0.0 for x, y in dec])}
    jac = [(_tags(a[k]["gold_tags"]), _tags(b[k]["gold_tags"])) for k in keys]
    jac = [len(x & y) / len(x | y) for x, y in jac if x and y]
    out["tags"] = {"n": len(jac), "jaccard": _mean(jac)}
    return out


class GoldLabel(EnrichOutput):
    is_decaf: bool = False


LABEL_SYSTEM = "You are an expert coffee cupper labelling an evaluation set. Answer from the tasting text only. Reply with one JSON object."
LABEL_PROMPT = """Coffee: {name}
Tasting text:
{text}

Allowed flavor tags: {vocab}

Return JSON: {{"flavor_tags": [up to 6 allowed tags], "acidity": 1-5 or null, "body": 1-5 or null, "sweetness": 1-5 or null, "is_decaf": true or false}}
Scale: 1 = very low, 3 = moderate, 5 = very high. Use null when the text gives no evidence."""
GOLD_FIELDS = [f"gold_{s}" for s in SCORES] + ["gold_is_decaf", "gold_tags"]


def label_gold(path: Path, client, vocab: list[str]) -> int:
    rows = read_gold_rows(path)
    vocab_set = set(vocab)
    labelled = 0
    for r in rows:
        if any(r[f].strip() for f in GOLD_FIELDS):
            continue  # a human (or an earlier run) already labelled this row
        messages = [{"role": "system", "content": LABEL_SYSTEM},
                    {"role": "user", "content": LABEL_PROMPT.format(name=r["name"], text=r["text"], vocab=", ".join(vocab))}]
        try:
            o = client.chat_json(messages, GoldLabel)
        except LLMError:
            continue
        for s in SCORES:
            r[f"gold_{s}"] = "" if getattr(o, s) is None else str(getattr(o, s))
        r["gold_is_decaf"] = "1" if o.is_decaf else "0"
        r["gold_tags"] = "; ".join(t.lower() for t in o.flavor_tags if t.lower() in vocab_set)
        labelled += 1
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return labelled
