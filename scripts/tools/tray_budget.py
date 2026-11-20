#!/usr/bin/env python3
"""Where the wall time of one tray scan went, from the driver's ``*.diag.json`` sidecars.

Usage: tray_budget.py SIDECAR [SIDECAR ...]      (the sidecars of ONE ``scan --slides`` run)

The driver times each opening's own phases (calibration, position, scan pass, park) and their
sum, ``total_seconds``.  It does not time what happens BETWEEN two openings: writing the frames
of the opening just scanned, and the preparation of the next one.  That time is recovered here
from the sidecars alone: the next opening started at ``finished_utc - total_seconds``, the
previous one ended at its ``finished_utc``.  ``timing_model.py`` leaves this time out.

``opening N costs`` is what leaving opening N out of the scan would save: its own total plus
the time between it and the opening before it.  Reads files only; never touches the scanner.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED = ("finished_utc", "total_seconds", "phase_seconds")
CLOCK_SLACK_S = 0.5  # finished_utc is the wall clock, total_seconds a monotonic one
PARTS = ("scan", "calibration", "position", "park", "other", "between")


@dataclass(frozen=True)
class OpeningCost:
    opening: int
    total: float  # the driver's own total for the opening
    between_before: float  # from the previous opening's end to this one's start

    @property
    def seconds(self) -> float:
        return self.total + self.between_before


@dataclass(frozen=True)
class TrayBudget:
    """Seconds of one tray scan, from the first opening's start to the last one's end."""

    openings: tuple[int, ...]
    scan: float
    calibration: float
    position: float
    park: float
    other: float  # inside the driver's totals but in none of its phases
    between: float  # between openings: frame writing and the next opening's preparation
    wall: float
    opening_costs: tuple[OpeningCost, ...]


@dataclass(frozen=True)
class _Timed:
    opening: int
    start: float
    end: float
    phases: Mapping[str, float]

    @property
    def total(self) -> float:
        return self.end - self.start


def _seconds(value: Any, name: str) -> float:
    """A finite, non-negative duration; the sidecar is external data."""
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0.0:
        raise ValueError(f"a sidecar's {name} is {value!r}; it must be finite and not negative")
    return seconds


def _timed(diag: Any) -> _Timed:
    if not isinstance(diag, Mapping):
        raise ValueError(f"a sidecar is {type(diag).__name__}, not a JSON object")
    for key in REQUIRED:
        if key not in diag:
            raise ValueError(f"a sidecar has no {key}; it cannot be placed in time")
    frame = diag.get("frame")
    if not isinstance(frame, int) or isinstance(frame, bool):
        raise ValueError(f"a sidecar's frame is {frame!r}; it must be an integer")
    raw_phases = diag["phase_seconds"]
    if not isinstance(raw_phases, Mapping) or not raw_phases:
        raise ValueError("a sidecar's phase_seconds must be a non-empty object")
    try:
        # started_utc is not used: it is the wall clock, while total_seconds is a monotonic
        # duration; finished_utc - total_seconds is the same clock on both sides of a gap.
        end = dt.datetime.fromisoformat(str(diag["finished_utc"])).timestamp()
        total = _seconds(diag["total_seconds"], "total_seconds")
        phases = {str(k): _seconds(v, f"phase {k}") for k, v in raw_phases.items()}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"a sidecar's timing cannot be read: {exc}") from exc
    return _Timed(frame, end - total, end, phases)


def _phase_sum(rows: Sequence[_Timed], wanted: str) -> float:
    if wanted == "calibration":
        return sum(v for row in rows for k, v in row.phases.items() if k.startswith("cal_"))
    return sum(row.phases.get(wanted, 0.0) for row in rows)


def tray_budget(diags: Iterable[Any]) -> TrayBudget:
    """The budget of one run; refuses sidecars that cannot belong to the same run."""
    rows = sorted((_timed(d) for d in diags), key=lambda row: row.end)
    if not rows:
        raise ValueError("no sidecars given")
    frames = [row.opening for row in rows]
    for frame in sorted(set(frames)):
        if frames.count(frame) > 1:
            raise ValueError(f"frame {frame} appears in more than one sidecar")
    gaps = [later.start - earlier.end for earlier, later in zip(rows, rows[1:], strict=False)]
    if any(gap < -CLOCK_SLACK_S for gap in gaps):
        raise ValueError("the sidecars overlap in time: they are not from one scan run")
    gaps = [max(0.0, gap) for gap in gaps]
    named = {part: _phase_sum(rows, part) for part in ("scan", "calibration", "position", "park")}
    in_scan = sum(row.total for row in rows)
    other = in_scan - sum(named.values())
    if other < -CLOCK_SLACK_S:
        print(
            f"warning: the phases add up to {-other:.1f} s more than the totals; "
            "they overlap, so 'other' is shown as 0",
            file=sys.stderr,
        )
    costs = tuple(
        OpeningCost(row.opening, row.total, before)
        for row, before in zip(rows, [0.0, *gaps], strict=True)
    )
    return TrayBudget(
        openings=tuple(row.opening for row in rows),
        other=max(0.0, other),
        between=sum(gaps),
        wall=in_scan + sum(gaps),
        opening_costs=costs,
        **named,
    )


def shares(budget: TrayBudget) -> dict[str, float]:
    """Each part as a fraction of the wall time."""
    if budget.wall <= 0.0:
        raise ValueError("the budget has no wall time")
    return {part: getattr(budget, part) / budget.wall for part in PARTS}


def load_sidecars(paths: Iterable[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in paths:
        try:
            out.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("sidecars", nargs="+", type=Path, help="*.diag.json files of one run")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        budget = tray_budget(load_sidecars(args.sidecars))
        fractions = shares(budget)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for part in PARTS:
        print(f"{part:12s} {getattr(budget, part):7.1f} s  {100 * fractions[part]:5.1f} %")
    print(f"{'wall':12s} {budget.wall:7.1f} s  (first start to last finish; app time excluded)")
    for cost in budget.opening_costs:
        print(
            f"opening {cost.opening} costs {cost.seconds:6.1f} s "
            f"(own {cost.total:.1f} + between {cost.between_before:.1f})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
