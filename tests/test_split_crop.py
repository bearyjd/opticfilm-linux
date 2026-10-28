"""Cropping each opening across the sweep to the bright window (synthetic frames only)."""

import dataclasses

import numpy as np
import pytest

from opticfilm_linux.model import RawSweep
from opticfilm_linux.pipeline.split import split_slides
from synthetic import FULL, make_sweep, truth_boxes

MM_PER_INCH = 25.4
DPIS = [300, 600, 1200]
WINDOW_MM = (3.0, 32.0)


def _px(mm: float, dpi: int) -> int:
    return round(mm / MM_PER_INCH * dpi)


def _mounted(
    dpi: int,
    window: tuple[int, int] | None = None,
    tilt_deg: float = 0.0,
    mount_noise: float = 0.0,
    ir: bool = True,
) -> tuple[RawSweep, int, int]:
    """A sweep whose openings are bright only between two columns; opaque mount elsewhere.

    Returns the sweep and the window columns [x0, x1) that are bright on EVERY row.
    """
    sweep = make_sweep(dpi=dpi, ir=ir)
    x0, x1 = window or (_px(WINDOW_MM[0], dpi), _px(WINDOW_MM[1], dpi))
    width = sweep.rgb.shape[1]
    rng = np.random.default_rng(1)
    rgb, ir_plane = sweep.rgb.copy(), None if sweep.ir is None else sweep.ir.copy()
    slope = np.tan(np.radians(tilt_deg))
    for box in truth_boxes(dpi):
        for row in range(box.y, box.y + box.h):
            shift = round(slope * (row - box.y - box.h / 2))
            keep = np.zeros(width, bool)
            keep[max(0, x0 + shift) : max(0, x1 + shift)] = True
            dark = np.uint16(0.01 * FULL)
            noise = rng.normal(0, mount_noise * FULL, width).clip(-0.01 * FULL, None)
            level = (dark + noise).clip(0, FULL).astype(np.uint16)
            rgb[row, ~keep] = level[~keep, None]
            if ir_plane is not None:
                ir_plane[row, ~keep] = level[~keep]
    shifts = [round(slope * d) for d in (-truth_boxes(dpi)[0].h / 2, truth_boxes(dpi)[0].h / 2)]
    inner0 = x0 + max(shifts + [0])
    inner1 = x1 + min(shifts + [0])
    return dataclasses.replace(sweep, rgb=rgb, ir=ir_plane), inner0, inner1


def _inset(dpi: int) -> int:
    from opticfilm_linux.pipeline.split import edge_inset_px

    return edge_inset_px(dpi)


@pytest.mark.parametrize("dpi", DPIS)
def test_box_hugs_the_window_minus_a_small_inset(dpi):
    sweep, x0, x1 = _mounted(dpi)
    inset = _inset(dpi)
    for slide in split_slides(sweep):
        assert abs(slide.box.x - (x0 + inset)) <= 2
        assert abs((slide.box.x + slide.box.w) - (x1 - inset)) <= 2
        assert slide.rgb.shape[1] == slide.box.w
        assert slide.ir.shape[1] == slide.box.w


@pytest.mark.parametrize("dpi", DPIS)
def test_half_degree_tilt_never_leaks_mount_into_the_crop(dpi):
    sweep, x0, x1 = _mounted(dpi, tilt_deg=0.5)
    for slide in split_slides(sweep):
        assert slide.box.x >= x0 - 1
        assert slide.box.x + slide.box.w <= x1 + 1
        assert slide.box.w > 0.6 * (x1 - x0)
        assert slide.ir.mean() > 0.8 * FULL * 0.9


def test_window_touching_the_left_edge_keeps_x_zero():
    dpi = 600
    right = _px(30.0, dpi)
    sweep, _, _ = _mounted(dpi, window=(0, right))
    inset = _inset(dpi)
    for slide in split_slides(sweep):
        assert slide.box.x == 0
        assert abs((slide.box.x + slide.box.w) - (right - inset)) <= 2


