#!/usr/bin/env python3
"""Register and average repeat scans of the same slide (16-bit RGB TIFFs).

Usage: scan_average.py OUT.tiff IN1.tiff IN2.tiff [IN3.tiff ...]

The first input is the reference.  Each other input is registered to it by sub-pixel
translation (tiled phase correlation at full resolution, seeded by a coarse pass on a
downsampled luminance), the residual and any scale/rotation are reported, and the file is
refused when the residual is large or no reliable match is found.  Images are Fourier-shifted,
averaged in float32, cropped to the common valid area and written as 16-bit TIFF with the
reference's resolution and descriptive tags (including an ICC profile when present).

Prints numbers only.  With 3 or more inputs it also prints the repeat noise on flat tiles:
per-frame sigma (from pair differences), the measured sigma of an n-frame average against a
held-out frame (n = 1 .. N-1) and the independent-noise prediction (n = 1 .. N).
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import tifffile

Array = npt.NDArray[np.float32]

TILE = 512
GRID = 5
COARSE_FACTOR = 4
MIN_TILES = 9
MIN_PEAK = 12.0  # z-score of the correlation peak (random mismatch gives ~5)
BLOCK = 128
BLOCK_MARGIN = 16  # real pixels read around each block
PAD = 16  # reflect padding before the FFT
EDGE_MARGIN = 24  # dropped around the common area (>= BLOCK_MARGIN + rounding)
MAX_SCALE_DEV = 0.01
MAX_ROT_DEG = 0.5
FLAT_TILE = 64
FLAT_TILES = 32
# Tags copied from the reference besides the structural ones tifffile writes itself.
COPY_TAGS = (270, 271, 272, 305, 306, 34675)  # description, make, model, software, date, ICC


class RegistrationError(Exception):
    """The input cannot be registered to the reference."""


@dataclass(frozen=True)
class Registration:
    """Affine map from reference to image coordinates: q = r + shift(r).

    shift(r) = (dy, dx) + G (r - centre); (dy, dx) is the shift at the reference centre.
    """

    dy: float
    dx: float
    gyy: float
    gyx: float
    gxy: float
    gxx: float
    centre: tuple[float, float]
    residual: float  # RMS of the tile shifts about the affine fit (px)
    translation_residual: float  # RMS about a pure translation (px)
    tiles: int
    peak: float

    @property
    def scale_y(self) -> float:
        return 1.0 + self.gyy

    @property
    def scale_x(self) -> float:
        return 1.0 + self.gxx

    @property
    def rotation_deg(self) -> float:
        return float(np.degrees((self.gyx - self.gxy) / 2.0))

    def shift_at(self, y: float, x: float) -> tuple[float, float]:
        ry, rx = y - self.centre[0], x - self.centre[1]
        return (
            self.dy + self.gyy * ry + self.gyx * rx,
            self.dx + self.gxy * ry + self.gxx * rx,
        )


IDENTITY = Registration(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (0.0, 0.0), 0.0, 0.0, 0, 0.0)


def luminance(img: npt.NDArray[np.uint16]) -> Array:
    return img.astype(np.float32).mean(axis=2)


def _block_mean(a: Array, f: int) -> Array:
    h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    return a[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3)).astype(np.float32)


def phase_correlate(ref: Array, img: Array, step: float = 0.02) -> tuple[float, float, float]:
    """Return (dy, dx, peak) with img(q) ~ ref(q - (dy, dx)); equal-shaped inputs."""
    h, w = ref.shape
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    fa = np.fft.fft2((ref - ref.mean()) * win)
    fb = np.fft.fft2((img - img.mean()) * win)
    cross = fb * np.conj(fa)
    cross /= (np.abs(cross) + 1e-9 * (np.abs(cross).max() + 1e-30)) ** 0.5  # partial whitening
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    cross *= (np.hypot(fy, fx) <= 0.2).astype(np.float32)
    corr = np.fft.ifft2(cross).real
    py, px = np.unravel_index(int(np.argmax(corr)), corr.shape)
    peak = float((corr[py, px] - corr.mean()) / (corr.std() + 1e-30))
    cy = float(py if py <= h // 2 else py - h)
    cx = float(px if px <= w // 2 else px - w)
    offs = np.arange(-1.5, 1.5 + step / 2, step)
    ey = np.exp(2j * np.pi * (cy + offs)[:, None] * np.fft.fftfreq(h)[None, :])
    ex = np.exp(2j * np.pi * np.fft.fftfreq(w)[:, None] * (cx + offs)[None, :])
    fine = (ey @ cross @ ex).real
    iy, ix = np.unravel_index(int(np.argmax(fine)), fine.shape)
    return cy + float(offs[iy]), cx + float(offs[ix]), peak


def _tile_origins(extent: int, n: int, size: int, lo: int, hi: int) -> list[int]:
    span = hi - lo - size
    if span < 0:
        return []
    return [lo + int(round(span * i / max(1, n - 1))) for i in range(n)]


def register(ref_lum: Array, img_lum: Array, max_residual: float = 0.5) -> Registration:
    """Estimate the translation of ``img_lum`` relative to ``ref_lum`` (pixels)."""
    h = min(ref_lum.shape[0], img_lum.shape[0])
    w = min(ref_lum.shape[1], img_lum.shape[1])
    f = COARSE_FACTOR
    cdy, cdx, _ = phase_correlate(_block_mean(ref_lum[:h, :w], f), _block_mean(img_lum[:h, :w], f))
    cy, cx = int(round(cdy * f)), int(round(cdx * f))
    size = min(TILE, h // 2, w // 2)
    margin = abs(cy) + 1, abs(cx) + 1
    ys = _tile_origins(
        h, GRID, size, max(0, -cy) + margin[0], min(h, img_lum.shape[0] - cy) - margin[0]
    )
    xs = _tile_origins(
        w, GRID, size, max(0, -cx) + margin[1], min(w, img_lum.shape[1] - cx) - margin[1]
    )
    pts: list[tuple[float, float, float, float]] = []  # y, x (tile centre), dy, dx
    peaks: list[float] = []
    for y in ys:
        for x in xs:
            a = ref_lum[y : y + size, x : x + size]
            b = img_lum[y + cy : y + cy + size, x + cx : x + cx + size]
            if b.shape != a.shape:
                continue
            ty, tx, peak = phase_correlate(a, b)
            if peak >= MIN_PEAK:
                pts.append((y + size / 2, x + size / 2, cy + ty, cx + tx))
                peaks.append(peak)
    if len(pts) < MIN_TILES:
        raise RegistrationError(
            f"only {len(pts)} reliable tiles (need {MIN_TILES}); different image?"
        )
    arr = np.array(pts)
    centre = (ref_lum.shape[0] / 2.0, ref_lum.shape[1] / 2.0)
    ry, rx = arr[:, 0] - centre[0], arr[:, 1] - centre[1]
    design = np.column_stack([ry, rx, np.ones(len(arr))])
    coef_y, *_ = np.linalg.lstsq(design, arr[:, 2], rcond=None)
    coef_x, *_ = np.linalg.lstsq(design, arr[:, 3], rcond=None)
    fit_res = np.hypot(arr[:, 2] - design @ coef_y, arr[:, 3] - design @ coef_x)
    trans_res = np.hypot(arr[:, 2] - np.median(arr[:, 2]), arr[:, 3] - np.median(arr[:, 3]))
    reg = Registration(
        float(coef_y[2]), float(coef_x[2]), float(coef_y[0]), float(coef_y[1]),
        float(coef_x[0]), float(coef_x[1]), centre,
        float(np.sqrt(np.mean(fit_res**2))), float(np.sqrt(np.mean(trans_res**2))),
        len(pts), float(np.mean(peaks)),
    )  # fmt: skip
    if reg.residual > max_residual:
        raise RegistrationError(f"registration residual {reg.residual:.3f} px > {max_residual} px")
    if max(abs(reg.gyy), abs(reg.gxx)) > MAX_SCALE_DEV or abs(reg.rotation_deg) > MAX_ROT_DEG:
        raise RegistrationError(
            f"scale {reg.scale_y:.5f}/{reg.scale_x:.5f} rot {reg.rotation_deg:.3f} deg is not small"
        )
    return reg


def common_area(
    shapes: Sequence[tuple[int, int]], regs: Sequence[Registration], ref_shape: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Reference-frame (r0, r1, c0, c1) valid in every image, with the edge margin applied."""
    h, w = ref_shape
    corners = [(0, 0), (0, w), (h, 0), (h, w)]
    r0, c0, r1, c1 = EDGE_MARGIN, EDGE_MARGIN, h - EDGE_MARGIN, w - EDGE_MARGIN
    for (sh, sw), reg in zip(shapes, regs, strict=True):
        sy = [reg.shift_at(*c)[0] for c in corners]
        sx = [reg.shift_at(*c)[1] for c in corners]
        r0 = max(r0, int(np.ceil(-min(sy))) + EDGE_MARGIN)
        c0 = max(c0, int(np.ceil(-min(sx))) + EDGE_MARGIN)
        r1 = min(r1, int(np.floor(sh - max(sy))) - EDGE_MARGIN)
        c1 = min(c1, int(np.floor(sw - max(sx))) - EDGE_MARGIN)
    if r1 - r0 < FLAT_TILE or c1 - c0 < FLAT_TILE:
        raise RegistrationError("no common area left after registration")
    return r0, r1, c0, c1


