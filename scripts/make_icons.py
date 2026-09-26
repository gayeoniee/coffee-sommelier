"""Generate the PWA app icons for the web app.

Draws a simple coffee-cup silhouette (shapes only, no font/text dependency) in
the app's own palette: a cream (#faf6f0) background, an espresso (#2b1d14)
cup body, and a roast (#6f4e37) handle.

Usage:
    uv run python scripts/make_icons.py

Writes:
    web/public/icons/icon-192.png
    web/public/icons/icon-512.png
    web/public/icons/maskable-512.png
    web/public/icons/apple-touch-icon.png
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "public" / "icons"

CREAM = (0xFA, 0xF6, 0xF0, 255)
ESPRESSO = (0x2B, 0x1D, 0x14, 255)
ROAST = (0x6F, 0x4E, 0x37, 255)


def draw_cup(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    """Draw a rounded coffee-cup silhouette (body + handle) inside the given box."""
    w = x1 - x0
    h = y1 - y0

    # Cup body: rounded rectangle occupying the left ~68% of the box.
    cup_w = w * 0.68
    cup_x0 = x0
    cup_x1 = x0 + cup_w
    cup_y0 = y0 + h * 0.08
    cup_y1 = y0 + h * 0.92
    radius = cup_w * 0.22
    draw.rounded_rectangle([cup_x0, cup_y0, cup_x1, cup_y1], radius=radius, fill=ESPRESSO)

    # Handle: an open ring to the right of the cup body.
    handle_cx = cup_x1 + (w - cup_w) * 0.42
    handle_cy = y0 + h * 0.5
    handle_rx = (w - cup_w) * 0.55
    handle_ry = h * 0.22
    stroke_w = max(2, round(cup_w * 0.14))
    draw.ellipse(
        [handle_cx - handle_rx, handle_cy - handle_ry, handle_cx + handle_rx, handle_cy + handle_ry],
        outline=ROAST,
        width=stroke_w,
    )


def make_icon(size: int, safe_margin: float, out_path: Path, opaque: bool = False) -> None:
    mode = "RGB" if opaque else "RGBA"
    bg = CREAM[:3] if opaque else CREAM
    img = Image.new(mode, (size, size), bg)
    draw = ImageDraw.Draw(img)
    pad = size * safe_margin
    draw_cup(draw, pad, pad, size - pad, size - pad)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"wrote {out_path} ({size}x{size}, margin={safe_margin})")


def main() -> None:
    # Regular icons: some breathing room, but not required to respect a strict
    # OS-masking safe zone.
    make_icon(192, 0.09, OUT_DIR / "icon-192.png")
    make_icon(512, 0.09, OUT_DIR / "icon-512.png")
    # Maskable icon: content must fit inside the central 80% safe area, i.e.
    # at least 10% padding on every side.
    make_icon(512, 0.1, OUT_DIR / "maskable-512.png")
    # apple-touch-icon: iOS ignores alpha, so render opaque.
    make_icon(180, 0.09, OUT_DIR / "apple-touch-icon.png", opaque=True)


if __name__ == "__main__":
    main()
