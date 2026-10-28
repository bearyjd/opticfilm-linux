"""Split a whole-holder sweep into slides and spot empty windows.

The openings are found along the sweep from a 1-D brightness profile. Infrared is preferred:
the opaque mount blocks it while film and clear windows pass it. Without infrared the
luminance profile is used instead.
"""

import cv2
import numpy as np

from opticfilm_linux.model import Box, RawSweep, Slide

MM_PER_INCH = 25.4
# Aperture length along the sweep, as measured by the community driver on one unit.
APERTURE_LENGTH_MM = 22.75
LENGTH_TOLERANCE = 0.25
FULL_SCALE = 65535.0
BLANK_MIN_MEAN = 0.95
MOUNT_MARGIN = 0.005
BLANK_MAX_STD = 0.02
# Across the sweep: the window is where columns stay above the midpoint between mount and window.
WINDOW_MIN_CONTRAST = 0.2
WINDOW_COLUMN_PERCENTILES = (2.0, 98.0)
WINDOW_LEVEL = 0.25  # of the way from mount to window; keeps dark picture content inside
WINDOW_ROW_QUORUM = 0.9
WINDOW_MIN_FRACTION = 0.5
# Dropped from each mount-side edge: rounded corners and edge shadow.
EDGE_INSET_MM = 0.15
EDGE_INSET_MIN_PX = 2


class SlideSplitError(ValueError):
    """The expected number of slide openings could not be found in the sweep."""


def _profile(sweep: RawSweep) -> np.ndarray:
    if sweep.ir is not None:
        return sweep.ir.mean(axis=1, dtype=np.float64)
    return sweep.rgb.mean(axis=(1, 2), dtype=np.float64)


def _runs_above(profile: np.ndarray, threshold: float) -> list[tuple[int, int]]:
    mask = np.concatenate(([0], (profile > threshold).astype(np.int8), [0]))
    edges = np.diff(mask)
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    return [(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]


def _thresholds(profile: np.ndarray) -> list[float]:
    """Otsu first; then just above the mount's own level, which keeps a very dark slide in."""
    scaled = np.clip(profile / FULL_SCALE * 255.0, 0, 255).astype(np.uint8).reshape(-1, 1)
    otsu, _ = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return [float(otsu) / 255.0 * FULL_SCALE, float(profile.min()) + MOUNT_MARGIN * FULL_SCALE]


def _openings(profile: np.ndarray, low: float, high: float, expected: int) -> list[tuple[int, int]]:
    best: list[tuple[int, int]] = []
    for threshold in _thresholds(profile):
        runs = [(a, b) for a, b in _runs_above(profile, threshold) if low <= b - a <= high]
        if len(runs) == expected:
            return runs
        best = best or runs
    return best


def edge_inset_px(dpi: int) -> int:
    return max(EDGE_INSET_MIN_PX, round(EDGE_INSET_MM / MM_PER_INCH * dpi))


def _longest_run(mask: np.ndarray) -> tuple[int, int] | None:
    runs = _runs_above(mask.astype(np.float64), 0.5)
    return max(runs, key=lambda run: run[1] - run[0]) if runs else None


def _window_columns(plane: np.ndarray, dpi: int) -> tuple[int, int]:
    """Column span ``(x, w)`` of the bright window in one opening; full width if unclear."""
    width = plane.shape[1]
    full = (0, width)
    if plane.size == 0:
        return full
    columns = plane.mean(axis=0, dtype=np.float64)
    low, high = np.percentile(columns, WINDOW_COLUMN_PERCENTILES)
    if high - low < WINDOW_MIN_CONTRAST * FULL_SCALE:
        return full
    bright = (plane > low + WINDOW_LEVEL * (high - low)).mean(axis=0) >= WINDOW_ROW_QUORUM
    run = _longest_run(bright)
    if run is None:
        return full
    inset = edge_inset_px(dpi)
    start = run[0] + inset if run[0] > 0 else 0
    end = run[1] - inset if run[1] < width else width
    if end - start < WINDOW_MIN_FRACTION * width:
        return full
    return start, end - start


def split_slides(sweep: RawSweep, expected: int = 4) -> tuple[Slide, ...]:
    target = APERTURE_LENGTH_MM / MM_PER_INCH * sweep.dpi
    low, high = target * (1 - LENGTH_TOLERANCE), target * (1 + LENGTH_TOLERANCE)
    runs = _openings(_profile(sweep), low, high, expected)
    if len(runs) != expected:
        raise SlideSplitError(f"expected {expected} slide openings, found {len(runs)}")
    plane = sweep.ir if sweep.ir is not None else sweep.rgb.mean(axis=2, dtype=np.float32)
    slides = []
    for index, (start, end) in enumerate(runs):
        x, w = _window_columns(plane[start:end], sweep.dpi)
        slides.append(
            Slide(
                index=index,
                rgb=sweep.rgb[start:end, x : x + w],
                ir=None if sweep.ir is None else sweep.ir[start:end, x : x + w],
                box=Box(x=x, y=start, w=w, h=end - start),
            )
        )
    return tuple(slides)


def is_blank(slide: Slide) -> bool:
    """A clear window: saturated bright and almost perfectly flat.

    A bright but real slide (snow, sky, overexposure) stays below the saturation level.
    """
    luminance = slide.rgb[::8, ::8].mean(axis=2) / FULL_SCALE
    return bool(luminance.mean() > BLANK_MIN_MEAN and luminance.std() < BLANK_MAX_STD)
