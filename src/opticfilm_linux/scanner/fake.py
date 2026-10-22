"""A scanner that replays a stored sweep; used for development and tests."""

import dataclasses
from pathlib import Path

from opticfilm_linux.model import RawSweep, ScanSettings
from opticfilm_linux.scanner.base import ScanError
from opticfilm_linux.sweepstore import load_sweep


class FakeScanner:
    def __init__(self, sweep_dir: Path) -> None:
        self._sweep_dir = sweep_dir

    def scan_holder(self, settings: ScanSettings) -> RawSweep:
        sweep = load_sweep(self._sweep_dir)
        if settings.dpi != sweep.dpi:
            raise ScanError(
                f"fake sweep was recorded at {sweep.dpi} dpi but {settings.dpi} dpi was requested"
            )
        if not settings.infrared:
            return dataclasses.replace(sweep, ir=None)
        return sweep

    def eject(self) -> None:
        return None
