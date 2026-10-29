"""Scanner status without ever blocking the app.

The status is asked for on a short-lived daemon thread and cached. A backend that hangs is
reported as an error once it has not answered for ``timeout_s``; everything else (the window
state, the runner's readiness check, shutdown) reads the cache and so cannot hang with it.
"""

import threading
from collections.abc import Callable

from opticfilm_linux.model import RawSweep, ScanSettings
from opticfilm_linux.scanner.base import Scanner, ScannerStatus


class StatusPoller:
    def __init__(
        self,
        scanner: Scanner,
        clock: Callable[[], float],
        timeout_s: float,
        on_problem: Callable[[str, str], None],
    ) -> None:
        self._scanner = scanner
        self._clock = clock
        self._timeout_s = timeout_s
        self._on_problem = on_problem
        self._lock = threading.Lock()
        self._status = ScannerStatus.UNKNOWN
        self._started: float | None = None  # set while a request is out
        self._reported_error = False
        self._closed = False
        self._button: bool | None = None  # last eject-button reading; None: no baseline yet
        self._press = False  # a new press seen and not yet taken

    def close(self) -> None:
        """The batch is gone: a request still out must not report to whatever is open next."""
        with self._lock:
            self._closed = True

    def poll(self) -> None:
        """Ask the scanner in the background, unless an earlier request is still out."""
        with self._lock:
            if self._closed or self._started is not None:
                return  # closed: the batch is gone, its scanner must not be asked again
            self._started = self._clock()
        threading.Thread(target=self._ask, name="app-scanner-status", daemon=True).start()

    def take_eject_press(self) -> bool:
        """True once per new press of the Eject button. The first reading after a start, and the
        first after any poll that read nothing, only set the baseline: it may be a stale press."""
        with self._lock:
            press, self._press = self._press, False
            return press

    def current(self) -> ScannerStatus:
        with self._lock:
            started, status = self._started, self._status
        if started is None or self._clock() - started <= self._timeout_s:
            return status
        self._report("Scanner not responding", "the scanner did not answer a status request")
        return ScannerStatus.ERROR

    def _ask(self) -> None:
        pressed: bool | None = None
        try:
            status, problem = self._scanner.status(), None
            reader = getattr(self._scanner, "eject_pressed", None)
            pressed = reader() if callable(reader) else None
        except Exception as exc:  # the backend may raise anything; show it, keep polling
            status, problem = ScannerStatus.ERROR, f"{type(exc).__name__}: {exc}"
        with self._lock:
            self._status, self._started = status, None
            if pressed is not None and self._button is not None and pressed and not self._button:
                self._press = True
            self._button = pressed
            if status is not ScannerStatus.ERROR:
                self._reported_error = False
        if problem is not None:
            self._report("Scanner error", problem)

    def _report(self, message: str, detail: str) -> None:
        # The callback runs under the lock, so ``close()`` cannot return while one is in progress
        # and nothing reaches the callback afterwards.
        with self._lock:
            if self._closed or self._reported_error:
                return
            self._reported_error = True
            self._on_problem(message, detail)


class CachedStatusScanner:
    """The scanner as the runner sees it: ``status()`` is the cached value, never a USB call."""

    def __init__(self, scanner: Scanner, poller: StatusPoller) -> None:
        self._scanner = scanner
        self._poller = poller

    def status(self) -> ScannerStatus:
        return self._poller.current()

    def scan_holder(self, settings: ScanSettings) -> RawSweep:
        return self._scanner.scan_holder(settings)

    def eject(self) -> None:
        self._scanner.eject()
