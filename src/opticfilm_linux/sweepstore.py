"""Save and load whole-holder sweeps as memory-mappable ``.npy`` files plus ``meta.json``."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import NamedTuple

import numpy as np

from opticfilm_linux.model import ALL_POSITIONS, HolderKind, RawSweep, check_positions
from opticfilm_linux.provenance import ScanEvidence

RGB_FILE = "rgb.npy"
IR_FILE = "ir.npy"
META_FILE = "meta.json"


class SweepFormatError(ValueError):
    """A stored sweep is missing, truncated or otherwise unreadable."""


def save_sweep(sweep: RawSweep, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    np.save(directory / RGB_FILE, np.asarray(sweep.rgb))
    if sweep.ir is not None:
        np.save(directory / IR_FILE, np.asarray(sweep.ir))
    meta = {"dpi": sweep.dpi, "holder": sweep.holder.value}
    if sweep.evidence is not None:
        meta["evidence"] = asdict(sweep.evidence)
    if sweep.positions != ALL_POSITIONS:  # a full tray keeps the meta of older builds
        meta["positions"] = list(sweep.positions)
    (directory / META_FILE).write_text(json.dumps(meta))


def _load_array(directory: Path, name: str) -> np.ndarray:
    try:
        return np.load(directory / name, mmap_mode="r")
    except (OSError, ValueError, EOFError) as exc:
        raise SweepFormatError(f"cannot read {name} in {directory}: {exc}") from exc


class _Meta(NamedTuple):
    dpi: int
    holder: HolderKind
    evidence: ScanEvidence | None
    positions: tuple[int, ...]


def _positions(meta: dict) -> tuple[int, ...]:
    """The scanned openings; a sweep saved before positions existed holds all four."""
    stored = meta.get("positions", list(ALL_POSITIONS))
    if not isinstance(stored, list):
        raise ValueError(f"positions must be a list, got {stored!r}")
    positions = tuple(stored)
    check_positions(positions)
    return positions


def _load_meta(directory: Path) -> _Meta:
    try:
        meta = json.loads((directory / META_FILE).read_text())
        data = meta.get("evidence")
        evidence = None
        if data is not None:
            evidence = ScanEvidence(**{**data, "diagnostics": tuple(data["diagnostics"])})
        return _Meta(int(meta["dpi"]), HolderKind(meta["holder"]), evidence, _positions(meta))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SweepFormatError(f"cannot read {META_FILE} in {directory}: {exc}") from exc


def sweep_dpi(directory: Path) -> int:
    """The resolution of a stored sweep, read from its metadata only."""
    if not directory.is_dir():
        raise SweepFormatError(f"sweep directory not found: {directory}")
    return _load_meta(directory)[0]


def load_sweep(directory: Path) -> RawSweep:
    if not directory.is_dir():
        raise SweepFormatError(f"sweep directory not found: {directory}")
    meta = _load_meta(directory)
    rgb = _load_array(directory, RGB_FILE)
    ir = _load_array(directory, IR_FILE) if (directory / IR_FILE).exists() else None
    try:
        return RawSweep(
            rgb=rgb,
            ir=ir,
            dpi=meta.dpi,
            holder=meta.holder,
            evidence=meta.evidence,
            positions=meta.positions,
        )
    except ValueError as exc:
        raise SweepFormatError(f"invalid sweep in {directory}: {exc}") from exc
