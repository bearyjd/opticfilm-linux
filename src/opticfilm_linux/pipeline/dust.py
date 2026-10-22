"""Infrared dust and scratch removal.

Dust and scratches block infrared light while film dyes do not, so defects show up as dark
spots in the infrared plane. They are masked there and inpainted in the colour image.
"""

import cv2
import numpy as np

REFERENCE_DPI = 600
MEDIAN_PX_AT_REFERENCE = 21
DILATE_PX_AT_REFERENCE = 2
INPAINT_RADIUS_AT_REFERENCE = 3
TILE_OVERLAP = 16
UINT8_TO_UINT16 = 257


def _odd(value: float) -> int:
    rounded = max(3, round(value))
    return rounded if rounded % 2 else rounded + 1


def defect_mask(
    ir: np.ndarray, sensitivity: float = 0.15, dilate_px: int = 2, median_px: int = 21
) -> np.ndarray:
    """True where ``ir`` is darker than its local median by more than ``sensitivity``."""
    ir8 = (ir >> 8).astype(np.uint8)
    median = cv2.medianBlur(ir8, _odd(median_px))
    drop = median.astype(np.int16) - ir8.astype(np.int16)
    mask = (drop > sensitivity * 255).astype(np.uint8)
    if dilate_px > 0 and mask.any():
        size = 2 * dilate_px + 1
        mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    return mask.astype(bool)


def remove_dust(
    rgb: np.ndarray, ir: np.ndarray | None, *, dpi: int = 3600, tile: int = 1024
) -> np.ndarray:
    """Inpaint infrared-detected defects in ``rgb``; returns a new array."""
    if ir is None:
        return rgb.copy()
    scale = dpi / REFERENCE_DPI
    mask = defect_mask(
        ir,
        dilate_px=max(1, round(DILATE_PX_AT_REFERENCE * scale)),
        median_px=_odd(MEDIAN_PX_AT_REFERENCE * scale),
    )
    out = rgb.copy()
    if not mask.any():
        return out
    radius = max(INPAINT_RADIUS_AT_REFERENCE, round(INPAINT_RADIUS_AT_REFERENCE * scale))
    rgb8 = (rgb >> 8).astype(np.uint8)
    mask8 = mask.astype(np.uint8) * 255
    height, width = mask.shape
    for y0 in range(0, height, tile):
        for x0 in range(0, width, tile):
            y1, x1 = min(y0 + tile, height), min(x0 + tile, width)
            if not mask[y0:y1, x0:x1].any():
                continue
            py0, py1 = max(0, y0 - TILE_OVERLAP), min(height, y1 + TILE_OVERLAP)
            px0, px1 = max(0, x0 - TILE_OVERLAP), min(width, x1 + TILE_OVERLAP)
            patch = cv2.inpaint(
                rgb8[py0:py1, px0:px1], mask8[py0:py1, px0:px1], radius, cv2.INPAINT_TELEA
            )
            core = patch[y0 - py0 : y1 - py0, x0 - px0 : x1 - px0]
            tile_mask = mask[y0:y1, x0:x1]
            out[y0:y1, x0:x1][tile_mask] = core[tile_mask].astype(np.uint16) * UINT8_TO_UINT16
    return out