def test_window_touching_the_right_edge_keeps_the_far_edge():
    dpi = 600
    left = _px(5.0, dpi)
    width = make_sweep(dpi=dpi).rgb.shape[1]
    sweep, _, _ = _mounted(dpi, window=(left, width))
    for slide in split_slides(sweep):
        assert abs(slide.box.x - (left + _inset(dpi))) <= 2
        assert slide.box.x + slide.box.w == width


def test_uniform_frame_falls_back_to_full_width():
    sweep = make_sweep(dpi=600)
    width = sweep.rgb.shape[1]
    for slide in split_slides(sweep):
        assert (slide.box.x, slide.box.w) == (0, width)


def test_clear_windows_fall_back_to_full_width():
    sweep = make_sweep(dpi=600, filled=(False, False, False, False))
    width = sweep.rgb.shape[1]
    for slide in split_slides(sweep):
        assert (slide.box.x, slide.box.w) == (0, width)


@pytest.mark.parametrize("dpi", [300, 600])
def test_noisy_mount_columns_do_not_move_the_edges(dpi):
    sweep, x0, x1 = _mounted(dpi, mount_noise=0.04)
    inset = _inset(dpi)
    for slide in split_slides(sweep):
        assert abs(slide.box.x - (x0 + inset)) <= 2
        assert abs((slide.box.x + slide.box.w) - (x1 - inset)) <= 2


def test_without_ir_the_luminance_profile_is_used():
    sweep, x0, x1 = _mounted(600, ir=False)
    inset = _inset(600)
    for slide in split_slides(sweep):
        assert slide.ir is None
        assert abs(slide.box.x - (x0 + inset)) <= 3
        assert abs((slide.box.x + slide.box.w) - (x1 - inset)) <= 3


def test_narrow_sliver_window_falls_back_to_full_width():
    dpi = 600
    sweep, _, _ = _mounted(dpi, window=(_px(10.0, dpi), _px(14.0, dpi)))
    width = sweep.rgb.shape[1]
    for slide in split_slides(sweep):
        assert (slide.box.x, slide.box.w) == (0, width)


def test_faint_contrast_between_mount_and_window_is_not_trusted():
    sweep, _, _ = _mounted(600)
    ir = (sweep.ir.astype(np.float64) * 0.1).astype(np.uint16)
    ir[sweep.ir < 0.5 * FULL] = np.uint16(0.01 * FULL)
    width = sweep.rgb.shape[1]
    for slide in split_slides(dataclasses.replace(sweep, ir=ir)):
        assert (slide.box.x, slide.box.w) == (0, width)


def test_no_column_bright_on_most_rows_falls_back_without_crashing():
    sweep, _, _ = _mounted(600)
    ir = sweep.ir.copy()
    width = ir.shape[1]
    for box in truth_boxes(600):
        rows = slice(box.y, box.y + box.h)
        ir[rows, : width // 3] = np.uint16(0.01 * FULL)
        ir[rows, width // 3 :] = np.uint16(0.9 * FULL)
        ir[box.y : box.y + box.h : 2, width // 3 : 2 * width // 3] = np.uint16(0.01 * FULL)
        ir[box.y + 1 : box.y + box.h : 2, 2 * width // 3 :] = np.uint16(0.01 * FULL)
    for slide in split_slides(dataclasses.replace(sweep, ir=ir)):
        assert (slide.box.x, slide.box.w) == (0, width)


def test_empty_plane_gives_the_full_span():
    from opticfilm_linux.pipeline.split import _window_columns

    assert _window_columns(np.zeros((0, 40), np.uint16), 600) == (0, 40)
    assert _window_columns(np.zeros((10, 0), np.uint16), 600) == (0, 0)


def test_crop_is_a_view_of_the_sweep():
    sweep, _, _ = _mounted(600)
    for slide in split_slides(sweep):
        assert np.shares_memory(slide.rgb, sweep.rgb)
        b = slide.box
        assert np.array_equal(slide.rgb, sweep.rgb[b.y : b.y + b.h, b.x : b.x + b.w])


def test_inset_scales_with_dpi_and_stays_small():
    assert _inset(300) < _inset(600) < _inset(3600)
    assert 2 <= _inset(600) <= 6
    assert _inset(100) == 2
