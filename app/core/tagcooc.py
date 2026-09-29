"""Open-variant flavor-tag co-occurrence (docs/adr/0018-open-tag-cooccurrence.md).

When the guest typed a note word ("흙내", "캐러멜") the shown tags are that word first, then the neighbour vote -- which
on the open pool often adds nothing (the tagged neighbours disagree). The co-occurrence table answers "beans whose
notes say A: how many also say B?" over the licence-clean open beans with notes (roasters_kr, shopify,
shopify_gauged -- never coffeereview, RoasterDB or Zenodo), and tops the shown tags up with the best-supported B:
"'캐러멜' 표기 원두 38개 중 20개가 '초콜릿'도 언급".

The table (config/tag_cooc_open.json) has one writer: scripts/build_tag_cooc.py. The gate constants below were picked
by scripts/eval_open_tag_cooc.py (E1 roaster-held-out CV, E2 Zenodo panel).
"""
import json
import logging
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

COOC_FILE = "tag_cooc_open.json"
COOC_TARGET = 2          # top the shown tags up to this many (echo included)
COOC_MIN_SUPPORT = 5     # beans carrying the guest's tag A
COOC_MIN_PAIR = 3        # beans carrying both A and B
COOC_MIN_SHARE = 0.3     # n(A, B) / n(A)
COOC_MIN_LIFT = 1.2      # share / base rate of B among the table's beans
_warned_missing = False


@dataclass(frozen=True)
class TagCooc:
    n_beans: int
    tag_count: dict[str, int]
    pair_count: dict[str, dict[str, int]]
    parent: dict[str, str]          # level-3 SCA tag -> its level-2 node (same-branch tags are never added)

    @classmethod
    def from_beans(cls, beans: Iterable[Iterable[str]], parent: dict[str, str] | None = None) -> "TagCooc":
        sets = [s for s in ({t.lower() for t in b} for b in beans) if s]
        tag_count = Counter(t for s in sets for t in s)
        pairs: dict[str, Counter] = {}
        for s in sets:
            for a in s:
                pairs.setdefault(a, Counter()).update(b for b in s if b != a)
        return cls(len(sets), dict(tag_count), {a: dict(c) for a, c in pairs.items()}, dict(parent or {}))

    @classmethod
    def from_doc(cls, doc: dict) -> "TagCooc":
        return cls(int(doc["n_beans"]), {k: int(v) for k, v in doc["tag_count"].items()},
                   {a: {b: int(n) for b, n in bs.items()} for a, bs in doc["pair_count"].items()},
                   dict(doc.get("parent", {})))

    def to_doc(self) -> dict:
        return {"n_beans": self.n_beans, "tag_count": dict(sorted(self.tag_count.items())),
                "pair_count": {a: dict(sorted(bs.items())) for a, bs in sorted(self.pair_count.items())},
                "parent": dict(sorted(self.parent.items()))}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TagCooc | None":
        """config/tag_cooc_open.json (or `path`); missing/unreadable -> None (logged once)."""
        global _warned_missing
        if path is None:
            from pipeline import settings
            path = settings.CONFIG_DIR / COOC_FILE
        p = Path(path)
        try:
            return cls.from_doc(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError) as e:
            if not _warned_missing:
                log.warning("tag co-occurrence table at %s not loaded (%s); no co-occurrence fill", p, e)
                _warned_missing = True
            return None

    def related(self, a: str, b: str) -> bool:
        """Same SCA branch (one is the other's level-2 node): "초콜릿" + "코코아" says nothing new."""
        return self.parent.get(a) == b or self.parent.get(b) == a

    def candidates(self, given: Iterable[str], shown: Iterable[str] = (), min_support: int = COOC_MIN_SUPPORT,
                   min_pair: int = COOC_MIN_PAIR, min_share: float = COOC_MIN_SHARE,
                   min_lift: float = COOC_MIN_LIFT) -> list[tuple[str, str, int, int]]:
        """Tags B that clear the gate for some given tag A, best first: [(B, A, n(A), n(A, B))], A being the given
        tag with the highest share for that B. B is never already shown nor on the same branch as a shown tag."""
        given = [t.lower() for t in given]
        shown = {t.lower() for t in shown} | set(given)
        best: dict[str, tuple[float, float, str, int, int]] = {}
        for a in given:
            n_a = self.tag_count.get(a, 0)
            if n_a < min_support:
                continue
            for b, n_ab in self.pair_count.get(a, {}).items():
                if b in shown or any(self.related(b, s) for s in shown) or n_ab < min_pair:
                    continue
                share = n_ab / n_a
                lift = share / (self.tag_count.get(b, 0) / self.n_beans) if self.tag_count.get(b) else 0.0
                if share < min_share or lift < min_lift:
                    continue
                if b not in best or (share, lift) > best[b][:2]:
                    best[b] = (share, lift, a, n_a, n_ab)
        ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
        return [(b, a, n_a, n_ab) for b, (_, _, a, n_a, n_ab) in ranked]


def cooc_line(a: str, b: str, n_a: int, n_ab: int, tag_ko: dict[str, str] | None = None) -> str:
    ko = tag_ko or {}
    return f"'{ko.get(a, a)}' 표기 원두 {n_a}개 중 {n_ab}개가 '{ko.get(b, b)}'도 언급"


def with_cooc_fill(pred, given: list[str], cooc: "TagCooc | None", tag_ko: dict[str, str] | None = None,
                   target: int = COOC_TARGET):
    """Top `pred.tags` (the guest's words already first) up to `target` from the co-occurrence of the guest's own
    tags `given`, one evidence line per added tag, placed after the "문구의 향미" line. Never drops or reorders a
    tag; attributes/confidence/n_neighbors untouched. No given tag, no table or enough tags -> `pred` unchanged."""
    if cooc is None or not given or len(pred.tags) >= target:
        return pred
    added = cooc.candidates(given, pred.tags)[:target - len(pred.tags)]
    if not added:
        return pred
    lines = [cooc_line(a, b, n_a, n_ab, tag_ko) for b, a, n_a, n_ab in added]
    at = next((i + 1 for i, e in enumerate(pred.evidence) if e.startswith("문구의 향미")), 0)
    evidence = [*pred.evidence[:at], *lines, *pred.evidence[at:]]
    return replace(pred, tags=[*pred.tags, *(b for b, *_ in added)], evidence=evidence)
