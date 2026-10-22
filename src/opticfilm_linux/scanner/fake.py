"""A scanner that replays a stored sweep; used for development and tests."""

import dataclasses
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from opticfilm_linux.model import RawSweep, ScanSettings
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.sweepstore import load_sweep


class FakeScanner:
    """Replays a stored sweep. ``statuses`` is a script for ``status()``; the last one repeats.

    ``before_scan`` runs at the start of every scan, so tests can block or fail a scan.
    """

    def __init__(
        self,
        sweep_dir: Path,
        statuses: Sequence[ScannerStatus] = (ScannerStatus.READY,),
        before_scan: Callable[[], None] | None = None,
    ) -> None:
        self._sweep_dir = sweep_dir
        self._statuses = tuple(statuses) or (ScannerStatus.READY,)
        self._next = 0
        self._lock = threading.Lock()
        self._before_scan = before_scan

    def status(self) -> ScannerStatus:
        with self._lock:
            status = self._statuses[min(self._next, len(self._statuses) - 1)]
            self._next += 1
            return status

    def scan_holder(self, settings: ScanSettings) -> RawSweep:
        if self._before_scan is not None:
            self._before_scan()
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
