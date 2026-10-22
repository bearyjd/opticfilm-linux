import numpy as np

from opticfilm_linux.pipeline.detail import reduce_grain, sharpen

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
