"""Frozen copies of the serial implementations from before the performance work (main at c7062fc).

Tests run them beside the current code on the same machine, so "byte-identical to before" is
checked on every CPU, not only where historical hashes happen to reproduce. Do not optimise or
"fix" this file: it is the yardstick.
"""

from collections.abc import Callable

import cv2
import numpy as np

from opticfilm_linux.pipeline.arrays import STRIP_ROWS, to_float, to_uint16
from opticfilm_linux.pipeline.dust import (
    CONTEXT_RADII,
    INPAINT_RADIUS_AT_REFERENCE,
    REFERENCE_DPI,
    UINT8_TO_UINT16,
    defect_mask_for,
)


def map_strips(
    image: np.ndarray, func: Callable[[np.ndarray], np.ndarray], margin: int = 0
) -> np.ndarray:
    height = image.shape[0]
    out = np.empty(image.shape, dtype=np.uint16)
    for y0 in range(0, height, STRIP_ROWS):
        y1 = min(y0 + STRIP_ROWS, height)
        p0, p1 = max(0, y0 - margin), min(height, y1 + margin)
        result = func(to_float(image[p0:p1]))
        out[y0:y1] = to_uint16(result[y0 - p0 : y1 - p0])
    return out


def remove_dust(rgb: np.ndarray, ir: np.ndarray | None, *, dpi: int = 3600) -> np.ndarray:
    if ir is None:
        return rgb.copy()
    scale = dpi / REFERENCE_DPI
    mask = defect_mask_for(ir, dpi)
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


def use_reference(monkeypatch) -> None:
    """Route the pipeline through the frozen implementations (the steps' own code is unchanged)."""
    from opticfilm_linux.pipeline import detail, run, tone

    monkeypatch.setattr(tone, "map_strips", map_strips)
    monkeypatch.setattr(detail, "map_strips", map_strips)
    monkeypatch.setattr(run, "remove_dust", remove_dust)
