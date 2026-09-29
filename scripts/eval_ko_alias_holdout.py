"""Honest coverage of the ADR 0017 Korean note aliases (pipeline.enrich KO_TAG_ALIASES_0017): they were picked from
the same roastery note lists the in-sample number (57% -> 75% of Korean note items read as a tag) was measured on.
docs/adr/0018-open-tag-cooccurrence.md.

Held-out estimate: split the roasteries of data/raw/roasters_kr/beans.jsonl (Blue Bottle Korea included) in half,
"pick" from half A only the aliases a picker could have found there (the alias term occurs in an A note item the
base vocabulary -- taxonomy Korean names + the pre-0017 aliases -- leaves unread), and measure on half B the share of
Korean note items that read as >= 1 tag: base vocabulary, base + aliases picked from A, base + all 49 aliases. Every
half/half roastery split (every one) is scored; the mean and 5th-95th percentile are reported. Also: each
alias's in-sample hit count / roastery count, and the non-note words that contain one ("리치한" is "rich").

Writes data/eval/open/phase7_ko_alias_holdout.json only.

    uv run python scripts/eval_ko_alias_holdout.py
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import settings  # noqa: E402
from pipeline.enrich import _HANGUL, _KO_TAG_ALIASES_BASE, KO_TAG_ALIASES_0017, rule_tags  # noqa: E402

BEANS = settings.RAW_DIR / "roasters_kr" / "beans.jsonl"
OUT = settings.DATA_DIR / "eval" / "open" / "phase7_ko_alias_holdout.json"


def note_items(path: Path = BEANS) -> list[tuple[str, str]]:
    """(roaster, note item) for every Korean note item on the collected roastery cards."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [(r["roaster"], n.strip()) for r in rows for n in (r.get("flavor_notes") or []) if _HANGUL.search(n or "")]


def vocabularies(tag_to_cat: dict[str, str], tag_ko: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """(base Korean vocabulary: taxonomy/extra Korean names + the pre-0017 aliases, the 0017 aliases that change
    it), built like app.core.textcues.ko_vocab_from_tag_ko (aliases override names)."""
    en = set(tag_to_cat)
    base = {"".join(ko.split()): t for t, ko in tag_ko.items() if ko and t in en}
    base.update({k: v for k, v in _KO_TAG_ALIASES_BASE.items() if v in en})
    return base, {k: v for k, v in KO_TAG_ALIASES_0017.items() if v in en and base.get(k) != v}


def reads(note: str, vocab: list[str], ko_vocab: dict[str, str]) -> bool:
    return bool(rule_tags(note, vocab, ko_vocab=ko_vocab))


def alias_hits(note: str, alias: str) -> bool:
    return alias in "".join(note.split())


def main() -> int:
    from app.repo import Repo
    from scripts.train_feature_model import OPEN_URL
    repo = Repo(OPEN_URL)
    try:
        tag_to_cat, tag_ko = repo.taxonomy()
    finally:
        repo.close()
    base, aliases = vocabularies(tag_to_cat, tag_ko)
    vocab = list(tag_to_cat)
    full = {**base, **aliases}
    items = note_items()
    roasters = sorted({r for r, _ in items})
    base_ok = [reads(n, vocab, base) for _, n in items]
    full_ok = [reads(n, vocab, full) for _, n in items]

    # per alias: in-sample hits (items it newly makes readable or reads at all) and the roasteries they come from
    per_alias = {}
    for a, tag in aliases.items():
        hit = [(r, n) for (r, n), ok in zip(items, base_ok) if not ok and alias_hits(n, a)]
        per_alias[a] = {"tag": tag, "unread_items_hit": len(hit), "roasteries": sorted({r for r, _ in hit}),
                        "examples": sorted({n for _, n in hit})[:5]}

    splits = []
    for half in combinations(roasters, len(roasters) // 2):
        a_set = set(half)
        picked = {a: t for a, t in aliases.items()
                  if any(r in a_set and not ok and alias_hits(n, a) for (r, n), ok in zip(items, base_ok))}
        vb = {**base, **picked}
        test = [n for r, n in items if r not in a_set]
        if not test:
            continue
        splits.append({
            "train": sorted(a_set), "n_test": len(test), "n_picked": len(picked),
            "base": sum(reads(n, vocab, base) for n in test) / len(test),
            "picked": sum(reads(n, vocab, vb) for n in test) / len(test),
            "all49": sum(reads(n, vocab, full) for n in test) / len(test)})

    def stat(k):
        v = np.array([s[k] for s in splits])
        return {"mean": round(float(v.mean()), 4), "p5": round(float(np.percentile(v, 5)), 4),
                "p95": round(float(np.percentile(v, 95)), 4)}

    # words that contain an alias but are not that note (checked by hand on the hit contexts below)
    ambiguous = {"리치": "'리치한 바디'/'리치함' = rich (not lychee) -- blocked via KO_NON_NOTES"}
    ctx = defaultdict(list)
    for r, n in items:
        for a in aliases:
            if alias_hits(n, a):
                ctx[a].append(n)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_items": len(items), "roasteries": {r: sum(1 for x, _ in items if x == r) for r in roasters},
        "n_aliases": len(aliases),
        "in_sample": {"base": round(sum(base_ok) / len(items), 4), "all49": round(sum(full_ok) / len(items), 4)},
        "held_out": {"n_splits": len(splits), "base": stat("base"), "picked_from_other_half": stat("picked"),
                     "all49": stat("all49"), "n_picked": stat("n_picked"),
                     "gain_picked": round(float(np.mean([s["picked"] - s["base"] for s in splits])), 4),
                     "gain_all49": round(float(np.mean([s["all49"] - s["base"] for s in splits])), 4)},
        "aliases_never_in_notes": sorted(a for a, v in per_alias.items() if not v["unread_items_hit"]),
        "aliases_one_roastery": sorted(a for a, v in per_alias.items() if len(v["roasteries"]) == 1),
        "per_alias": per_alias, "ambiguous": ambiguous,
        "contexts": {a: sorted(set(v))[:8] for a, v in sorted(ctx.items())},
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("n_items", "n_aliases", "in_sample", "held_out",
                                            "aliases_never_in_notes", "aliases_one_roastery")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
