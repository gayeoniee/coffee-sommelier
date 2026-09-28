"""Rewrite the data-dependent numbers in README.md and the competition draft from the eval JSON, so a data refresh
never leaves stale numbers behind (docs/adr/0015-automated-refresh.md).

README: every value matched by one of scripts/check_readme_numbers.py's DATA checks (violations, menu counts,
coverage, LOO) is replaced with the JSON value -- the same regexes that check them, so after this the check passes
by construction for those; the other checks (explain quality, bench, compare3, convergence) come from evals a data
refresh does not re-run and are left alone. Draft: the block between the numbers markers is re-rendered with
scripts/competition/render_numbers.py (the open variant's data/eval/open).

Usage: uv run python -m scripts.refresh.update_numbers [--readme README.md] [--eval-dir data/eval]
       [--draft docs/competition/data-recipe-draft.md] [--open-eval-dir data/eval/open]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from scripts.check_readme_numbers import _checks
from scripts.competition.render_numbers import NUMBERS_END, NUMBERS_START
from scripts.competition.render_numbers import main as render_numbers

ROOT = Path(__file__).resolve().parents[2]
DATA_CHECKS = {"violations", "menu_brand_count", "menu_total", "coverage_total", "coverage_decaf", "coverage_tagged",
               "loo_acidity", "loo_body", "loo_tag_f1", "loo_category_f1", "loo_open_acidity", "loo_open_body"}


def _is_data_check(name: str) -> bool:
    return name in DATA_CHECKS or name.startswith("menu_")


def _fmt(found: str, want) -> str:
    if isinstance(want, bool) or want is None:
        return found
    if isinstance(want, float) and ("." in found or not want.is_integer()):
        return repr(round(want, 4))
    n = int(want)
    return f"{n:,}" if "," in found else str(n)


def update_readme(readme: str, eval_dir: Path) -> tuple[str, int]:
    """Returns (new text, number of values changed)."""
    cache: dict[str, dict | None] = {}

    def load(name: str) -> dict | None:
        if name not in cache:
            p = Path(eval_dir) / name
            cache[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        return cache[name]

    edits: dict[tuple[int, int], str] = {}
    for c in _checks():
        if not _is_data_check(c.name) or any(load(f) is None for f in c.files):
            continue
        expected = c.expected({f: load(f) for f in c.files})
        for pat in c.patterns:
            for m in re.finditer(pat, readme):
                for i, want in enumerate(expected, start=1):
                    if i > (m.re.groups or 0) or m.group(i) is None:
                        continue
                    edits[m.span(i)] = _fmt(m.group(i), want)
    changed = 0
    out = readme
    for (a, b), text in sorted(edits.items(), reverse=True):
        if out[a:b] != text:
            out = out[:a] + text + out[b:]
            changed += 1
    return out, changed


def update_draft(draft: str, open_eval_dir: Path) -> str:
    """The draft wraps the rendered block (which carries its own markers) in a second pair of markers."""
    a = draft.index(NUMBERS_START)
    b = draft.index(NUMBERS_START, a + len(NUMBERS_START))
    e = draft.index(NUMBERS_END, b) + len(NUMBERS_END)
    return draft[:b] + render_numbers(open_eval_dir).rstrip("\n") + draft[e:]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--readme", type=Path, default=ROOT / "README.md")
    ap.add_argument("--eval-dir", type=Path, default=ROOT / "data" / "eval")
    ap.add_argument("--draft", type=Path, default=ROOT / "docs" / "competition" / "data-recipe-draft.md")
    ap.add_argument("--open-eval-dir", type=Path, default=ROOT / "data" / "eval" / "open")
    a = ap.parse_args(argv)
    text, n = update_readme(a.readme.read_text(encoding="utf-8"), a.eval_dir)
    a.readme.write_text(text, encoding="utf-8", newline="\n")
    draft = a.draft.read_text(encoding="utf-8")
    new_draft = update_draft(draft, a.open_eval_dir)
    a.draft.write_text(new_draft, encoding="utf-8", newline="\n")
    print(f"README: {n}개 수치 갱신, 초안 수치 블록: {'갱신' if new_draft != draft else '변화 없음'}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
