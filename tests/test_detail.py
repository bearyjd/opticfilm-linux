import cv2
import numpy as np
import pytest

import reference_pipeline
from opticfilm_linux.model import Box, Slide
from opticfilm_linux.pipeline import arrays, run
from opticfilm_linux.pipeline.detail import reduce_grain, sharpen
from opticfilm_linux.pipeline.run import ProcessOptions

FULL = 65535
EDGE_COLUMN = 64


def _step(noise: float = 0.0, seed: int = 0) -> np.ndarray:
    """(48, 128, 3) uint16: 0.3 left of EDGE_COLUMN, 0.7 right, optional Gaussian noise."""
    values = np.full((48, 128, 3), 0.3)
    values[:, EDGE_COLUMN:] = 0.7
    if noise:
        values = values + np.random.default_rng(seed).normal(0, noise, values.shape)
    return np.clip(np.rint(values * FULL), 0, FULL).astype(np.uint16)


def _max_gradient(image: np.ndarray) -> float:
    profile = image.astype(np.float64).mean(axis=(0, 2))
    return float(np.abs(np.diff(profile)).max())


def _edge_position(image: np.ndarray) -> int:
    profile = image.astype(np.float64).mean(axis=(0, 2))
    return int(np.abs(np.diff(profile)).argmax())


def test_sharpen_increases_edge_gradient():
    image = _step()
    assert _max_gradient(sharpen(image, radius_px=1.5, amount=0.8)) >= 1.2 * _max_gradient(image)


def test_sharpen_leaves_flat_region_unchanged():
    image = _step()
    out = sharpen(image, radius_px=1.5, amount=0.8)
    flat = np.abs(out[:, :40].astype(np.int64) - image[:, :40])
    assert flat.max() <= 0.01 * FULL


def test_grain_reduction_cuts_noise_and_keeps_edge():
    image = _step(noise=0.03)
    out = reduce_grain(image, strength=0.6)
    before = image[:, 5:40].astype(np.float64).std()
    after = out[:, 5:40].astype(np.float64).std()
    assert after <= 0.7 * before
    assert abs(_edge_position(out) - _edge_position(image)) <= 1


def test_strength_zero_is_identity():
    image = _step(noise=0.03)
    assert np.array_equal(reduce_grain(image, strength=0.0), image)
    assert np.array_equal(sharpen(image, amount=0.0), image)


def test_outputs_are_uint16_same_shape():
    image = _step(noise=0.02)
    for out in (sharpen(image), reduce_grain(image)):
        assert out.dtype == np.uint16 and out.shape == image.shape


def test_input_is_not_modified():
    image = _step(noise=0.02)
    before = image.copy()
    sharpen(image)
    reduce_grain(image)
    assert np.array_equal(image, before)


# --- dpi scaling: kernels are defined in pixels at 600 dpi and grow linearly with dpi ---------

REFERENCE_DPI = 600


def _grain_pattern(scale: int, noise: float = 0.03) -> np.ndarray:
    """Grain blobs 1 px wide at 600 dpi, enlarged ``scale`` times, on a flat grey."""
    rng = np.random.default_rng(3)
    blobs = np.clip(0.5 + rng.normal(0, noise, (60, 80, 3)), 0, 1)
    big = np.kron(blobs, np.ones((scale, scale, 1)))
    return np.rint(big * FULL).astype(np.uint16)


def _soft_edge(scale: int) -> np.ndarray:
    """A dark-to-light edge blurred over about 1.5 px at 600 dpi, enlarged ``scale`` times."""
    x = (np.arange(80) - 40 + 0.5) / 1.5
    row = 0.3 + 0.4 / (1 + np.exp(-2 * x))
    small = np.ascontiguousarray(np.broadcast_to(row[None, :, None], (30, 80, 3)))
    big = cv2.resize(small, (80 * scale, 30 * scale), interpolation=cv2.INTER_CUBIC)
    return np.rint(np.clip(big, 0, 1) * FULL).astype(np.uint16)


def test_600_dpi_is_byte_identical_to_the_frozen_implementation():
    for image in (_step(noise=0.03), _grain_pattern(1), _soft_edge(1)):
        for dpi_kwargs in ({}, {"dpi": REFERENCE_DPI}):
            for amount in (0.5, 1.2):
                assert np.array_equal(
                    sharpen(image, amount=amount, **dpi_kwargs),
                    reference_pipeline.reference_sharpen(image, amount=amount),
                )
            for strength in (0.3, 0.9):
                assert np.array_equal(
                    reduce_grain(image, strength=strength, **dpi_kwargs),
                    reference_pipeline.reference_reduce_grain(image, strength=strength),
                )


def test_zero_strength_is_identity_at_every_dpi():
    image = _grain_pattern(6)
    for dpi in (300, 600, 3600, 7200):
        assert np.array_equal(sharpen(image, amount=0.0, dpi=dpi), image)
        assert np.array_equal(reduce_grain(image, strength=0.0, dpi=dpi), image)


