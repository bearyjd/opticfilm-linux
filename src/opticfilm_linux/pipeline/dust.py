"""Infrared dust and scratch removal.

Dust and scratches block infrared light while film dyes do not, so defects show up as dark
spots in the infrared plane. They are masked there and inpainted in the colour image.
"""

from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

import cv2
import numpy as np

from opticfilm_linux.pipeline.arrays import MAX_WORKERS

REFERENCE_DPI = 600
MEDIAN_PX_AT_REFERENCE = 21
DILATE_PX_AT_REFERENCE = 2
INPAINT_RADIUS_AT_REFERENCE = 3
CONTEXT_RADII = 3
UINT8_TO_UINT16 = 257
INPAINT_WORKERS = MAX_WORKERS  # cv2.inpaint itself runs on one thread
# Patches in flight hold at most this many 8-bit patch bytes (and at most a quarter of the
# image's own size); one patch always runs, however big. A long scratch has a huge bounding box.
PATCH_BUDGET_BYTES = 256 * 1024 * 1024
Box = tuple[int, int, int, int]
Repaired = tuple[tuple[slice, slice], np.ndarray, np.ndarray]


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


def defect_mask_for(ir: np.ndarray, dpi: int) -> np.ndarray:
    """``defect_mask`` with its window and dilation scaled to the scan resolution."""
    scale = dpi / REFERENCE_DPI
    return defect_mask(
        ir,
        dilate_px=max(1, round(DILATE_PX_AT_REFERENCE * scale)),
        median_px=_odd(MEDIAN_PX_AT_REFERENCE * scale),
    )


def _repair(
    rgb: np.ndarray,
    mask: np.ndarray,
    mask8: np.ndarray,
    box: Box,
    radius: int,
) -> Repaired:
    """Inpaint one defect's patch; returns its core box, its defect pixels there and their values.
    Reads only ``rgb`` (never the output), so patches can be computed in any order."""
    x, y, w, h = box
    pad = CONTEXT_RADII * radius + 4
    height, width = mask.shape
    py0, py1 = max(0, y - pad), min(height, y + h + pad)
    px0, px1 = max(0, x - pad), min(width, x + w + pad)
    patch8 = (rgb[py0:py1, px0:px1] >> 8).astype(np.uint8)  # only the patch, never the slide
    patch = cv2.inpaint(patch8, mask8[py0:py1, px0:px1], radius, cv2.INPAINT_TELEA)
    core = (slice(y, y + h), slice(x, x + w))
    defect = mask[core]
    patch_core = patch[y - py0 : y + h - py0, x - px0 : x + w - px0]
    return core, defect, patch_core[defect].astype(np.uint16) * UINT8_TO_UINT16


def remove_dust(rgb: np.ndarray, ir: np.ndarray | None, *, dpi: int = 3600) -> np.ndarray:
    """Inpaint infrared-detected defects in ``rgb``; returns a new array.

    Each connected defect is repaired in its own patch (its bounding box plus context), so the
    result does not depend on where the image happens to be cut and memory stays bounded by the
    size of the defects. Inpainting cost grows with defect area times radius squared (16x per
    doubling of dpi), so patches are inpainted on a few threads; they are written back in contour
    order, exactly as one thread would, because neighbouring bounding boxes can overlap.
    """
    if ir is None:
        return rgb.copy()
    scale = dpi / REFERENCE_DPI
    mask = defect_mask_for(ir, dpi)
    out = rgb.copy()
    if not mask.any():
        return out
    radius = max(INPAINT_RADIUS_AT_REFERENCE, round(INPAINT_RADIUS_AT_REFERENCE * scale))
    mask8 = mask.astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(contour) for contour in contours]
    _repair_in_order(boxes, lambda box: _repair(rgb, mask, mask8, box, radius), radius, out)
    return out


def _patch_bytes(box: Box, radius: int) -> int:
    """8-bit bytes of a patch (its box plus context, three channels), before edge clipping."""
    pad = 2 * (CONTEXT_RADII * radius + 4)
    return (box[2] + pad) * (box[3] + pad) * 3


def _repair_in_order(
    boxes: list[Box], repair: Callable[[Box], Repaired], radius: int, out: np.ndarray
) -> None:
    """Inpaint patches on a few threads, with the patches in flight bounded by bytes (one
    always runs), submitted lazily, and written back in contour order as one thread would."""
    budget = min(PATCH_BUDGET_BYTES, out.nbytes // 4)
    workers = max(1, min(INPAINT_WORKERS, len(boxes)))
    window: deque[tuple[Future[Repaired], int]] = deque()
    in_flight = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="inpaint") as pool:
        for box in boxes:
            size = _patch_bytes(box, radius)
            while window and (in_flight + size > budget or len(window) >= 2 * workers):
                in_flight -= _write_back(window.popleft(), out)
            window.append((pool.submit(repair, box), size))
            in_flight += size
        while window:
            _write_back(window.popleft(), out)


def _write_back(entry: "tuple[Future[Repaired], int]", out: np.ndarray) -> int:
    future, size = entry
    core, defect, values = future.result()
    out[core][defect] = values
    return size
