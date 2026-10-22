"""Exposure, colour-fade restoration and negative inversion on uint16 RGB arrays."""

import numpy as np

FULL_SCALE = 65535.0
SAMPLE_PIXELS = 1_000_000
MIN_SPAN = 1e-4
TARGET_MEDIAN = 0.45
GAMMA_LIMITS = (0.5, 2.0)
EXPOSURE_PERCENTILES = (0.1, 99.9)
NEGATIVE_PERCENTILES = (0.5, 99.5)
MIN_TRANSMITTANCE = 1e-4


def _to_float(rgb: np.ndarray) -> np.ndarray:
    return rgb.astype(np.float32) / FULL_SCALE


def _to_uint16(values: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(values * FULL_SCALE), 0, FULL_SCALE).astype(np.uint16)


def _sample(values: np.ndarray) -> np.ndarray:
    step = max(1, int(np.sqrt(values.shape[0] * values.shape[1] / SAMPLE_PIXELS)))
    return values[::step, ::step]


def auto_exposure(rgb: np.ndarray) -> np.ndarray:
    """Global black/white stretch and gamma from luminance, keeping colour balance."""
    x = _to_float(rgb)
    sample = _sample(x)
    # Black/white points come from the darkest and brightest channel at each pixel, so that
    # one shared stretch never clips a single channel by more than the percentile allows.
    low = np.percentile(sample.min(axis=2), EXPOSURE_PERCENTILES[0])
    high = np.percentile(sample.max(axis=2), EXPOSURE_PERCENTILES[1])
    median = np.percentile(sample.mean(axis=2), 50)
    if high - low < MIN_SPAN:
        return rgb.copy()
    stretched = np.clip((x - low) / (high - low), 0.0, 1.0)
    mid = float(np.clip((median - low) / (high - low), 1e-3, 1 - 1e-3))
    gamma = float(np.clip(np.log(TARGET_MEDIAN) / np.log(mid), *GAMMA_LIMITS))
    return _to_uint16(stretched**gamma)


def restore_color(rgb: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Per-channel percentile stretch to undo fading and colour casts."""
    x = _to_float(rgb)
    sample = _sample(x)
    low = np.percentile(sample, EXPOSURE_PERCENTILES[0], axis=(0, 1))
    high = np.percentile(sample, EXPOSURE_PERCENTILES[1], axis=(0, 1))
    span = high - low
    usable = span > MIN_SPAN
    safe_span = np.where(usable, span, 1.0)
    stretched = np.where(usable, np.clip((x - low) / safe_span, 0.0, 1.0), x)
    return _to_uint16((1.0 - strength) * x + strength * stretched)


def invert_negative(rgb: np.ndarray) -> np.ndarray:
    """Density inversion with automatic black and white points per channel."""
    transmittance = np.clip(_to_float(rgb), MIN_TRANSMITTANCE, 1.0)
    density = -np.log10(transmittance)
    sample = _sample(density)
    low = np.percentile(sample, NEGATIVE_PERCENTILES[0], axis=(0, 1))
    high = np.percentile(sample, NEGATIVE_PERCENTILES[1], axis=(0, 1))
    span = high - low
    usable = span > MIN_SPAN
    safe_span = np.where(usable, span, 1.0)
    return _to_uint16(np.where(usable, np.clip((density - low) / safe_span, 0.0, 1.0), 0.0))
