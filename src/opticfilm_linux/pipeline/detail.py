"""Sharpening and grain reduction on uint16 RGB arrays."""

import math

import cv2
import numpy as np

from opticfilm_linux.pipeline.arrays import map_strips

GRAIN_SIGMA_COLOR = 0.1
GRAIN_SIGMA_SPACE = 2.5


def sharpen(rgb: np.ndarray, radius_px: float = 1.5, amount: float = 0.5) -> np.ndarray:
    """Unsharp mask: add ``amount`` of the difference from a Gaussian blur."""
    if amount == 0:
        return rgb.copy()

    def apply(x: np.ndarray) -> np.ndarray:
        return x + amount * (x - cv2.GaussianBlur(x, (0, 0), radius_px))

    return map_strips(rgb, apply, margin=math.ceil(4 * radius_px) + 1)


def reduce_grain(rgb: np.ndarray, strength: float = 0.3) -> np.ndarray:
    """Edge-preserving smoothing blended in by ``strength`` (0 returns an equal copy)."""
    if strength == 0:
        return rgb.copy()

    def apply(x: np.ndarray) -> np.ndarray:
        smooth = cv2.bilateralFilter(x, -1, GRAIN_SIGMA_COLOR, GRAIN_SIGMA_SPACE)
        return (1.0 - strength) * x + strength * smooth

    return map_strips(rgb, apply, margin=math.ceil(3 * GRAIN_SIGMA_SPACE) + 2)
