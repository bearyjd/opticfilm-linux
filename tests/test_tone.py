import warnings

import numpy as np

from opticfilm_linux.pipeline.tone import (
    adjust_brightness,
    auto_exposure,
    invert_negative,
    restore_color,
)

FULL = 65535


def _gradient(lo: float, hi: float, h: int = 64, w: int = 256) -> np.ndarray:
    """(h, w, 3) uint16 image whose three channels ramp from lo to hi of full scale."""
    ramp = np.linspace(lo, hi, w)[None, :].repeat(h, axis=0)
    wobble = np.linspace(0, 0.02 * (hi - lo), h)[:, None]
    channels = [np.clip(ramp + wobble * k, 0, 1) for k in (0, 1, 2)]
    return np.rint(np.stack(channels, axis=2) * FULL).astype(np.uint16)


def _clipped_fraction(image: np.ndarray) -> float:
    return float(np.mean((image == 0) | (image == FULL)))


def test_restore_color_stretches_faded_image():
    out = restore_color(_gradient(0.3, 0.7))
    for channel in range(3):
        low, high = np.percentile(out[..., channel], [0.1, 99.9])
        assert low <= 0.01 * FULL
        assert high >= 0.99 * FULL


def test_restore_color_removes_cast():
    base = _gradient(0.2, 0.6).astype(np.float32)
    cast = base.copy()
    cast[..., 0] *= 1.3
    out = restore_color(cast.astype(np.uint16))
    means = out.reshape(-1, 3).mean(axis=0)
    assert means.max() / means.min() < 1.05


def test_strength_zero_is_identity():
    image = _gradient(0.3, 0.7)
    assert np.array_equal(restore_color(image, strength=0.0), image)


def test_clipping_is_limited():
    out = restore_color(auto_exposure(_gradient(0.05, 0.95)))
    assert _clipped_fraction(out) <= 0.005


def test_all_black_frame_has_no_nan_or_error():
    black = np.zeros((32, 32, 3), np.uint16)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for step in (auto_exposure, restore_color, invert_negative):
            out = step(black)
            assert out.dtype == np.uint16 and out.shape == black.shape


def test_very_dark_slide_is_brightened_not_blown_out():
    out = auto_exposure(_gradient(0.01, 0.09))
    assert 0.2 * FULL < out.mean() < 0.6 * FULL
    assert _clipped_fraction(out) < 0.01


def test_functions_return_uint16_same_shape():
    image = _gradient(0.1, 0.9)
    for step in (auto_exposure, restore_color, invert_negative):
        out = step(image)
        assert out.dtype == np.uint16 and out.shape == image.shape


def test_input_is_not_modified():
    image = _gradient(0.1, 0.9)
    before = image.copy()
    for step in (auto_exposure, restore_color, invert_negative):
        step(image)
    assert np.array_equal(image, before)


def test_invert_negative_matches_positive():
    positive = _gradient(0.1, 0.9).astype(np.float64) / FULL
    negative = np.rint(10 ** (-2.0 * positive) * FULL).astype(np.uint16)
    recovered = invert_negative(negative).astype(np.float64) / FULL
    assert np.corrcoef(recovered.ravel(), positive.ravel())[0, 1] > 0.95


def test_invert_negative_leaves_a_channel_with_no_range_unchanged():
    image = _gradient(0.1, 0.9)
    image[..., 1] = np.uint16(0.5 * FULL)  # a flat green channel
    out = invert_negative(image)
    assert np.array_equal(out[..., 1], image[..., 1])


def test_invert_negative_leaves_an_all_white_frame_unchanged():
    white = np.full((16, 16, 3), FULL, np.uint16)
    assert np.array_equal(invert_negative(white), white)


def _flat(level: float) -> np.ndarray:
    return np.full((16, 16, 3), np.uint16(level * FULL), np.uint16)


def test_brightness_plus_one_stop_doubles_midgrey():
    out = adjust_brightness(_flat(0.25), 1.0)
    assert abs(int(out[0, 0, 0]) - int(0.5 * FULL)) <= 2


def test_brightness_minus_one_stop_halves():
    out = adjust_brightness(_flat(0.5), -1.0)
    assert abs(int(out[0, 0, 0]) - int(0.25 * FULL)) <= 2


def test_brightness_zero_is_identity():
    image = _gradient(0.1, 0.9)
    assert np.array_equal(adjust_brightness(image, 0.0), image)


def test_brightness_clips_at_white():
    out = adjust_brightness(_flat(0.8), 2.0)
    assert out.max() == FULL


def test_brightness_all_black_has_no_nan_or_warning():
    black = np.zeros((8, 8, 3), np.uint16)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert np.array_equal(adjust_brightness(black, 1.5), black)
