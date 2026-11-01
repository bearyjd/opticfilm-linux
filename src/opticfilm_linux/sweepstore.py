"""Save and load whole-holder sweeps as memory-mappable ``.npy`` files plus ``meta.json``."""

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from opticfilm_linux.model import HolderKind, RawSweep
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
    (directory / META_FILE).write_text(json.dumps(meta))


def _load_array(directory: Path, name: str) -> np.ndarray:
    try:
        return np.load(directory / name, mmap_mode="r")
    except (OSError, ValueError, EOFError) as exc:
        raise SweepFormatError(f"cannot read {name} in {directory}: {exc}") from exc


def _load_meta(directory: Path) -> tuple[int, HolderKind, ScanEvidence | None]:
    try:
        meta = json.loads((directory / META_FILE).read_text())
        data = meta.get("evidence")
        evidence = None
        if data is not None:
            evidence = ScanEvidence(**{**data, "diagnostics": tuple(data["diagnostics"])})
        return int(meta["dpi"]), HolderKind(meta["holder"]), evidence
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
    dpi, holder, evidence = _load_meta(directory)
    rgb = _load_array(directory, RGB_FILE)
    ir = _load_array(directory, IR_FILE) if (directory / IR_FILE).exists() else None
    try:
        return RawSweep(rgb=rgb, ir=ir, dpi=dpi, holder=holder, evidence=evidence)
    except ValueError as exc:
        raise SweepFormatError(f"invalid sweep in {directory}: {exc}") from exc
