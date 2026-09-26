"""깨진 상대 링크 검사: 마크다운의 상대 링크·이미지 경로가 실제로 있는지 확인한다.

사용법: `uv run python scripts/check_links.py [paths...] [--root DIR]`
경로를 주지 않으면 README.md, web/README.md, docs/ 아래 마크다운(docs/superpowers 제외)을 본다.
종료 코드 0 = 깨진 링크 없음, 1 = 깨진 링크 목록 출력.

- 외부 링크(http·https·mailto 등), 문서 안 앵커(#...)는 건너뛴다. `#앵커`·`?쿼리`는 떼고 파일만 본다.
- 코드 블록(```)·인라인 코드(`...`) 안의 링크는 예시라 건너뛴다.
- 저장소 루트 밖으로 나가는 경로(예: GitHub 웹 경로 `../../tree/wine-v1`)는 로컬 파일이 아니라 건너뛴다.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MD_LINK = re.compile(r"!?\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
HTML_SRC = re.compile(r"""<(?:img|a|source)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def default_paths(root: Path = ROOT) -> list[Path]:
    paths = [root / "README.md", root / "web" / "README.md"]
    paths += [p for p in sorted((root / "docs").rglob("*.md")) if "superpowers" not in p.relative_to(root).parts]
    return [p for p in paths if p.exists()]


def _links(text: str) -> list[str]:
    out, in_fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = INLINE_CODE.sub("", line)
        out += [m.group(1).strip("<>") for m in MD_LINK.finditer(line)]
        out += [m.group(1) for m in HTML_SRC.finditer(line)]
    return out


def find_broken(paths: list[Path], root: Path = ROOT) -> list[tuple[Path, str]]:
    root = root.resolve()
    broken = []
    for md in paths:
        md = Path(md).resolve()
        for link in _links(md.read_text(encoding="utf-8")):
            if not link or link.startswith("#") or SCHEME.match(link) or link.startswith("//"):
                continue
            target = re.split(r"[#?]", link, maxsplit=1)[0]
            if not target:
                continue
            base = root if target.startswith("/") else md.parent
            resolved = (base / target.lstrip("/")).resolve()
            if not resolved.is_relative_to(root):
                continue                            # GitHub web path (e.g. ../../tree/<tag>), not a local file
            if not resolved.exists():
                broken.append((md, link))
    return broken


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--root", type=Path, default=ROOT)
    a = ap.parse_args(argv)
    paths = a.paths or default_paths(a.root)
    broken = find_broken(paths, root=a.root)
    for md, link in broken:
        try:
            shown = md.relative_to(a.root.resolve())
        except ValueError:
            shown = md
        print(f"BROKEN {shown}: {link}")
    print(f"{len(paths)} files checked, {len(broken)} broken links")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
