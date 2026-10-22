"""Save and load whole-holder sweeps as memory-mappable ``.npy`` files plus ``meta.json``."""

import json
from pathlib import Path

import numpy as np

from opticfilm_linux.model import HolderKind, RawSweep

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
    (directory / META_FILE).write_text(json.dumps(meta))


def _load_array(directory: Path, name: str) -> np.ndarray:
    try:
        return np.load(directory / name, mmap_mode="r")
    except (OSError, ValueError, EOFError) as exc:
        raise SweepFormatError(f"cannot read {name} in {directory}: {exc}") from exc


def _load_meta(directory: Path) -> tuple[int, HolderKind]:
    try:
        meta = json.loads((directory / META_FILE).read_text())
        return int(meta["dpi"]), HolderKind(meta["holder"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SweepFormatError(f"cannot read {META_FILE} in {directory}: {exc}") from exc


def load_sweep(directory: Path) -> RawSweep:
    if not directory.is_dir():
        raise SweepFormatError(f"sweep directory not found: {directory}")
    dpi, holder = _load_meta(directory)
    rgb = _load_array(directory, RGB_FILE)
    ir = _load_array(directory, IR_FILE) if (directory / IR_FILE).exists() else None
    try:
        return RawSweep(rgb=rgb, ir=ir, dpi=dpi, holder=holder)
    except ValueError as exc:
        raise SweepFormatError(f"invalid sweep in {directory}: {exc}") from exc
