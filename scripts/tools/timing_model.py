#!/usr/bin/env python3
"""Estimate the wall-clock hours to scan N slides from measured ``*.diag.json`` sidecars.

Usage: timing_model.py [options] SIDECAR [SIDECAR ...]

The baseline is the per-opening phase times of the ``--holder slide`` CLI path at 3600 dpi,
one calibration per opening.  The position and park phases grow with the opening's distance
from the home position, so they are fitted linearly against the opening index (frame - 1).

The app's ``--slides`` path was MEASURED on 2026-11-07 (driver-latest ``scan --slides 1-3
--dpi 3600 --ir --no-clean --dark-fallback default``): calibration ~5.1-5.2 s on EVERY opening
(no once-per-tray saving for dual/IR), scan pass 43.8-43.9 s, wall 218 s for 3 slides, of which
~33 s lies outside the driver's own total (start-up, writing the overscan TIFFs).  So
``--cal-reuse`` is a PROJECTION: what a calibration reuse would save (~5 s per opening); the
app currently recalibrates every opening.  Sidecar totals exclude that ~33 s per run.
``--post-overlap`` only hides the small unaccounted remainder (total_seconds minus the sum of
phase_seconds, plus ``--post-seconds``); real post-processing time is not in the sidecars.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Measured: --overscan-mm 1.5 -> 11696 lines, 0.5 -> 11120 lines at 3600 dpi.  The slope is
# 576 wire lines per +1 mm of --overscan-mm (per side), not image lines per mm of film, and it is
# quantised by the driver's chunk size, so small overscan changes are approximate.
LINES_PER_OVERSCAN_MM_AT_3600 = 576.0
BASE_DPI = 3600
DEFAULT_SLIDES = 3000
DEFAULT_TRAY_SIZE = 4
DEFAULT_SWAP_SECONDS = 30.0


@dataclass(frozen=True)
class PhaseModel:
    """Per-opening phase times (seconds) at the baseline dpi and overscan."""

    scan: float
    position_intercept: float
    position_slope: float
    park_intercept: float
    park_slope: float
    calibration: float
    overhead: float
    lines: float
    overscan_mm: float
    dpi: int
    n_sidecars: int


@dataclass(frozen=True)
class Estimate:
    n_slides: int
    n_trays: int
    opening_seconds: tuple[float, ...]  # first tray, per opening
    tray_seconds: float
    total_seconds: float

    @property
    def hours(self) -> float:
        return self.total_seconds / 3600.0


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _line_fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    """Least-squares (intercept, slope); slope 0 when there is a single distinct x."""
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0.0:
        return my, 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    return my - slope * mx, slope


def fit_model(diags: Iterable[Mapping[str, Any]], baseline_dpi: int = BASE_DPI) -> PhaseModel:
    """Fit a PhaseModel from parsed sidecar dicts taken at ``baseline_dpi``."""
    rows = [d for d in diags if d.get("dpi") == baseline_dpi and "phase_seconds" in d]
    if not rows:
        raise ValueError(f"no sidecars at {baseline_dpi} dpi with phase_seconds")
    ks: list[float] = []
    pos: list[float] = []
    park: list[float] = []
    scan: list[float] = []
    cal: list[float] = []
    over: list[float] = []
    for d in rows:
        ph = d["phase_seconds"]
        ks.append(float(d.get("frame", 1)) - 1.0)
        pos.append(float(ph.get("position", 0.0)))
        park.append(float(ph.get("park", 0.0)))
        scan.append(float(ph["scan"]))
        cal.append(sum(float(v) for k, v in ph.items() if k.startswith("cal_")))
        over.append(
            float(d.get("total_seconds", sum(ph.values()))) - sum(float(v) for v in ph.values())
        )
    p_int, p_slope = _line_fit(ks, pos)
    k_int, k_slope = _line_fit(ks, park)
    return PhaseModel(
        scan=_mean(scan),
        position_intercept=p_int,
        position_slope=p_slope,
        park_intercept=k_int,
        park_slope=k_slope,
        calibration=_mean(cal),
        overhead=max(0.0, _mean(over)),
        lines=_mean([float(d.get("lines", 0.0)) for d in rows]),
        overscan_mm=_mean([float(d.get("overscan_mm", 0.0)) for d in rows]),
        dpi=baseline_dpi,
        n_sidecars=len(rows),
    )


def scan_seconds(model: PhaseModel, dpi: int, overscan_mm: float | None) -> float:
    """Scan pass: proportional to window length, x (dpi/baseline)^2 (7200 -> x4)."""
    seconds = model.scan
    if overscan_mm is not None and model.lines > 0:
        extra = (
            (overscan_mm - model.overscan_mm)
            * LINES_PER_OVERSCAN_MM_AT_3600
            * (model.dpi / BASE_DPI)
        )
        seconds *= max(0.0, (model.lines + extra) / model.lines)
    return seconds * (dpi / model.dpi) ** 2


def estimate(
    model: PhaseModel,
    *,
    n_slides: int = DEFAULT_SLIDES,
    tray_size: int = DEFAULT_TRAY_SIZE,
    dpi: int | None = None,
    overscan_mm: float | None = None,
    cal_reuse: bool = False,
    post_overlap: bool = False,
    post_seconds: float = 0.0,
    swap_seconds: float = DEFAULT_SWAP_SECONDS,
) -> Estimate:
    """Pure core: total time for ``n_slides`` in trays of ``tray_size`` openings."""
    if n_slides < 0 or tray_size < 1:
        raise ValueError("n_slides must be >= 0 and tray_size >= 1")
    scan = scan_seconds(model, dpi or model.dpi, overscan_mm)
    post = 0.0 if post_overlap else model.overhead + post_seconds

    def opening(k: int) -> float:
        position = model.position_intercept + model.position_slope * k
        park = model.park_intercept + model.park_slope * k
        cal = 0.0 if (cal_reuse and k > 0) else model.calibration
        return scan + max(0.0, position) + max(0.0, park) + cal + post

    full_trays, rest = divmod(n_slides, tray_size)
    n_trays = full_trays + (1 if rest else 0)
    per_open = tuple(opening(k) for k in range(tray_size))
    tray_seconds = sum(per_open) + swap_seconds
    total = full_trays * tray_seconds
    if rest:
        total += sum(per_open[:rest]) + swap_seconds
    return Estimate(n_slides, n_trays, per_open, tray_seconds, total)


def load_sidecars(paths: Iterable[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in paths:
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"skip {p.name}: {exc}", file=sys.stderr)
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("sidecars", nargs="+", type=Path, help="*.diag.json files (3600 dpi baseline)")
    ap.add_argument("--slides", type=int, default=DEFAULT_SLIDES, help="number of slides")
    ap.add_argument("--tray-size", type=int, default=DEFAULT_TRAY_SIZE, help="openings per tray")
    ap.add_argument("--dpi", type=int, help="target dpi (scan pass scales by (dpi/3600)^2)")
    ap.add_argument("--overscan-mm", type=float, help="target --overscan (scan time follows)")
    ap.add_argument("--cal-reuse", action="store_true", help="no calibration after opening 1")
    ap.add_argument("--post-overlap", action="store_true", help="hide post-processing time")
    ap.add_argument("--post-seconds", type=float, default=0.0, help="extra post time per opening")
    ap.add_argument("--swap-seconds", type=float, default=DEFAULT_SWAP_SECONDS)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        model = fit_model(load_sidecars(args.sidecars))
        est = estimate(
            model,
            n_slides=args.slides,
            tray_size=args.tray_size,
            dpi=args.dpi,
            overscan_mm=args.overscan_mm,
            cal_reuse=args.cal_reuse,
            post_overlap=args.post_overlap,
            post_seconds=args.post_seconds,
            swap_seconds=args.swap_seconds,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"model sidecars={model.n_sidecars} scan={model.scan:.1f}s "
        f"position={model.position_intercept:.2f}+{model.position_slope:.2f}k "
        f"park={model.park_intercept:.2f}+{model.park_slope:.2f}k "
        f"cal={model.calibration:.2f}s overhead={model.overhead:.2f}s"
    )
    print("opening_seconds " + " ".join(f"{s:.1f}" for s in est.opening_seconds))
    print(f"tray_seconds {est.tray_seconds:.1f} trays {est.n_trays}")
    print(f"slides {est.n_slides} hours {est.hours:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
