"""Synthetic full-window frames in the real driver's geometry, at any resolution.

A frame is what the driver writes for one holder opening: lines along the transport, the slide's
long side along the lines, dark holder around a bright opening. At 600 dpi a frame is 927 lines x
876 px (39.2 x 37 mm); other resolutions scale it. Frames are generated and written strip by
strip, so even a 7200 dpi frame (11124 x 10512 px) never sits in memory whole. Everything is
deterministic: the same dpi and frame number always give the same pixels.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

REFERENCE_DPI = 600
LINES_AT_REFERENCE = 927
PIXELS_AT_REFERENCE = 876
MM_PER_INCH = 25.4
OPENING_ALONG_MM = 38.0  # the slide's long side, along the transport
OPENING_ACROSS_MM = 25.5
STRIP_LINES = 256
SPECKS_PER_FRAME = 6
FULL = 65535.0
MOUNT_LEVEL = 0.01
FILM_IR_LEVEL = 0.9
SPECK_RGB_GAIN = 0.1
SPECK_IR_GAIN = 0.35


@dataclass(frozen=True)
class FrameGeometry:
    lines: int
    pixels: int
    top: int  # first line of the opening
    left: int  # first pixel of the opening
    along: int  # opening size in lines
    across: int  # opening size in pixels

    @classmethod
    def at(cls, dpi: int) -> "FrameGeometry":
        lines = round(LINES_AT_REFERENCE * dpi / REFERENCE_DPI)
        pixels = round(PIXELS_AT_REFERENCE * dpi / REFERENCE_DPI)
        along = round(OPENING_ALONG_MM / MM_PER_INCH * dpi)
        across = round(OPENING_ACROSS_MM / MM_PER_INCH * dpi)
        return cls(lines, pixels, (lines - along) // 2, (pixels - across) // 2, along, across)


def _specks(geometry: FrameGeometry, dpi: int, frame: int) -> list[tuple[int, int, int]]:
    rng = np.random.default_rng((dpi, frame, 1))
    scale = dpi / REFERENCE_DPI
    out = []
    for _ in range(SPECKS_PER_FRAME):
        radius = max(1, round(int(rng.integers(2, 5)) * scale))
        margin = radius + 2
        y = int(rng.integers(geometry.top + margin, geometry.top + geometry.along - margin))
        x = int(rng.integers(geometry.left + margin, geometry.left + geometry.across - margin))
        out.append((y, x, radius))
    return out


def _darken(strip: np.ndarray, y0: int, specks: list, gain: float) -> None:
    for y, x, r in specks:
        a, b = max(y - r, y0), min(y + r + 1, y0 + strip.shape[0])
        if a < b:
            strip[a - y0 : b - y0, x - r : x + r + 1] *= gain


def _rgb_strip(g: FrameGeometry, dpi: int, frame: int, y0: int, y1: int) -> np.ndarray:
    rng = np.random.default_rng((dpi, frame, 2, y0))
    strip = np.full((y1 - y0, g.pixels, 3), MOUNT_LEVEL * FULL, np.float32)
    a, b = max(y0, g.top), min(y1, g.top + g.along)
    if a < b:
        yy = ((np.arange(a, b, dtype=np.float32) - g.top) / g.along)[:, None]
        xx = (np.arange(g.across, dtype=np.float32) / g.across)[None, :]
        phases = np.random.default_rng((dpi, frame, 0)).uniform(0.5, 1.5, (3, 3))
        for c, (fx, fy, p) in enumerate(phases):
            wave = 0.5 + 0.35 * np.sin(2 * np.pi * (fx * xx + fy * yy) + 4 * p)
            strip[a - y0 : b - y0, g.left : g.left + g.across, c] = wave * FULL
    strip += rng.normal(0, 0.004 * FULL, strip.shape).astype(np.float32)
    _darken(strip, y0, _specks(g, dpi, frame), SPECK_RGB_GAIN)
    return np.clip(strip, 0, FULL).astype(np.uint16)


def _ir_strip(g: FrameGeometry, dpi: int, frame: int, y0: int, y1: int) -> np.ndarray:
    rng = np.random.default_rng((dpi, frame, 3, y0))
    strip = np.full((y1 - y0, g.pixels), MOUNT_LEVEL * FULL, np.float32)
    a, b = max(y0, g.top), min(y1, g.top + g.along)
    if a < b:
        strip[a - y0 : b - y0, g.left : g.left + g.across] = FILM_IR_LEVEL * FULL
    strip += rng.normal(0, 0.005 * FULL, strip.shape).astype(np.float32)
    _darken(strip, y0, _specks(g, dpi, frame), SPECK_IR_GAIN)
    plane = np.clip(strip, 0, FULL).astype(np.uint16)
    return np.repeat(plane[:, :, None], 3, axis=2)  # the driver's three equal IR channels


StripMaker = Callable[[FrameGeometry, int, int, int, int], np.ndarray]


def _strips(dpi: int, frame: int, make: StripMaker) -> Iterator[np.ndarray]:
    geometry = FrameGeometry.at(dpi)
    for y0 in range(0, geometry.lines, STRIP_LINES):
        yield make(geometry, dpi, frame, y0, min(y0 + STRIP_LINES, geometry.lines))


def write_frame(path: Path, dpi: int, frame: int, infrared: bool) -> None:
    """Write one visible (or, with ``infrared``, IR) frame TIFF, one strip in memory at a time."""
    geometry = FrameGeometry.at(dpi)
    tifffile.imwrite(
        path,
        _strips(dpi, frame, _ir_strip if infrared else _rgb_strip),
        shape=(geometry.lines, geometry.pixels, 3),
        dtype=np.uint16,
        rowsperstrip=STRIP_LINES,
        photometric="rgb",
    )
