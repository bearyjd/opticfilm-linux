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
    ``load_error`` is raised by every ``load_next_strip`` (a missed load); ``load_calls`` counts
    them.
    """

    def __init__(
        self,
        sweep_dir: Path,
        statuses: Sequence[ScannerStatus] = (ScannerStatus.READY,),
        before_scan: Callable[[], None] | None = None,
        load_error: ScanError | None = None,
    ) -> None:
        self._sweep_dir = sweep_dir
        self._statuses = tuple(statuses) or (ScannerStatus.READY,)
        self._next = 0
        self._lock = threading.Lock()
        self._before_scan = before_scan
        self.load_error = load_error  # tests may set it later
        self.load_calls = 0
        self.scan_requests: list[ScanSettings] = []  # every scan_holder request, in order

    def status(self) -> ScannerStatus:
        with self._lock:
            status = self._statuses[min(self._next, len(self._statuses) - 1)]
            self._next += 1
            return status

    def scan_holder(self, settings: ScanSettings) -> RawSweep:
        self.scan_requests.append(settings)
        if self._before_scan is not None:
            self._before_scan()
        sweep = load_sweep(self._sweep_dir)
        if settings.dpi != sweep.dpi:
            raise ScanError(
                f"fake sweep was recorded at {sweep.dpi} dpi but {settings.dpi} dpi was requested"
            )
        if settings.positions != sweep.positions:  # it replays a sweep; it cannot rescan one
            raise ScanError(
                f"fake sweep holds positions {sweep.positions} but {settings.positions} "
                "were requested"
            )
        if not settings.infrared:
            return dataclasses.replace(sweep, ir=None)
        return sweep

    def eject(self) -> None:
        return None

    def load_next_strip(self) -> None:
        with self._lock:
            self.load_calls += 1
        if self.load_error is not None:
            raise self.load_error
