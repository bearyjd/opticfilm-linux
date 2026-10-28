"""Infrared dust and scratch removal.

Dust and scratches block infrared light while film dyes do not, so defects show up as dark
spots in the infrared plane. They are masked there and inpainted in the colour image.

Only the median window that finds a defect scales with the scan resolution. The infrared and
colour planes register to 0 px, so the mask needs to grow by a fixed pixel or two to cover a
speck's soft edge, and a small fixed inpaint radius fills it: scaling both with dpi (12 px and
18 px at 3600 dpi) repainted about 40 times the defect area and smeared the edges around it.
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
DILATE_PX = DILATE_PX_AT_REFERENCE  # at every dpi: the planes register to 0 px
INPAINT_RADIUS_PX = INPAINT_RADIUS_AT_REFERENCE  # at every dpi: the fill propagates inwards anyway
CONTEXT_RADII = 3
UINT8_TO_UINT16 = 257  # the old 8-bit repair; kept for the frozen reference in tests
INPAINT_WORKERS = MAX_WORKERS  # cv2.inpaint itself runs on one thread
# Patches in flight hold at most this many patch bytes (and at most a quarter of the image's
# own size); one patch always runs, however big. A long scratch has a huge bounding box.
PATCH_BUDGET_BYTES = 256 * 1024 * 1024
# Infrared dips on strong visible edges are not dust (see ``_on_visible_edges``). On the real
# 600 dpi scans these dips are one or two pixels wide; the reach is in sensor pixels, like the
# dilation, because the planes register to 0 px.
EDGE_REACH_PX = (1, 2)
EDGE_CONTRAST = 0.5  # the brighter neighbour is at least twice the darker one
EDGE_FLOOR = 0.02 * 65535  # and brighter by 2% of full scale, so near-black noise is no edge
EDGE_RIM_PX = 2 * max(EDGE_REACH_PX)  # an edge-like rim this wide, joined to a speck, is kept
_SQUARE_3 = np.ones((3, 3), np.uint8)
EDGE_STRIP_ROWS = 64
SPECK_DEPTH = 0.1  # a defect darkening the colour by 10% or more...
SPECK_FLOOR = 0.01 * 65535  # ...and by more than noise in dark areas does is dust, edge or not
SPECK_BOX_WINDOWS = 2  # larger defects are not judged as specks (bounds the work)
Box = tuple[int, int, int, int]
Repaired = tuple[tuple[slice, slice], np.ndarray, np.ndarray]


def _odd(value: float) -> int:
    rounded = max(3, round(value))
    return rounded if rounded % 2 else rounded + 1


def defect_mask(
    ir: np.ndarray,
    sensitivity: float = 0.15,
    dilate_px: int = 2,
    median_px: int = 21,
    visible: np.ndarray | None = None,
) -> np.ndarray:
    """True where ``ir`` is darker than its local median by more than ``sensitivity``.

    Given the ``visible`` image (colour or grey, registered to ``ir``), a dip that sits on a strong
    step of the visible image is not dust (see ``_on_visible_edges``) and is left out."""
    if visible is not None:
        _check_visible(visible, ir.shape)
    ir8 = (ir >> 8).astype(np.uint8)
    median = cv2.medianBlur(ir8, _odd(median_px))
    drop = median.astype(np.int16) - ir8.astype(np.int16)
    found = drop > sensitivity * 255
    mask = found.astype(np.uint8)
    if visible is not None and found.any():
        edges = _on_visible_edges(visible, found)
        if edges.any():
            edges &= ~_in_darker_regions(visible, mask, edges, _odd(median_px))
        kept = (found & ~edges).astype(np.uint8)
        # A speck joined to an edge dip is judged with it, so its own edge-like rim may be lost:
        # grow the kept pixels back through found ones only, EDGE_RIM_PX steps, so the speck is
        # masked whole while the dip further along the edge stays out.
        for _ in range(EDGE_RIM_PX):
            kept = cv2.dilate(kept, _SQUARE_3) & mask
        mask = kept
    if dilate_px > 0 and mask.any():
        mask = cv2.dilate(mask, _disc(dilate_px))
    return mask.astype(bool)


def _check_visible(visible: np.ndarray, shape: tuple[int, ...]) -> None:
    """The edge thresholds are on the scanner's 16-bit scale, grey or three colour channels."""
    if visible.dtype != np.uint16 or not (
        visible.ndim == 2 or (visible.ndim == 3 and visible.shape[2] == 3)
    ):
        raise ValueError(
            f"visible image must be 16-bit grey or RGB, not {visible.dtype} {visible.shape}"
        )
    if visible.shape[:2] != shape:
        raise ValueError(f"visible image {visible.shape[:2]} does not match infrared {shape}")


