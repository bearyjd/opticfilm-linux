"""Deterministic synthetic four-slide holder sweeps for tests.

Geometry follows the community driver's measurements on one unit (apertures of 22.75 mm along
the sweep at a 62.6 mm pitch) and is a test fixture only, to be replaced by real scans.
Film is transparent in infrared, the mount is opaque, a clear window is bright everywhere.
"""

import cv2
import numpy as np

from opticfilm_linux.model import Box, HolderKind, RawSweep

MM_PER_INCH = 25.4
SCAN_WIDTH_MM = 35.0
SCAN_LENGTH_MM = 226.0
APERTURE_LENGTH_MM = 22.75
PITCH_MM = 62.6
FIRST_OFFSET_MM = 8.0
FULL = 65535.0
ALL_FILLED = (True, True, True, True)


def _px(mm: float, dpi: int) -> int:
    return round(mm / MM_PER_INCH * dpi)


def truth_boxes(dpi: int, count: int = 4) -> tuple[Box, ...]:
    width, height = _px(SCAN_WIDTH_MM, dpi), _px(APERTURE_LENGTH_MM, dpi)
    return tuple(
        Box(0, _px(FIRST_OFFSET_MM + k * PITCH_MM, dpi), width, height) for k in range(count)
    )


def _gradient(h: int, w: int, rng: np.random.Generator, tilt_deg: float) -> np.ndarray:
    yy, xx = np.meshgrid(np.linspace(0, 1, h), np.linspace(0, 1, w), indexing="ij")
    freq, phase = rng.uniform(0.5, 1.5, (3, 2)), rng.uniform(0, 2 * np.pi, 3)
    channels = [
        0.5 + 0.35 * np.sin(2 * np.pi * (f[0] * xx + f[1] * yy) + p)
        for f, p in zip(freq, phase, strict=True)
    ]
    image = np.stack(channels, axis=2).astype(np.float32) * FULL
    if tilt_deg:
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), tilt_deg, 1.0)
        image = cv2.warpAffine(image, matrix, (w, h), borderMode=cv2.BORDER_REFLECT)
    return image


def _specks(dpi: int, filled: tuple[bool, ...], dust: int, seed: int) -> list[tuple[int, int, int]]:
    rng = np.random.default_rng(seed + 10_000)
    boxes = [b for b, f in zip(truth_boxes(dpi, len(filled)), filled, strict=True) if f]
    scale = dpi / 600
    out = []
    for _ in range(dust if boxes else 0):
        box = boxes[int(rng.integers(len(boxes)))]
        radius = max(1, round(int(rng.integers(2, 5)) * scale))
        margin = radius + 2
        y = int(rng.integers(box.y + margin, box.y + box.h - margin))
        x = int(rng.integers(margin, box.w - margin))
        out.append((y, x, radius))
    return out


def dust_centers(
    dpi: int, dust: int, seed: int = 0, filled: tuple[bool, ...] = ALL_FILLED
) -> list[tuple[int, int]]:
    return [(y, x) for y, x, _ in _specks(dpi, filled, dust, seed)]


def make_sweep(
    dpi: int = 600,
    filled: tuple[bool, ...] = ALL_FILLED,
    dust: int = 0,
    ir: bool = True,
    tilt_deg: float = 0.0,
    seed: int = 0,
) -> RawSweep:
    h, w = _px(SCAN_LENGTH_MM, dpi), _px(SCAN_WIDTH_MM, dpi)
    rng = np.random.default_rng(seed)
    rgb = np.full((h, w, 3), 0.01 * FULL, np.float32)
    ir_plane = np.full((h, w), 0.01 * FULL, np.float32)
    for box, is_filled in zip(truth_boxes(dpi, len(filled)), filled, strict=True):
        rows = slice(box.y, box.y + box.h)
        if is_filled:
            rgb[rows] = _gradient(box.h, w, rng, tilt_deg)
            ir_plane[rows] = 0.9 * FULL
        else:
            rgb[rows] = 0.97 * FULL
            ir_plane[rows] = 0.97 * FULL
    rgb += rng.normal(0, 0.004 * FULL, rgb.shape).astype(np.float32)
    ir_plane += rng.normal(0, 0.005 * FULL, ir_plane.shape).astype(np.float32)
    for y, x, r in _specks(dpi, filled, dust, seed):
        rgb[y - r : y + r + 1, x - r : x + r + 1] *= 0.1
        ir_plane[y - r : y + r + 1, x - r : x + r + 1] *= 0.35
    rgb16 = np.clip(rgb, 0, FULL).astype(np.uint16)
    ir16 = np.clip(ir_plane, 0, FULL).astype(np.uint16) if ir else None
    return RawSweep(rgb=rgb16, ir=ir16, dpi=dpi, holder=HolderKind.SLIDE4)
