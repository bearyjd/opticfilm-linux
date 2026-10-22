"""Conversions between uint16 images and float32 values in [0, 1]."""

import numpy as np

FULL_SCALE = 65535.0


def to_float(image: np.ndarray) -> np.ndarray:
    return image.astype(np.float32) / FULL_SCALE


def to_uint16(values: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(values * FULL_SCALE), 0, FULL_SCALE).astype(np.uint16)