def _in_darker_regions(
    visible: np.ndarray, mask: np.ndarray, edges: np.ndarray, widest_px: int
) -> np.ndarray:
    """The ``edges`` pixels whose connected defect is clearly darker, in the visible image, than
    the pixels around it: a speck or scratch that darkens the colour evenly (a plateau, so no
    single pixel is a local minimum), not a dip along an edge, which lies between the edge's
    dark and bright sides. "Clearly darker": the defect's median is below the 10th percentile
    of its one-pixel ring by SPECK_DEPTH of it and by SPECK_FLOOR. Every defect is judged,
    including one inside a hole of another (its region includes what it encloses). A defect
    whose bounding box is wider than the median window both ways and covers more than
    SPECK_BOX_WINDOWS windows a side is not judged (edges joined into networks can span the
    frame), which bounds the work per defect; a long straight or a bent scratch is judged.
    A speck joined to an edge dip is judged with the dip; ``defect_mask`` grows its kept
    pixels back over its rim."""
    out = np.zeros(edges.shape, bool)
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    height, width = mask.shape
    largest = (SPECK_BOX_WINDOWS * widest_px) ** 2
    for contour, (_, _, _, parent) in zip(contours, hierarchy[0], strict=True):
        if parent >= 0:  # the outline of a hole, not a defect
            continue
        x, y, w, h = cv2.boundingRect(contour)
        too_big = min(w, h) > widest_px and w * h > largest
        if too_big or not edges[y : y + h, x : x + w].any():
            continue
        y0, y1, x0, x1 = max(0, y - 1), min(height, y + h + 1), max(0, x - 1), min(width, x + w + 1)
        region = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.drawContours(region, [contour], -1, 1, cv2.FILLED, offset=(-x0, -y0))
        region &= mask[y0:y1, x0:x1]
        inside = region.astype(bool)
        ring = cv2.dilate(region, _SQUARE_3).astype(bool) & ~mask[y0:y1, x0:x1].astype(bool)
        if not ring.any():
            continue
        level = _visible_level(visible[y0:y1, x0:x1])
        around = float(np.percentile(level[ring], 10))
        if np.median(level[inside]) < around - max(SPECK_DEPTH * around, SPECK_FLOOR):
            out[y0:y1, x0:x1] |= inside & edges[y0:y1, x0:x1]
    return out


def _disc(radius: int) -> np.ndarray:
    size = 2 * radius + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def defect_mask_for(ir: np.ndarray, dpi: int, visible: np.ndarray | None = None) -> np.ndarray:
    """``defect_mask`` with its median window scaled to the scan resolution (not its dilation)."""
    scale = dpi / REFERENCE_DPI
    return defect_mask(
        ir,
        dilate_px=DILATE_PX,
        median_px=_odd(MEDIAN_PX_AT_REFERENCE * scale),
        visible=visible,
    )


