"""Sharpening and grain reduction on uint16 RGB arrays."""

import cv2
import numpy as np

from opticfilm_linux.pipeline.arrays import to_float, to_uint16

GRAIN_SIGMA_COLOR = 0.1
GRAIN_SIGMA_SPACE = 2.5


def sharpen(rgb: np.ndarray, radius_px: float = 1.5, amount: float = 0.5) -> np.ndarray:
    """Unsharp mask: add ``amount`` of the difference from a Gaussian blur."""
    if amount == 0:
        return rgb.copy()
    x = to_float(rgb)
    blurred = cv2.GaussianBlur(x, (0, 0), radius_px)
    return to_uint16(x + amount * (x - blurred))


def reduce_grain(rgb: np.ndarray, strength: float = 0.3) -> np.ndarray:
    """Edge-preserving smoothing blended in by ``strength`` (0 returns an equal copy)."""
    if strength == 0:
        return rgb.copy()
    x = to_float(rgb)
    smooth = cv2.bilateralFilter(x, -1, GRAIN_SIGMA_COLOR, GRAIN_SIGMA_SPACE)
    return to_uint16((1.0 - strength) * x + strength * smooth)
