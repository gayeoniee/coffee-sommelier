"""Check docs/competition/data-recipe-draft.md against the submission portal's rules:

- Sections "### A-2" .. "### A-6" must each have >= 300 non-whitespace characters in their body.
- Report how many 【작성 필요】 (still-to-fill-in) markers remain.
- The generated-numbers fragment (<!-- numbers:start -->...<!-- numbers:end -->) must be present,
  so the numeric claims in the draft come from render_numbers.py, not from hand-typed numbers.

Usage: uv run python scripts/competition/check_draft.py docs/competition/data-recipe-draft.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_SECTIONS = ("A-2", "A-3", "A-4", "A-5", "A-6")
MIN_CHARS = 300
TODO_MARKER = "【작성 필요"
NUMBERS_START = "<!-- numbers:start -->"
NUMBERS_END = "<!-- numbers:end -->"

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.*)$", re.MULTILINE)
_WS_RE = re.compile(r"\s+")


def non_ws_len(text: str) -> int:
    """Character count with all whitespace (spaces, tabs, newlines) removed."""
    return len(_WS_RE.sub("", text))


def count_todo(text: str) -> int:
    return text.count(TODO_MARKER)


def has_numbers_markers(text: str) -> bool:
    return NUMBERS_START in text and NUMBERS_END in text


def parse_sections(text: str, keys: tuple[str, ...] = REQUIRED_SECTIONS) -> dict[str, str]:
    """Extract the body text of each "### <key>. ..." heading, up to the next ## or ### heading."""
    headings = list(_HEADING_RE.finditer(text))
    sections: dict[str, str] = {}
    for idx, m in enumerate(headings):
        level, title = m.group(1), m.group(2)
        if level != "###":
            continue
        for key in keys:
            if title == key or title.startswith(key + ".") or title.startswith(key + " "):
                start = m.end()
                end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
                sections[key] = text[start:end]
                break
    return sections


def check(text: str) -> dict:
    sections = parse_sections(text)
    lengths = {key: non_ws_len(sections.get(key, "")) for key in REQUIRED_SECTIONS}
    missing = [key for key in REQUIRED_SECTIONS if key not in sections]
    short = {key: n for key, n in lengths.items() if n < MIN_CHARS}
    return {
        "lengths": lengths,
        "missing_sections": missing,
        "short_sections": short,
        "todo_count": count_todo(text),
        "has_numbers_markers": has_numbers_markers(text),
    }


def report(result: dict) -> str:
    lines = ["check_draft 결과"]
    for key in REQUIRED_SECTIONS:
        n = result["lengths"].get(key, 0)
        mark = "OK" if n >= MIN_CHARS else "FAIL"
        lines.append(f"  {key}: {n}자 (공백 제외) [{mark}]")
    if result["missing_sections"]:
        lines.append(f"  누락된 섹션: {', '.join(result['missing_sections'])}")
    lines.append(f"  남은 【작성 필요】 개수: {result['todo_count']}")
    marker_mark = "OK" if result["has_numbers_markers"] else "FAIL"
    lines.append(f"  numbers 마커: [{marker_mark}]")
    return "\n".join(lines)


def main(path: str | Path) -> int:
    text = Path(path).read_text(encoding="utf-8")
    result = check(text)
    print(report(result))
    ok = not result["missing_sections"] and not result["short_sections"] and result["has_numbers_markers"]
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: check_draft.py <path-to-draft.md>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