def _on_visible_edges(visible: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    """True at candidate pixels the visible image explains as an edge rather than a speck.

    Real scans dip in the infrared on the one or two pixels where the visible image steps from
    dark to bright, while the infrared on both sides is level. A pixel counts as on an edge when,
    ``d`` pixels to either side along a row or a column (``d`` in ``EDGE_REACH_PX``), its visible
    level lies between its two neighbours' and the brighter one is at least ``1 / (1 -
    EDGE_CONTRAST)`` times the darker (and brighter by ``EDGE_FLOOR``, so noise in near-black
    areas is no edge). Dust darkens the visible image too, so a pixel darker than all four
    neighbours at one of those distances is a speck and is never dropped.

    Works in strips of rows, so the extra memory is one strip of float copies, not the image's."""
    reach = max(EDGE_REACH_PX)
    height = visible.shape[0]
    out = np.zeros(candidates.shape, bool)
    for top in range(0, height, EDGE_STRIP_ROWS):
        bottom = min(height, top + EDGE_STRIP_ROWS)
        if not candidates[top:bottom].any():
            continue
        y0, y1 = max(0, top - reach), min(height, bottom + reach)
        level = _visible_level(visible[y0:y1])
        padded = np.pad(level, reach, mode="edge")
        edge = np.zeros((bottom - top, level.shape[1]), bool)
        speck = np.zeros_like(edge)
        rows = slice(top - y0 + reach, bottom - y0 + reach)
        cols = slice(reach, reach + level.shape[1])
        centre = padded[rows, cols]
        for d in EDGE_REACH_PX:
            darker_than_all = np.ones_like(edge)
            for dy, dx in ((0, d), (d, 0)):
                before = padded[rows.start - dy : rows.stop - dy, cols.start - dx : cols.stop - dx]
                after = padded[rows.start + dy : rows.stop + dy, cols.start + dx : cols.stop + dx]
                low, high = np.minimum(before, after), np.maximum(before, after)
                step = high - low
                edge |= (
                    (centre >= low)
                    & (centre <= high)
                    & (step >= EDGE_CONTRAST * high)
                    & (step >= EDGE_FLOOR)
                )
                darker_than_all &= (centre < before) & (centre < after)
            speck |= darker_than_all
        out[top:bottom] = edge & ~speck & candidates[top:bottom]
    return out


def _visible_level(rows: np.ndarray) -> np.ndarray:
    """One float32 level per pixel: the mean of the colour channels, or the grey value."""
    if rows.ndim == 2:
        return rows.astype(np.float32)
    return rows.mean(axis=2, dtype=np.float32)


def _repair(
    rgb: np.ndarray,
    mask: np.ndarray,
    mask8: np.ndarray,
    box: Box,
    radius: int,
) -> Repaired:
    """Inpaint one defect's patch; returns its core box, its defect pixels there and their values.
    Reads only ``rgb`` (never the output), so patches can be computed in any order. OpenCV
    inpaints 16-bit images one channel at a time, which keeps the repair at full precision."""
    x, y, w, h = box
    pad = CONTEXT_RADII * radius + 4
    height, width = mask.shape
    py0, py1 = max(0, y - pad), min(height, y + h + pad)
    px0, px1 = max(0, x - pad), min(width, x + w + pad)
    patch_mask = mask8[py0:py1, px0:px1]
    core = (slice(y, y + h), slice(x, x + w))
    defect = mask[core]
    values = np.empty((int(np.count_nonzero(defect)), rgb.shape[2]), np.uint16)
    for channel in range(rgb.shape[2]):
        plane = np.ascontiguousarray(rgb[py0:py1, px0:px1, channel])  # only the patch
        filled = cv2.inpaint(plane, patch_mask, radius, cv2.INPAINT_TELEA)
        values[:, channel] = filled[y - py0 : y + h - py0, x - px0 : x + w - px0][defect]
    return core, defect, values


def remove_dust(rgb: np.ndarray, ir: np.ndarray | None, *, dpi: int = 3600) -> np.ndarray:
    """Inpaint infrared-detected defects in ``rgb``; returns a new array.

    Each connected defect is repaired in its own patch (its bounding box plus context), so the
    result does not depend on where the image happens to be cut and memory stays bounded by the
    size of the defects. Inpainting cost grows with defect area times radius squared, so patches
    are inpainted on a few threads; they are written back in contour order, exactly
    as one thread would, because neighbouring bounding boxes can overlap.
    """
    if ir is None:
        return rgb.copy()
    mask = defect_mask_for(ir, dpi, visible=rgb)
    out = rgb.copy()
    if not mask.any():
        return out
    radius = INPAINT_RADIUS_PX
    mask8 = mask.astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(contour) for contour in contours]
    _repair_in_order(boxes, lambda box: _repair(rgb, mask, mask8, box, radius), radius, out)
    return out


def _patch_bytes(box: Box, radius: int) -> int:
    """Bytes a patch holds while it is inpainted (its box plus context, one 16-bit channel in
    and one out, the mask, and the repaired values), before edge clipping."""
    pad = 2 * (CONTEXT_RADII * radius + 4)
    return (box[2] + pad) * (box[3] + pad) * (2 + 2 + 1) + box[2] * box[3] * 2 * 3


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