def _shift_block(block: Array, fy: float, fx: float) -> Array:
    """Fourier-shift ``block`` so out(r) = block(r + f); reflect padding avoids wrap seams."""
    padded = np.pad(block, ((PAD, PAD), (PAD, PAD), (0, 0)), mode="reflect")
    spec = np.fft.rfft2(padded, axes=(0, 1))
    py = np.exp(2j * np.pi * np.fft.fftfreq(padded.shape[0])[:, None] * fy)
    px = np.exp(2j * np.pi * np.fft.rfftfreq(padded.shape[1])[None, :] * fx)
    spec *= (py * px)[:, :, None].astype(np.complex64)
    out = np.fft.irfft2(spec, s=padded.shape[:2], axes=(0, 1)).astype(np.float32)
    return out[PAD:-PAD, PAD:-PAD]


def warp_crop(
    img: npt.NDArray[np.uint16], reg: Registration, area: tuple[int, int, int, int]
) -> Array:
    """Resample ``img`` onto reference coordinates ``area`` in blocks (local Fourier shifts).

    The shift varies by under 0.05 px across a block for a scale error of 5e-4, so each block
    uses the shift at its centre.
    """
    r0, r1, c0, c1 = area
    out = np.empty((r1 - r0, c1 - c0, 3), np.float32)
    m = BLOCK_MARGIN
    for a in range(r0, r1, BLOCK):
        b = min(a + BLOCK, r1)
        for c in range(c0, c1, BLOCK):
            d = min(c + BLOCK, c1)
            sy, sx = reg.shift_at((a + b) / 2.0, (c + d) / 2.0)
            ny, nx = int(round(sy)), int(round(sx))
            fy, fx = sy - ny, sx - nx
            blk = img[a - m + ny : b + m + ny, c - m + nx : d + m + nx].astype(np.float32)
            if fy != 0.0 or fx != 0.0:
                blk = _shift_block(blk, fy, fx)
            out[a - r0 : b - r0, c - c0 : d - c0] = blk[m:-m, m:-m]
    return out


