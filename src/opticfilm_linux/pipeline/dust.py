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
CONTEXT_RADII = 3
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


def remove_dust(rgb: np.ndarray, ir: np.ndarray | None, *, dpi: int = 3600) -> np.ndarray:
    """Inpaint infrared-detected defects in ``rgb``; returns a new array.

    Each connected defect is repaired in its own patch (its bounding box plus context), so the
    result does not depend on where the image happens to be cut and memory stays bounded by the
    size of the defects.
    """
    if ir is None:
        return rgb.copy()
    scale = dpi / REFERENCE_DPI
    dilate = max(1, round(DILATE_PX_AT_REFERENCE * scale))
    mask = defect_mask(ir, dilate_px=dilate, median_px=_odd(MEDIAN_PX_AT_REFERENCE * scale))
    out = rgb.copy()
    if not mask.any():
        return out
    radius = max(INPAINT_RADIUS_AT_REFERENCE, round(INPAINT_RADIUS_AT_REFERENCE * scale))
    pad = CONTEXT_RADII * radius + 4
    rgb8 = (rgb >> 8).astype(np.uint8)
    mask8 = mask.astype(np.uint8) * 255
    height, width = mask.shape
    contours, _ = cv2.findContours(mask8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        py0, py1 = max(0, y - pad), min(height, y + h + pad)
        px0, px1 = max(0, x - pad), min(width, x + w + pad)
        patch = cv2.inpaint(
            rgb8[py0:py1, px0:px1], mask8[py0:py1, px0:px1], radius, cv2.INPAINT_TELEA
        )
        core = (slice(y, y + h), slice(x, x + w))
        patch_core = patch[y - py0 : y + h - py0, x - px0 : x + w - px0]
        defect = mask[core]
        out[core][defect] = patch_core[defect].astype(np.uint16) * UINT8_TO_UINT16
    return out
