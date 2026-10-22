"""Exposure, colour-fade restoration and negative inversion on uint16 RGB arrays."""

import numpy as np

from opticfilm_linux.pipeline.arrays import map_strips, to_float

SAMPLE_PIXELS = 250_000
MIN_SPAN = 1e-4
TARGET_MEDIAN = 0.45
GAMMA_LIMITS = (0.5, 2.0)
EXPOSURE_PERCENTILES = (0.1, 99.9)
NEGATIVE_PERCENTILES = (0.5, 99.5)
MIN_TRANSMITTANCE = 1e-4


def _sample(image: np.ndarray) -> np.ndarray:
    """A strided subsample of a uint16 image, as float32 in [0, 1]; statistics come from this."""
    step = max(1, int(np.sqrt(image.shape[0] * image.shape[1] / SAMPLE_PIXELS)))
    return to_float(image[::step, ::step])


def auto_exposure(rgb: np.ndarray) -> np.ndarray:
    """One shared black/white stretch and gamma for all channels, keeping colour balance."""
    sample = _sample(rgb)
    # Black/white points come from the darkest and brightest channel at each pixel, so that
    # one shared stretch never clips a single channel by more than the percentile allows.
    low = np.percentile(sample.min(axis=2), EXPOSURE_PERCENTILES[0])
    high = np.percentile(sample.max(axis=2), EXPOSURE_PERCENTILES[1])
    median = np.percentile(sample.mean(axis=2), 50)
    if high - low < MIN_SPAN:
        return rgb.copy()
    mid = float(np.clip((median - low) / (high - low), 1e-3, 1 - 1e-3))
    gamma = float(np.clip(np.log(TARGET_MEDIAN) / np.log(mid), *GAMMA_LIMITS))
    return map_strips(rgb, lambda x: np.clip((x - low) / (high - low), 0.0, 1.0) ** gamma)


def restore_color(rgb: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Per-channel percentile stretch to undo fading and colour casts."""
    sample = _sample(rgb)
    low = np.percentile(sample, EXPOSURE_PERCENTILES[0], axis=(0, 1))
    high = np.percentile(sample, EXPOSURE_PERCENTILES[1], axis=(0, 1))
    span = high - low
    usable = span > MIN_SPAN
    safe_span = np.where(usable, span, 1.0)

    def apply(x: np.ndarray) -> np.ndarray:
        stretched = np.where(usable, np.clip((x - low) / safe_span, 0.0, 1.0), x)
        return (1.0 - strength) * x + strength * stretched

    return map_strips(rgb, apply)


def invert_negative(rgb: np.ndarray) -> np.ndarray:
    """Density inversion with automatic black and white points per channel.

    A channel with no range (flat, all black or all white) is returned unchanged.
    """

    def density(x: np.ndarray) -> np.ndarray:
        return -np.log10(np.clip(x, MIN_TRANSMITTANCE, 1.0))

    sample = density(_sample(rgb))
    low = np.percentile(sample, NEGATIVE_PERCENTILES[0], axis=(0, 1))
    high = np.percentile(sample, NEGATIVE_PERCENTILES[1], axis=(0, 1))
    span = high - low
    usable = span > MIN_SPAN
    safe_span = np.where(usable, span, 1.0)

    def apply(x: np.ndarray) -> np.ndarray:
        return np.where(usable, np.clip((density(x) - low) / safe_span, 0.0, 1.0), x)

    return map_strips(rgb, apply)
