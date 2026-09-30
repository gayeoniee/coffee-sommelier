"""Deterministic pixel reader for Momos Coffee's (momos.co.kr) taste-chart image.

Momos (an imweb shop) bakes its 산미(acidity)/무게감(body)/로스팅레벨(roast) gauges into
a single tall PNG per product (``<template id="prodDetailPC"><p data-gallery><img
class="fr-dib" src="...">``) instead of publishing them as page text or markup --
see the "never read from an image" note in ``pipeline.collect.roasters_kr``. This
module is the one deliberate, narrow exception: the chart is a *fixed template*
(same canvas width, same two colors, same segment geometry on every product page
sampled 2026-09), so it can be measured with plain pixel arithmetic -- no OCR, no
LLM, nothing that "reads" the image as an image. There is no sweetness bar on this
template.

The block is three identical 5-segment horizontal bars, top to bottom: 산미
(acidity), 무게감 (body), then a roast-level indicator bar under "로스팅레벨"/the
라이트..다크 labels. Each segment is a solid "filled" or ``TRACK_RGB`` ("empty")
rectangle; the fill fraction of the whole bar, rounded to the nearest half
segment, is the 0-5 reading. ``TRACK_RGB`` is one fixed color everywhere sampled
(including the decaf line), but the *fill* accent color is not: the regular
line uses a dark brown while at least one decaf product (idx 4876, sampled
2026-09) uses a lighter tan -- both are plain solid colors, so rather than
hardcode one, the reader classifies each candidate pixel as "track" (matches
``TRACK_RGB``), "background/text" (near-white, or a near-neutral low-saturation
dark gray -- Korean glyph anti-aliasing) or otherwise "fill" (see
``_fill_candidate_mask``). This stays pure pixel arithmetic -- no OCR, no LLM --
while tolerating a second known accent color without guessing at any others.
There is no sweetness bar on this template.

The Agtron number printed under the roast row is plain rendered text with no
fixed position/font contract we can rely on without OCR, so it is
intentionally never read -- see ``read_chart``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

TRACK_RGB = (224, 219, 212)  # light gray/tan = empty segment; constant across every product seen
COLOR_TOL = 20  # per-channel tolerance around the track reference color
WHITE_FLOOR = 245  # a pixel with every channel above this is page background, never a bar segment
NEUTRAL_SPAN = 18  # max(channel) - min(channel) at or below this is near-gray -> text/icon, not a
# fill accent (every fill color observed is a distinctly saturated brown/tan, span > 60)
MIN_BAR_WIDTH = 500  # the real bar spans ~571px at the template's fixed 1000px width
MAX_BAR_WIDTH = 650
MIN_MATCH_COVERAGE = 0.85  # fraction of a candidate span that must be fill/track (rest is AA seams)
BRIDGE_GAP = 6  # bridge anti-aliased seams between segments when clustering matched pixels
MIN_BAND_ROWS = 6  # a real bar is ~12px tall; require a solid run of matching rows
X_TOLERANCE = 10  # allowed drift in the bar's left edge between rows of one band
MAX_BAND_GAP = 250  # max vertical px between the bars of one chart block
TEMPLATE_X0_RANGE = (340, 390)  # the template's bar always starts in this x-band at 1000px width
BLOCK_X0_TOLERANCE = 8  # bars in one real chart block share x0 this tightly; photos matching by
# chance (e.g. a light sky/background) drift by tens of px bar to bar -- see find_chart_block

ROAST_LABELS = ("라이트", "미디엄 라이트", "미디엄", "미디엄 다크", "다크")


def _close_mask(arr: np.ndarray, rgb: tuple[int, int, int]) -> np.ndarray:
    return np.all(np.abs(arr.astype(int) - np.array(rgb)) <= COLOR_TOL, axis=-1)


def _fill_candidate_mask(arr: np.ndarray, track_mask: np.ndarray) -> np.ndarray:
    """Pixels that could be a filled segment: not background white, not the
    (already-matched) track color, and not a near-neutral dark gray (glyph
    anti-aliasing) -- see the module docstring for why this isn't one fixed color."""
    a = arr.astype(int)
    is_white = np.all(a >= WHITE_FLOOR, axis=-1)
    span = a.max(axis=-1) - a.min(axis=-1)
    is_neutral = span <= NEUTRAL_SPAN
    return ~is_white & ~track_mask & ~is_neutral


