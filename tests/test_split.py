import dataclasses

import numpy as np
import pytest

from opticfilm_linux.model import Box
from opticfilm_linux.pipeline.split import SlideSplitError, is_blank, split_slides
from synthetic import make_sweep, truth_boxes


def _assert_boxes_close(slides, truth, tol):
    assert len(slides) == len(truth)
    for slide, expected in zip(slides, truth, strict=True):
        got = slide.box
        assert abs(got.y - expected.y) <= tol
        assert abs((got.y + got.h) - (expected.y + expected.h)) <= tol
        assert abs(got.x - expected.x) <= tol
        assert abs(got.w - expected.w) <= tol


def test_four_openings_found_at_600dpi():
    slides = split_slides(make_sweep(dpi=600))
    _assert_boxes_close(slides, truth_boxes(600), tol=2)
    assert [s.index for s in slides] == [0, 1, 2, 3]


def test_works_without_ir():
    slides = split_slides(make_sweep(dpi=600, ir=False))
    _assert_boxes_close(slides, truth_boxes(600), tol=3)
    assert all(s.ir is None for s in slides)


def test_tilted_content_is_not_clipped():
    slides = split_slides(make_sweep(dpi=600, tilt_deg=2.0))
    for slide, expected in zip(slides, truth_boxes(600), strict=True):
        assert slide.box.w >= expected.w - 2
        assert slide.box.h >= expected.h - 2


def test_partial_holder_flags_blank_slides():
    slides = split_slides(make_sweep(dpi=600, filled=(True, False, True, True)))
    assert [is_blank(s) for s in slides] == [False, True, False, False]


def test_dark_slide_is_not_blank():
    sweep = make_sweep(dpi=600)
    box = truth_boxes(600)[2]
    dark = sweep.rgb.copy()
    dark[box.y : box.y + box.h] = np.uint16(0.05 * 65535)
    slides = split_slides(dataclasses.replace(sweep, rgb=dark))
    assert is_blank(slides[2]) is False


def test_slides_are_views_of_the_sweep_not_copies():
    sweep = make_sweep(dpi=600)
    slides = split_slides(sweep)
    assert all(np.shares_memory(s.rgb, sweep.rgb) for s in slides)
    assert slides[0].box == Box(
        x=slides[0].box.x, y=slides[0].box.y, w=sweep.rgb.shape[1], h=slides[0].rgb.shape[0]
    )


def test_too_few_openings_raises():
    sweep = make_sweep(dpi=600)
    rgb, ir = sweep.rgb.copy(), sweep.ir.copy()
    for box in truth_boxes(600)[1:3]:
        rgb[box.y : box.y + box.h] = 0
        ir[box.y : box.y + box.h] = 0
    with pytest.raises(SlideSplitError, match="expected 4"):
        split_slides(dataclasses.replace(sweep, rgb=rgb, ir=ir))