def test_grain_reduction_has_the_same_relative_effect_at_3600_dpi():
    def relative_noise(scale: int) -> float:
        image = _grain_pattern(scale)
        out = reduce_grain(image, strength=0.6, dpi=REFERENCE_DPI * scale)
        return float(out.astype(np.float64).std() / image.astype(np.float64).std())

    at_600, at_3600 = relative_noise(1), relative_noise(6)
    assert at_600 < 0.8  # the reduction is real at the reference
    assert abs(at_3600 - at_600) < 0.1  # and a 6x enlarged pattern is treated alike


def test_fixed_pixel_grain_filter_would_not_touch_enlarged_grain():
    """Guards the premise: at 3600 dpi without scaling the reduction is nearly invisible."""
    image = _grain_pattern(6)
    unscaled = reference_pipeline.reference_reduce_grain(image, strength=0.6)
    assert unscaled.astype(np.float64).std() > 0.8 * image.astype(np.float64).std()
    scaled = reduce_grain(image, strength=0.6, dpi=3600)
    assert scaled.astype(np.float64).std() < 0.6 * image.astype(np.float64).std()


def test_sharpening_has_the_same_relative_effect_at_3600_dpi():
    small, big = _soft_edge(1), _soft_edge(6)
    ref = sharpen(small, amount=1.0, dpi=600)
    out = sharpen(big, amount=1.0, dpi=3600)
    ref_gain = _max_gradient(ref) / _max_gradient(small)
    out_gain = _max_gradient(out) / _max_gradient(big)
    assert ref_gain > 1.2
    assert abs(out_gain - ref_gain) < 0.15 * ref_gain
    # the overshoot (rim) next to the edge is as large, relative to the step, at both scales
    step = 0.4 * FULL
    rim_600 = (ref[:, :, 0].astype(np.float64).mean(axis=0).max() - 0.7 * FULL) / step
    rim_3600 = (out[:, :, 0].astype(np.float64).mean(axis=0).max() - 0.7 * FULL) / step
    assert rim_600 > 0.005
    assert abs(rim_3600 - rim_600) < 0.03


def test_grain_filter_width_grows_continuously_with_dpi():
    """Non-integer scales (900 and 1500 dpi) change the result too: direct and shrunk paths."""
    image = _grain_pattern(6)
    noise = [
        reduce_grain(image, strength=1.0, dpi=dpi).astype(np.float64).std()
        for dpi in (600, 900, 1200, 1500, 1800)
    ]
    assert all(a > b for a, b in zip(noise, noise[1:], strict=False))


@pytest.mark.parametrize("dpi", [900, 1500, 3600])
def test_strips_and_threads_do_not_change_high_dpi_results(dpi, monkeypatch):
    rng = np.random.default_rng(5)
    image = rng.integers(20_000, 40_000, (701, 305, 3), dtype=np.uint16)  # not multiples of factor
    monkeypatch.setattr(arrays, "MAX_WORKERS", 1)
    monkeypatch.setattr(arrays, "STRIP_ROWS", 10_000)  # one strip: no seams possible
    whole = (reduce_grain(image, dpi=dpi), sharpen(image, dpi=dpi))
    monkeypatch.setattr(arrays, "STRIP_ROWS", 128)
    monkeypatch.setattr(arrays, "MAX_WORKERS", 6)
    monkeypatch.setattr(arrays, "STRIPS_PER_WORKER", 1)
    assert np.array_equal(reduce_grain(image, dpi=dpi), whole[0])
    assert np.array_equal(sharpen(image, dpi=dpi), whole[1])


def test_scale_is_capped_at_7200_dpi():
    image = _grain_pattern(2)
    assert np.array_equal(sharpen(image, dpi=100_000), sharpen(image, dpi=7200))
    assert np.array_equal(reduce_grain(image, dpi=100_000), reduce_grain(image, dpi=7200))


def test_non_positive_dpi_is_rejected():
    image = _step()
    for dpi in (0, -600):
        for step in (sharpen, reduce_grain):
            with pytest.raises(ValueError, match="dpi"):
                step(image, dpi=dpi)


def test_enhance_slide_passes_its_dpi_to_the_detail_steps(monkeypatch):
    seen: dict[str, int] = {}
    monkeypatch.setattr(run, "sharpen", lambda img, amount, dpi: seen.update(sharpen=dpi) or img)
    monkeypatch.setattr(
        run, "reduce_grain", lambda img, strength, dpi: seen.update(grain=dpi) or img
    )
    slide = Slide(index=0, rgb=_step(), ir=None, box=Box(0, 0, 128, 48))
    run.enhance_slide(slide, ProcessOptions(remove_dust=False), 3600)
    assert seen == {"sharpen": 3600, "grain": 3600}


def test_high_dpi_grain_reduction_stays_16_bit():
    out = reduce_grain(_grain_pattern(6), strength=1.0, dpi=3600)
    # an 8-bit round trip would leave only 256 distinct levels per channel
    assert out.dtype == np.uint16 and len(np.unique(out[..., 0])) > 1000
