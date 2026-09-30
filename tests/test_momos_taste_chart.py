from pathlib import Path

import numpy as np
from PIL import Image

from pipeline.collect.momos_taste_chart import read_chart, read_chart_array

FIXTURES = Path(__file__).parent / "fixtures" / "roasters_kr"


def test_read_chart_regular_fill_color():
    # Cropped straight from a real Momos detail image (product idx=7375): 산미 4/5,
    # 무게감 3/5, roast bar 1/5 ("라이트").
    r = read_chart(FIXTURES / "momos_chart_crop.png")
    assert r is not None
    assert (r.acidity, r.body, r.roast_step) == (4.0, 3.0, 1)


def test_read_chart_decaf_accent_color():
    # Cropped from a decaf product (idx=4876) whose bars use a lighter tan fill
    # accent instead of the regular line's dark brown -- see the module
    # docstring. Also exercises a half-filled segment (body = 2.5).
    r = read_chart(FIXTURES / "momos_chart_crop_decaf.png")
    assert r is not None
    assert (r.acidity, r.body, r.roast_step) == (3.0, 2.5, 2)


def test_read_chart_returns_none_for_plain_image():
    arr = np.full((200, 1000, 3), 255, dtype=np.uint8)  # blank white canvas, no chart
    assert read_chart_array(arr) is None


def test_read_chart_returns_none_for_missing_file(tmp_path):
    assert read_chart(tmp_path / "does-not-exist.png") is None


def test_read_chart_ignores_incidental_matches_outside_template_band():
    # A large flat block sitting well outside the template's known x-band
    # (TEMPLATE_X0_RANGE) must not be mistaken for a bar, however wide it is.
    arr = np.full((200, 1000, 3), 255, dtype=np.uint8)
    arr[20:40, 600:1000] = (139, 111, 71)  # fill-like color, but starts at x=600
    arr[80:100, 600:1000] = (224, 219, 212)  # track color, same wrong x-band
    assert read_chart_array(arr) is None


def test_read_chart_half_step_rounding(tmp_path):
    # Synthesize a bar at the template's exact geometry with a precise fill ratio
    # to check the nearest-half-segment rounding independent of any real image.
    from pipeline.collect.momos_taste_chart import TRACK_RGB

    arr = np.full((60, 1000, 3), 255, dtype=np.uint8)
    fill = (134, 77, 24)
    # Two bars, each 12 rows tall, x0=366..930 (matches TEMPLATE_X0_RANGE), first bar
    # is exactly 70% filled (3.5/5), second is fully filled (5/5, no track at all).
    x0, x1 = 366, 930
    split = x0 + round((x1 - x0) * 0.70)
    arr[5:17, x0:split] = fill
    arr[5:17, split:x1] = TRACK_RGB
    arr[25:37, x0:x1] = fill
    r = read_chart_array(arr)
    assert r is not None
    assert r.acidity == 3.5
    assert r.body == 5.0