def pick_flat_tiles(lum: Array) -> list[tuple[int, int]]:
    """Origins of the flattest FLAT_TILE tiles (judged on 8x8 block means) that are not dark."""
    t = FLAT_TILE
    ny, nx = lum.shape[0] // t, lum.shape[1] // t
    small = _block_mean(lum[: ny * t, : nx * t], 8)
    tiles = small.reshape(ny, t // 8, nx, t // 8).transpose(0, 2, 1, 3).reshape(ny, nx, -1)
    spread = tiles.std(axis=2)
    mean = tiles.mean(axis=2)
    bright = mean > 0.1 * 65535.0
    if bright.sum() < 8:  # dark slide: settle for anything above black
        bright = mean > 0.02 * 65535.0
    spread = np.where(bright, spread, np.inf)
    order = np.argsort(spread, axis=None)[: max(1, FLAT_TILES)]
    return [(int(i // nx) * t, int(i % nx) * t) for i in order if np.isfinite(spread.flat[i])]


@dataclass(frozen=True)
class NoiseReport:
    per_frame: tuple[float, ...]  # sigma of each input (from the pair variances)
    measured: tuple[float, ...]  # sigma of the n-frame average, n = 1..N-1 (vs a held-out frame)
    predicted: tuple[float, ...]  # same from the per-frame sigmas assuming independence, n = 1..N


def noise_report(patches: Sequence[Array]) -> NoiseReport:
    """Repeat noise on flat tiles (per-tile, per-channel means removed).

    Pair variances v_ij = s_i^2 + s_j^2 give each frame's sigma (least squares).  The n-frame
    average is measured against a held-out frame h: var(mean(S) - x_h) = sigma_S^2 + s_h^2,
    averaged over every held-out frame and every n-subset S of the others.
    """
    cen = [p - p.mean(axis=(1, 2), keepdims=True) for p in patches]
    n = len(cen)
    rows, rhs = [], []
    for i, j in itertools.combinations(range(n), 2):
        row = np.zeros(n)
        row[[i, j]] = 1.0
        rows.append(row)
        rhs.append(float(np.mean((cen[i] - cen[j]) ** 2)))
    sq = np.clip(np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0], 0.0, None)
    measured = []
    for m in range(1, n):
        vals = []
        for h in range(n):
            for sub in itertools.combinations([i for i in range(n) if i != h], m):
                diff = sum(cen[i] for i in sub) / m - cen[h]
                vals.append(max(0.0, float(np.mean(diff**2)) - sq[h]))
        measured.append(float(np.sqrt(np.mean(vals))))
    predicted = [float(np.sqrt(sq.mean() / m)) for m in range(1, n + 1)]
    return NoiseReport(tuple(float(np.sqrt(v)) for v in sq), tuple(measured), tuple(predicted))


def read_tiff(path: Path) -> tuple[npt.NDArray[np.uint16], dict[str, object]]:
    with tifffile.TiffFile(path) as tf:
        page = tf.pages.first
        data = page.asarray()
        if data.dtype != np.uint16 or data.ndim != 3 or data.shape[2] != 3:
            raise ValueError(f"{path.name}: expected 16-bit RGB, got {data.dtype} {data.shape}")
        tags = page.tags
        meta: dict[str, object] = {}
        if "XResolution" in tags and "YResolution" in tags:
            xn, xd = tags["XResolution"].value
            yn, yd = tags["YResolution"].value
            meta["resolution"] = (xn / xd, yn / yd)
        if "ResolutionUnit" in tags:
            meta["resolutionunit"] = int(tags["ResolutionUnit"].value)
        extra = []
        for code in COPY_TAGS:
            tag = tags.get(code)
            if tag is not None:
                dtype = tag.dtype if isinstance(tag.dtype, int) else int(tag.dtype)
                extra.append((code, dtype, tag.count, tag.value, True))
        meta["extratags"] = extra
        return data, meta


def write_tiff(path: Path, data: npt.NDArray[np.uint16], meta: dict[str, object]) -> None:
    kwargs: dict[str, object] = {"photometric": "rgb", "metadata": None}
    kwargs.update(meta)
    tifffile.imwrite(path, data, **kwargs)  # type: ignore[arg-type]


def average(
    paths: Sequence[Path], max_residual: float = 0.5
) -> tuple[npt.NDArray[np.uint16], dict[str, object], list[str]]:
    """Register and average ``paths``; returns (image, reference meta, report lines)."""
    lines: list[str] = []
    t_start = time.perf_counter()
    ref, meta = read_tiff(paths[0])
    ref_lum = luminance(ref)
    regs: list[Registration] = [IDENTITY]
    shapes: list[tuple[int, int]] = [(ref.shape[0], ref.shape[1])]
    for p in paths[1:]:
        t0 = time.perf_counter()
        img, _ = read_tiff(p)
        try:
            reg = register(ref_lum, luminance(img), max_residual)
        except RegistrationError as exc:
            raise RegistrationError(f"{p.name}: {exc}") from exc
        regs.append(reg)
        shapes.append((img.shape[0], img.shape[1]))
        lines.append(
            f"register {p.name} dy={reg.dy:.3f} dx={reg.dx:.3f} residual={reg.residual:.3f}px "
            f"(translation-only {reg.translation_residual:.3f}px) "
            f"scale_y={reg.scale_y:.6f} scale_x={reg.scale_x:.6f} "
            f"rot={reg.rotation_deg:.4f}deg tiles={reg.tiles} "
            f"peak={reg.peak:.1f} t={time.perf_counter() - t0:.1f}s"
        )
    del ref_lum
    area = common_area(shapes, regs, shapes[0])
    lines.append(
        f"common_area rows={area[1] - area[0]} cols={area[3] - area[2]} "
        f"(reference {shapes[0][0]}x{shapes[0][1]})"
    )
    total: Array | None = None
    tiles: list[tuple[int, int]] = []
    patches: list[Array] = []
    for i, p in enumerate(paths):
        t0 = time.perf_counter()
        img = ref if i == 0 else read_tiff(p)[0]
        warped = warp_crop(img, regs[i], area)
        del img
        if total is None:
            total = warped.copy()
            tiles = pick_flat_tiles(luminance_f(warped))
        else:
            total += warped
        patches.append(
            np.stack([warped[y : y + FLAT_TILE, x : x + FLAT_TILE] for y, x in tiles])
            if tiles
            else np.zeros((0, FLAT_TILE, FLAT_TILE, 3), np.float32)
        )
        lines.append(f"warp {p.name} t={time.perf_counter() - t0:.1f}s")
    assert total is not None
    if len(paths) >= 3 and tiles:
        rep = noise_report(patches)
        lines.append(f"noise_flat_tiles={len(tiles)} unit=DN16 (RMS, all channels)")
        lines.append("noise_per_frame " + " ".join(f"{v:.1f}" for v in rep.per_frame))
        lines.append(
            "noise_measured_vs_n "
            + " ".join(f"n{k}={v:.1f}" for k, v in enumerate(rep.measured, 1))
        )
        lines.append(
            "noise_predicted_vs_n "
            + " ".join(f"n{k}={v:.1f}" for k, v in enumerate(rep.predicted, 1))
        )
    out = np.clip(np.rint(total / len(paths)), 0, 65535).astype(np.uint16)
    lines.append(f"total_runtime={time.perf_counter() - t_start:.1f}s inputs={len(paths)}")
    return out, meta, lines


def luminance_f(a: Array) -> Array:
    return a.mean(axis=2)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("output", type=Path)
    ap.add_argument(
        "inputs", nargs="+", type=Path, help="at least 2 repeat scans; first = reference"
    )
    ap.add_argument("--max-residual", type=float, default=0.5, help="refuse above this (px)")
    args = ap.parse_args(argv)
    if len(args.inputs) < 2:
        ap.error("need at least 2 inputs")
    try:
        out, meta, lines = average(args.inputs, args.max_residual)
    except (RegistrationError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    write_tiff(args.output, out, meta)
    print("\n".join(lines))
    print(f"wrote {args.output.name} shape={out.shape[0]}x{out.shape[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
