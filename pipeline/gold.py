import csv
import random
from pathlib import Path

from pipeline.enrich import EnrichOutput, coffee_texts
from pipeline.llm import LLMError
from pipeline.records import CoffeeRecord, ReviewRecord, read_jsonl

SCORES = ("acidity", "body", "sweetness")
GOLD_COLUMNS = (["key", "name", "text"] + [f"pred_{s}" for s in SCORES] + ["pred_is_decaf", "pred_tags"]
                + [f"gold_{s}" for s in SCORES] + ["gold_is_decaf", "gold_tags"])
MAX_DECAF = 10


def sample_gold(enriched_dir: Path, norm_dir: Path, out_path: Path, n: int = 50, seed: int = 42) -> int:
    if out_path.exists():
        raise FileExistsError(f"{out_path} exists; move it away before resampling (it may hold labels)")
    texts = coffee_texts(read_jsonl(norm_dir / "reviews.jsonl", ReviewRecord))
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
            row.update(key=c.key, name=c.name, text=texts[c.key][:2000],
                       pred_is_decaf="1" if c.is_decaf else "0", pred_tags="; ".join(c.flavor_tags),
                       **{f"pred_{s}": "" if getattr(c, s) is None else str(getattr(c, s)) for s in SCORES})
            w.writerow(row)
    return len(picked)


def _tags(s: str) -> set[str]:
    return {t.strip().lower() for t in s.split(";") if t.strip()}


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def score_gold(path: Path) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    out: dict = {}
    for s in SCORES:
        pairs = [(r[f"pred_{s}"], int(r[f"gold_{s}"])) for r in rows if r[f"gold_{s}"].strip()]
        exact = [1.0 if p.strip() and int(p) == g else 0.0 for p, g in pairs]
        within = [1.0 if p.strip() and abs(int(p) - g) <= 1 else 0.0 for p, g in pairs]
        out[s] = {"n": len(pairs), "exact": _mean(exact), "within1": _mean(within)}
    dec = [(r["pred_is_decaf"].strip() == "1", r["gold_is_decaf"].strip() in ("1", "true", "yes", "y"))
           for r in rows if r["gold_is_decaf"].strip()]
    out["is_decaf"] = {"n": len(dec), "accuracy": _mean([1.0 if p == g else 0.0 for p, g in dec])}
    jac = []
    for r in rows:
        gold = _tags(r["gold_tags"])
        if gold:
            pred = _tags(r["pred_tags"])
            jac.append(len(pred & gold) / len(pred | gold))
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
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
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
