"""Conversions between uint16 images and float32 values in [0, 1], and strip-wise processing."""

from collections.abc import Callable

import numpy as np

FULL_SCALE = 65535.0
STRIP_ROWS = 128


def to_float(image: np.ndarray) -> np.ndarray:
    return image.astype(np.float32) / FULL_SCALE


def to_uint16(values: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(values * FULL_SCALE), 0, FULL_SCALE).astype(np.uint16)


def map_strips(
    image: np.ndarray, func: Callable[[np.ndarray], np.ndarray], margin: int = 0
) -> np.ndarray:
    """Apply ``func`` (float32 in [0, 1] to float) to horizontal strips; returns a new uint16 image.

    Each strip is padded with ``margin`` rows of context on both sides, so neighbourhood filters
    give the same result as a single pass while only a strip of float temporaries is alive.
    """
    height = image.shape[0]
    out = np.empty(image.shape, dtype=np.uint16)
    for y0 in range(0, height, STRIP_ROWS):
        y1 = min(y0 + STRIP_ROWS, height)
        p0, p1 = max(0, y0 - margin), min(height, y1 + margin)
        result = func(to_float(image[p0:p1]))
        out[y0:y1] = to_uint16(result[y0 - p0 : y1 - p0])
    return out
