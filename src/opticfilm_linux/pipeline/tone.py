"""Exposure, colour-fade restoration and negative inversion on uint16 RGB arrays."""

import numpy as np

from opticfilm_linux.pipeline.arrays import map_strips, to_float

SAMPLE_PIXELS = 250_000
MIN_SPAN = 1e-4
TARGET_MEDIAN = 0.45
GAMMA_LIMITS = (0.5, 2.0)  # tone-mapped input: inverted negatives, finished images
# Linear scanner positives sit with their median at 2-5 % of full range and want gammas of about
# 0.2-0.3; 0.2 lifts a mid level of 1.8 % to the target, and anything darker is mostly noise.
LINEAR_GAMMA_LIMITS = (0.2, 2.0)
BLACK_PERCENTILE = 0.5
BLACK_INSET = 0.2  # black levels come from the central 60 % of the frame, clear of any mount
# The scanner's channels differ by about 0.011 of full range; a larger difference is the scene's
# own colour (a red sunset has no blue-free shadows), which this step must not take away.
MAX_BLACK_OFFSET = 0.03
EXPOSURE_PERCENTILES = (0.1, 99.9)
NEGATIVE_PERCENTILES = (0.5, 99.5)
MIN_TRANSMITTANCE = 1e-4


def _sample(image: np.ndarray) -> np.ndarray:
    """A strided subsample of a uint16 image, as float32 in [0, 1]; statistics come from this."""
    step = max(1, int(np.sqrt(image.shape[0] * image.shape[1] / SAMPLE_PIXELS)))
    return to_float(image[::step, ::step])


def auto_exposure(rgb: np.ndarray, gamma_limits: tuple[float, float] = GAMMA_LIMITS) -> np.ndarray:
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
    gamma = float(np.clip(np.log(TARGET_MEDIAN) / np.log(mid), *gamma_limits))
    return map_strips(rgb, lambda x: np.clip((x - low) / (high - low), 0.0, 1.0) ** gamma)


def subtract_black(rgb: np.ndarray) -> np.ndarray:
    """Bring every channel of linear data down to the darkest channel's black level.

    Raw scans have a slightly different black level per channel. A shared gamma bends each
    offset differently, a cast that no later stretch undoes, so the per-channel part goes first;
    the shared part is left to ``auto_exposure``, which takes it from the whole frame.
    Black levels are a low percentile of the frame's interior, so dark mount at the edges
    (cropped or not) never sets them. A channel's offset is also never larger than the same
    offset measured on the whole frame: a scanner offset is in every pixel (the opaque mount
    shows it too), while coloured content in the middle must not push neutral shadows near the
    edges below zero. A channel with no range in the interior (a uniform or fully clipped
    channel) is left alone, offsets are capped at ``MAX_BLACK_OFFSET``, and the subtraction is
    in float, so nothing wraps below zero.
    """
    sample = _sample(rgb)
    height, width = sample.shape[:2]
    dy, dx = int(height * BLACK_INSET), int(width * BLACK_INSET)
    interior = sample[dy : height - dy, dx : width - dx].reshape(-1, 3)
    black, high = np.percentile(interior, [BLACK_PERCENTILE, 100 - BLACK_PERCENTILE], axis=0)
    usable = high - black > MIN_SPAN
    if not usable.any():
        return rgb.copy()
    whole = np.percentile(sample.reshape(-1, 3), BLACK_PERCENTILE, axis=0)
    offset = np.minimum(black - black[usable].min(), whole - whole[usable].min())
    offset = np.where(usable, np.clip(offset, 0.0, MAX_BLACK_OFFSET), 0.0).astype(np.float32)
    if not offset.any():
        return rgb.copy()
    return map_strips(rgb, lambda x: np.clip(x - offset, 0.0, 1.0))


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


def adjust_brightness(rgb: np.ndarray, ev: float) -> np.ndarray:
    """Scale by 2**ev (photographic stops), clipping at white; ev 0 returns an equal copy."""
    if ev == 0:
        return rgb.copy()
    gain = 2.0**ev
    return map_strips(rgb, lambda x: x * gain)
