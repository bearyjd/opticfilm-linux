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


def split_slides(sweep: RawSweep, expected: int = 4) -> tuple[Slide, ...]:
    target = APERTURE_LENGTH_MM / MM_PER_INCH * sweep.dpi
    low, high = target * (1 - LENGTH_TOLERANCE), target * (1 + LENGTH_TOLERANCE)
    runs = _openings(_profile(sweep), low, high, expected)
    if len(runs) != expected:
        raise SlideSplitError(f"expected {expected} slide openings, found {len(runs)}")
    width = sweep.rgb.shape[1]
    return tuple(
        Slide(
            index=index,
            rgb=sweep.rgb[start:end],
            ir=None if sweep.ir is None else sweep.ir[start:end],
            box=Box(x=0, y=start, w=width, h=end - start),
        )
        for index, (start, end) in enumerate(runs)
    )


def is_blank(slide: Slide) -> bool:
    """A clear window: saturated bright and almost perfectly flat.

    A bright but real slide (snow, sky, overexposure) stays below the saturation level.
    """
    luminance = slide.rgb[::8, ::8].mean(axis=2) / FULL_SCALE
    return bool(luminance.mean() > BLANK_MIN_MEAN and luminance.std() < BLANK_MAX_STD)
