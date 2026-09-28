"""Compose docs/competition/images/05_product_card.png from live-site card screenshots (web/scripts/card-shot.mjs).

Each card is cropped to its top (name · score · caffeine/decaf chips · taste bars) and its evidence footer; the
LLM explanation paragraph in between is left out on purpose (the explanation wording was still being fixed when the
screenshots were taken) and marked "(설명 문장 생략)".

Usage: uv run python scripts/competition/card_composite.py OUT.png SHOT.png:TOP_END:EVIDENCE_START [...]
  TOP_END / EVIDENCE_START are pixel rows in the screenshot (2x device scale).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

GAP = 24
NOTE_H = 44
BG = (250, 246, 240)
MUTED = (120, 110, 100)


def _font(size: int):
    for name in ("malgun.ttf", "C:/Windows/Fonts/malgun.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def crop_card(path: Path, top_end: int, evidence_start: int) -> Image.Image:
    img = Image.open(path).convert("RGB")
    top = img.crop((0, 0, img.width, top_end))
    bottom = img.crop((0, evidence_start, img.width, img.height))
    out = Image.new("RGB", (img.width, top.height + NOTE_H + bottom.height), (255, 253, 250))
    out.paste(top, (0, 0))
    d = ImageDraw.Draw(out)
    y = top.height + NOTE_H // 2
    d.line((36, y, img.width - 36, y), fill=(220, 210, 200), width=2)
    note = "(설명 문장 생략)"
    f = _font(22)
    w = d.textlength(note, font=f)
    d.rectangle(((img.width - w) / 2 - 10, y - 16, (img.width + w) / 2 + 10, y + 16), fill=(255, 253, 250))
    d.text(((img.width - w) / 2, y - 14), note, fill=MUTED, font=f)
    out.paste(bottom, (0, top.height + NOTE_H))
    return out


def compose(out_path: Path, specs: list[tuple[Path, int, int]]) -> Path:
    cards = [crop_card(p, a, b) for p, a, b in specs]
    h = max(c.height for c in cards)
    w = sum(c.width for c in cards) + GAP * (len(cards) + 1)
    canvas = Image.new("RGB", (w, h + 2 * GAP), BG)
    x = GAP
    for c in cards:
        canvas.paste(c, (x, GAP))
        x += c.width + GAP
    canvas.save(out_path, optimize=True)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    specs = []
    for arg in sys.argv[2:]:
        p, a, b = arg.rsplit(":", 2)
        specs.append((Path(p), int(a), int(b)))
    print(compose(Path(sys.argv[1]), specs))
