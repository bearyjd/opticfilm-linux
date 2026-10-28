"""Sharpening and grain reduction on uint16 RGB arrays.

The scanner's optical blur and the film grain measure more pixels as dpi rises, so both kernels
are defined in pixels at 600 dpi and grow linearly with dpi. The scale is capped at 7200 dpi
(the scanner's maximum) to bound runtime and memory.
"""

import math

import cv2
import numpy as np

from opticfilm_linux.pipeline.arrays import map_strips, to_float
from opticfilm_linux.pipeline.dust import REFERENCE_DPI

GRAIN_SIGMA_COLOR = 0.1
GRAIN_SIGMA_SPACE = 2.5
MAX_SCALE = 12.0  # 7200 dpi relative to the 600 dpi reference
# Above this scale the grain filter runs on an image shrunk by a whole factor instead, so its
# cost stays that of the reference size (a bilateral filter costs the square of its radius).
GRAIN_DIRECT_SCALE = 2.0


def dpi_scale(dpi: int) -> float:
    """Kernel scale for ``dpi`` relative to 600 dpi, capped at ``MAX_SCALE``."""
    if dpi <= 0:
        raise ValueError(f"dpi must be positive, got {dpi}")
    return min(dpi / REFERENCE_DPI, MAX_SCALE)


def sharpen(
    rgb: np.ndarray, radius_px: float = 1.5, amount: float = 0.5, dpi: int = REFERENCE_DPI
) -> np.ndarray:
    """Unsharp mask: add ``amount`` of the difference from a Gaussian blur.

    ``radius_px`` is the radius at 600 dpi; the radius used grows in proportion to ``dpi``.
    """
    scale = dpi_scale(dpi)
    if amount == 0:
        return rgb.copy()
    radius = radius_px * scale

    def apply(x: np.ndarray) -> np.ndarray:
        return x + amount * (x - cv2.GaussianBlur(x, (0, 0), radius))

    return map_strips(rgb, apply, margin=math.ceil(4 * radius) + 1)


def reduce_grain(rgb: np.ndarray, strength: float = 0.3, dpi: int = REFERENCE_DPI) -> np.ndarray:
    """Edge-preserving smoothing blended in by ``strength`` (0 returns an equal copy).

    The filter width is 2.5 px at 600 dpi and grows in proportion to ``dpi``.
    """
    scale = dpi_scale(dpi)
    if strength == 0:
        return rgb.copy()
    factor = int(scale) if scale >= GRAIN_DIRECT_SCALE else 1
    height, width = rgb.shape[:2]
    if factor > 1 and height >= factor and width >= factor:
        return _reduce_grain_shrunk(rgb, strength, scale, factor)
    sigma_space = GRAIN_SIGMA_SPACE * scale

    def apply(x: np.ndarray) -> np.ndarray:
        smooth = cv2.bilateralFilter(x, -1, GRAIN_SIGMA_COLOR, sigma_space)
        return (1.0 - strength) * x + strength * smooth

    return map_strips(rgb, apply, margin=math.ceil(3 * sigma_space) + 2)


def _reduce_grain_shrunk(rgb: np.ndarray, strength: float, scale: float, factor: int) -> np.ndarray:
    """Filter an image shrunk ``factor`` times (area average, still 16-bit) and blend it back.

    The smoothed layer is enlarged again strip by strip, so no full-size float image exists.
    """
    height, width = rgb.shape[:2]
    small_h, small_w = height // factor, width // factor
    small = cv2.resize(
        rgb[: small_h * factor, : small_w * factor],
        (small_w, small_h),
        interpolation=cv2.INTER_AREA,
    )
    sigma_space = GRAIN_SIGMA_SPACE * scale / factor

    def bilateral(x: np.ndarray) -> np.ndarray:
        return cv2.bilateralFilter(x, -1, GRAIN_SIGMA_COLOR, sigma_space)

    smooth = to_float(map_strips(small, bilateral, margin=math.ceil(3 * sigma_space) + 2))
    cols = np.minimum(np.arange(width), small_w * factor - 1)

    def blend(x: np.ndarray, p0: int, p1: int) -> np.ndarray:
        first = max(0, p0 // factor - 1)  # one extra row of context for the linear enlargement
        last = min(small_h, -(-p1 // factor) + 1)
        band = cv2.resize(
            smooth[first:last],
            (small_w * factor, (last - first) * factor),
            interpolation=cv2.INTER_LINEAR,
        )
        rows = np.minimum(np.arange(p0, p1), small_h * factor - 1) - first * factor
        return (1.0 - strength) * x + strength * band[rows][:, cols]

    return map_strips(rgb, blend, with_rows=True)
