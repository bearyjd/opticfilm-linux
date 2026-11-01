"""Regression coverage for the linear-positive exposure and display transform."""

import numpy as np
import pytest

from opticfilm_linux.pipeline.arrays import map_strips
from opticfilm_linux.pipeline.tone import HIGHLIGHT_KNEE, _highlight_rolloff, auto_exposure

FULL = 65535


def _grey(values: np.ndarray) -> np.ndarray:
    return np.repeat(np.rint(values * FULL).astype(np.uint16)[..., None], 3, axis=2)


def _decode(values: np.ndarray) -> np.ndarray:
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def test_dark_scene_keeps_black_and_lifts_median_without_mutating_input():
    values = np.concatenate(
        [np.linspace(0, 0.002, 500), np.linspace(0.002, 0.05, 9000), np.linspace(0.05, 1, 500)]
    )
    image = _grey(values.reshape(100, 100))
    original = image.copy()
    out = auto_exposure(image, linear=True) / FULL
    assert np.percentile(out, 1) <= 20 / 255
    assert 0.35 <= np.median(out) <= 0.5
    assert np.array_equal(image, original)


def test_highlights_roll_off_smoothly_without_clipping():
    values = np.concatenate([np.linspace(0, 0.08, 9000), np.linspace(0.1, 1, 1000)])
    out = auto_exposure(_grey(values.reshape(100, 100)), linear=True) / FULL
    highlights = out.reshape(-1, 3)[9000:, 0]
    assert highlights[-1] < 1
    assert np.all(np.diff(highlights) >= 0)
    assert highlights[-1] - highlights[0] > 0.05


def test_neutral_ramp_keeps_equal_channels():
    image = _grey(np.linspace(0, 0.3, 10000).reshape(100, 100))
    out = auto_exposure(image, linear=True)
    assert np.array_equal(out[..., 0], out[..., 1])
    assert np.array_equal(out[..., 1], out[..., 2])


def test_gain_is_capped_at_three_stops_and_srgb_toe_is_used():
    values = np.concatenate([np.linspace(0, 0.002, 9800), np.ones(200)]).reshape(100, 100)
    image = _grey(values)
    out = auto_exposure(image, linear=True) / FULL
    sample = image.astype(np.float32) / FULL
    low, high = np.percentile(sample.min(axis=2), 0.1), np.percentile(sample.max(axis=2), 99.9)
    stretched = np.clip((sample - low) / (high - low), 0, 1) * 8
    expected = np.minimum(stretched, 0.0031308) * 12.92
    toe = stretched <= 0.0031308
    assert np.max(np.abs(out[toe] - expected[toe])) < 2 / FULL
    assert np.median(_decode(out)) <= np.median(stretched) + 2 / FULL


def test_bright_scene_does_not_reduce_exposure():
    image = _grey(np.linspace(0.2, 0.6, 10000).reshape(100, 100))
    out = auto_exposure(image, linear=True) / FULL
    sample = image.astype(np.float32) / FULL
    low, high = np.percentile(sample.min(axis=2), 0.1), np.percentile(sample.max(axis=2), 99.9)
    stretched = np.clip((sample - low) / (high - low), 0, 1)
    assert np.median(_decode(out)) >= np.median(stretched) - 2 / FULL


def test_degenerate_frame_returns_an_independent_copy():
    image = np.full((20, 20, 3), 1234, dtype=np.uint16)
    out = auto_exposure(image, linear=True)
    assert np.array_equal(out, image)
    assert not np.shares_memory(out, image)


@pytest.mark.parametrize("gamma_limits", [(0.5, 2.0), (0.2, 2.0), (1.0, 1.0)])
def test_default_path_matches_frozen_original_formula(gamma_limits):
    image = np.random.default_rng(5).integers(0, FULL + 1, (60, 70, 3), dtype=np.uint16)
    sample = image.astype(np.float32) / FULL
    low = np.percentile(sample.min(axis=2), 0.1)
    high = np.percentile(sample.max(axis=2), 99.9)
    median = np.percentile(sample.mean(axis=2), 50)
    mid = float(np.clip((median - low) / (high - low), 1e-3, 1 - 1e-3))
    gamma = float(np.clip(np.log(0.45) / np.log(mid), *gamma_limits))
    expected = map_strips(image, lambda x: np.clip((x - low) / (high - low), 0, 1) ** gamma)
    assert np.array_equal(auto_exposure(image, gamma_limits), expected)


def test_linear_switch_is_explicit_and_ignores_gamma_limits():
    image = _grey(np.linspace(0, 1, 10000).reshape(100, 100) ** 4)
    linear = auto_exposure(image, linear=True)
    assert not np.array_equal(linear, auto_exposure(image))
    for limits in [(0.2, 2.0), (0.5, 2.0), (1.0, 1.0)]:
        assert np.array_equal(auto_exposure(image, limits, linear=True), linear)
    assert np.array_equal(auto_exposure(image, linear=False), auto_exposure(image))


def test_highlight_rolloff_value_and_first_derivative_are_continuous_at_knee():
    delta = 1e-6
    values = np.array([HIGHLIGHT_KNEE - delta, HIGHLIGHT_KNEE, HIGHLIGHT_KNEE + delta])
    out = _highlight_rolloff(values)
    assert out[1] == pytest.approx(HIGHLIGHT_KNEE, abs=1e-12)
    assert np.max(np.abs(out - HIGHLIGHT_KNEE)) <= delta + 1e-12
    left_slope, right_slope = np.diff(out) / delta
    assert left_slope == pytest.approx(1.0, abs=1e-5)
    assert right_slope == pytest.approx(left_slope, abs=1e-5)


def test_highlight_rolloff_is_strictly_increasing_above_knee():
    values = np.linspace(HIGHLIGHT_KNEE, 8, 10000, dtype=np.float64)
    out = _highlight_rolloff(values)
    assert np.all(np.diff(out) > 0)
    assert out[-1] < 1


def test_coloured_channels_keep_their_order_and_black_stays_near_zero():
    ramp = np.linspace(0, 1, 10000).reshape(100, 100) ** 4
    image = np.rint(ramp[..., None] * np.array([1.0, 0.65, 0.3]) * FULL).astype(np.uint16)
    out = auto_exposure(image, linear=True)
    assert np.max(out[0, 0]) / FULL <= 1 / FULL
    coloured = out.reshape(-1, 3)[5000:]
    assert np.all(coloured[:, 0] > coloured[:, 1])
    assert np.all(coloured[:, 1] > coloured[:, 2])
