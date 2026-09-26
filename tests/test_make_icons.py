"""Tests for scripts/make_icons.py.

Focus: the maskable icon must keep its drawn content inside the maskable-icon
"safe zone" — the circle of radius 0.4 * size inscribed in the icon, centered
on the icon. Anything outside that circle can be clipped away by an OS mask
(circle, squircle, rounded-square, ...), so the cup silhouette must not reach it.
"""
import math
from pathlib import Path

from PIL import Image

from scripts.make_icons import CREAM, OUT_DIR, draw_cup, make_icon


def _max_content_radius(img: Image.Image) -> float:
    """Max distance from center over all pixels that differ from the background."""
    size = img.size[0]
    assert img.size == (size, size)
    cx = cy = size / 2
    bg = img.convert("RGBA").getpixel((0, 0))
    px = img.convert("RGBA").load()
    max_r = 0.0
    for y in range(size):
        for x in range(size):
            if px[x, y] != bg:
                r = math.hypot(x - cx, y - cy)
                if r > max_r:
                    max_r = r
    return max_r


def test_maskable_icon_generation_stays_within_safe_zone(tmp_path):
    size = 512
    out = tmp_path / "maskable-512.png"
    make_icon(size, 0.17, out)
    img = Image.open(out)
    max_r = _max_content_radius(img)
    assert max_r <= 0.4 * size + 1


def test_committed_maskable_icon_stays_within_safe_zone():
    path = OUT_DIR / "maskable-512.png"
    assert path.exists(), f"missing {path}, run: uv run python scripts/make_icons.py"
    img = Image.open(path)
    size = img.size[0]
    max_r = _max_content_radius(img)
    assert max_r <= 0.4 * size + 1


def test_a_too_small_margin_would_violate_the_safe_zone(tmp_path):
    """Sanity check for the test itself: the old 0.1 margin (pre-fix) fails."""
    size = 512
    out = tmp_path / "unsafe.png"
    make_icon(size, 0.1, out)
    img = Image.open(out)
    max_r = _max_content_radius(img)
    assert max_r > 0.4 * size + 1