def _row_bar_extent(match_row: np.ndarray) -> tuple[int, int] | None:
    """[x0, x1) of the bar in this row if one of its clusters of matched pixels
    looks like a fill/track bar, else None. Stray matches elsewhere in the row
    (e.g. anti-aliased label text that happens to land near the track color)
    are rejected by clustering matched pixels first: the bar's ~571px span is
    one dense cluster, unrelated matches sit in far smaller, sparser ones."""
    idx = np.nonzero(match_row)[0]
    if idx.size == 0:
        return None
    gaps = np.diff(idx)
    breaks = np.nonzero(gaps > BRIDGE_GAP)[0]
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [len(idx) - 1]))
    for s, e in zip(starts, ends):
        x0, x1 = int(idx[s]), int(idx[e]) + 1
        width = x1 - x0
        if not (MIN_BAR_WIDTH <= width <= MAX_BAR_WIDTH):
            continue
        count = e - s + 1
        if count / width >= MIN_MATCH_COVERAGE:
            return x0, x1
    return None


@dataclass
class BarReading:
    y0: int
    y1: int
    x0: int
    x1: int
    value: float | None  # 0-5 in half steps, or None if the band had no measurable fill/track


def _find_bars(fill_mask: np.ndarray, track_mask: np.ndarray) -> list[BarReading]:
    match_mask = fill_mask | track_mask
    h = match_mask.shape[0]
    bars: list[BarReading] = []
    band_rows: list[int] = []
    band_x: tuple[int, int] | None = None

    def flush() -> None:
        if len(band_rows) >= MIN_BAND_ROWS and band_x is not None:
            y0, y1 = band_rows[0], band_rows[-1] + 1
            x0, x1 = band_x
            fcount = int(fill_mask[y0:y1, x0:x1].sum())
            tcount = int(track_mask[y0:y1, x0:x1].sum())
            total = fcount + tcount
            value = None
            if total > 0:
                ratio = fcount / total
                value = max(0.0, min(5.0, round(ratio * 5 * 2) / 2))
            bars.append(BarReading(y0, y1, x0, x1, value))

    for y in range(h):
        extent = _row_bar_extent(match_mask[y])
        if extent is None or (band_x is not None and abs(extent[0] - band_x[0]) > X_TOLERANCE):
            flush()
            band_rows = []
            band_x = None
            if extent is None:
                continue
        band_rows.append(y)
        band_x = extent
    flush()
    return bars


def find_chart_block(arr: np.ndarray) -> list[BarReading]:
    """The bars belonging to one chart block (>=2 bars, closely spaced, sharing an
    x-extent), or [] if the fixed template was not found anywhere in the image."""
    track_mask = _close_mask(arr, TRACK_RGB)
    fill_mask = _fill_candidate_mask(arr, track_mask)
    bars = [b for b in _find_bars(fill_mask, track_mask) if TEMPLATE_X0_RANGE[0] <= b.x0 <= TEMPLATE_X0_RANGE[1]]
    blocks: list[list[BarReading]] = []
    for b in bars:
        # Chain against the block's FIRST bar (not its last) so a run of incidental
        # matches in a photo -- which drift x0 by tens of px bar to bar -- cannot
        # accumulate its way past the tolerance one hop at a time.
        if (blocks and b.y0 - blocks[-1][-1].y1 <= MAX_BAND_GAP
                and abs(b.x0 - blocks[-1][0].x0) <= BLOCK_X0_TOLERANCE):
            blocks[-1].append(b)
        else:
            blocks.append([b])
    for block in blocks:
        if len(block) >= 2:
            return block
    return []


@dataclass
class ChartReading:
    acidity: float | None  # 0-5 in half steps
    body: float | None  # 0-5 in half steps
    roast_step: int | None  # 1 (라이트) .. 5 (다크), best-effort
    bars: list[BarReading]


def read_chart_array(arr: np.ndarray) -> ChartReading | None:
    block = find_chart_block(arr)
    if len(block) < 2:
        return None
    roast_step = None
    if len(block) >= 3 and block[2].value is not None:
        step = round(block[2].value)  # the roast bar is always a whole number of segments
        if 1 <= step <= 5:
            roast_step = step
    return ChartReading(acidity=block[0].value, body=block[1].value, roast_step=roast_step, bars=block)


def read_chart(image_path: Path) -> ChartReading | None:
    """Read the fixed-template chart out of a Momos detail image. Returns None
    (never a guess) when the template isn't found -- e.g. a product whose detail
    image has no chart, or an unreadable/corrupt file."""
    try:
        with Image.open(image_path) as im:
            arr = np.array(im.convert("RGB"))
    except OSError:
        return None
    return read_chart_array(arr)
