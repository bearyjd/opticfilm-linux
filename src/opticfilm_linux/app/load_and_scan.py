"""Load & scan: one click loads the next holder, waits for a fresh READY, then scans it.

The click is the owner's statement that the holder is seated and latched, so it is logged as
that confirmation. A load that ends LATCHED or UNKNOWN uses up the RELEASED state; one refused
before the driver moved anything (another program, a plain refusal) leaves it RELEASED, so a
second explicit click is accepted. It starts only from a known state (an eject
this app saw finish and a poll after it that says NOT_READY) and only from that explicit click:
auto-eject, a timer or the front-panel button never start it. One holder-pool job keeps the
holder reservation from the load until the hand-over to the scan, which happens in one critical
section. The load is sent at most once per click; the first anomaly (load error, no fresh READY
in time, an ERROR/INTERRUPTED/NOT_CONNECTED poll, a close request) ends it without a scan.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Literal

from opticfilm_linux.app.holder import (
    ANOTHER_PROGRAM,
    NEXT_STRIP_OFF,
    REFUSED,
    SCANNER_INTERRUPTED,
    WAIT_EXPORT,
    WAIT_HOLDER,
)
from opticfilm_linux.app.messages import holder_failed_text
from opticfilm_linux.app.state import Banner
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.scanner import holder_latch, interrupted
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.scanner.driver import ScannerInUse
from opticfilm_linux.scanner.holder_latch import HolderLatch
from opticfilm_linux.scanner.interrupted import ScannerInterrupted, ScannerRefused

log = logging.getLogger(__name__)

LoadScanPhase = Literal["idle", "loading", "waiting_ready", "scanning", "failed"]
LOAD_READY_TIMEOUT_S = 30.0  # a load that does not end in READY within this is an anomaly
READY_POLL_S = 0.1
STOP_STATUSES = (ScannerStatus.ERROR, ScannerStatus.INTERRUPTED, ScannerStatus.NOT_CONNECTED)

LOAD_SCAN_OFF = "Load & scan is switched off. Use Load next holder, then Scan."
NO_SCANNER = "Load & scan needs a batch open for scanning on a scanner that can move the holder."
NOT_RELEASED = (
    "Load & scan starts only after an eject this app saw finish. Check the holder by hand, "
    "then use Load next holder and Scan."
)
SCANNER_NOT_WAITING = (
    "Load & scan needs the scanner to be waiting for a holder. Check the slot, or use Scan."
)
READY_TIMEOUT = (
    "Load & scan stopped: the scanner did not get ready after the load, so nothing was scanned. "
    "Check the holder by hand, then use the manual buttons."
)
STOPPED_ANOMALY = (
    "Load & scan stopped: the scanner reported a problem after the load, so nothing was "
    "scanned. Check the holder by hand, then use the manual buttons."
)
STOPPED_CLOSING = (
    "Load & scan stopped by the close request: the holder was loaded, not scanned. "
    "Press Close again."
)
CONFIRMATION_LOG = "Load & scan: the owner confirmed the holder is seated and latched"


class LoadAndScanCommands:
    """Mixed into ``Controller``, whose lock, session, holder worker and latch it uses."""

    def _init_load_and_scan(
        self, enabled: bool, ready_timeout_s: float, clock: Callable[[], float]
    ) -> None:
        self._load_and_scan = enabled
        self._load_ready_timeout_s = ready_timeout_s
        self._load_clock = clock  # its own clock: the status poller's timeout must not move
        self._load_scan_phase: LoadScanPhase = "idle"
        self._load_scan_owned = False  # this job holds ``_holder_busy`` (until the hand-over)
        self._load_scan_cancel = threading.Event()  # a close request: stop, never scan
        self._release_mark: tuple[int, int] | None = None  # (latch applied, sequence) at RELEASED

    @property
    def load_and_scan_enabled(self) -> bool:
        return self._load_and_scan

    def set_load_and_scan(self, on: bool) -> None:
        """Applies to the next click; one already running carries on."""
        with self._lock:
            self._load_and_scan = on

    def load_and_scan(self) -> None:
        session = self._session
        poller = session.poller if session is not None else None
        status, sequence = poller.snapshot() if poller is not None else (ScannerStatus.UNKNOWN, 0)
        marker = interrupted.is_pending()  # storage: read outside the lock
        with self._lock:
            refusal = self._load_scan_refusal(session, status, sequence)
            if refusal is None and marker:
                refusal = SCANNER_INTERRUPTED
            if refusal is None:
                self._holder_busy = self._load_scan_owned = True
                self._load_scan_phase = "loading"
                self._load_scan_cancel.clear()
        if refusal is not None:
            self._set(_banner=Banner(refusal))
            return
        log.info(CONFIRMATION_LOG)
        future = self._run_writer(self._load_scan_job, "Could not load and scan", self._holder_pool)
        self._track(future, self._load_scan_ended)
        with self._lock:
            if future is not None and not future.done() and self._load_scan_owned:
                self._holder_future = future

    def _load_scan_refusal(self, session, status: ScannerStatus, sequence: int) -> str | None:
        """Why Load & scan cannot start now; called under the lock (no storage access)."""
        if not self._load_and_scan:
            return LOAD_SCAN_OFF
        if not self._next_strip:
            return NEXT_STRIP_OFF
        if self._holder_busy:
            return WAIT_HOLDER
        refusal = self._holder_refusal()
        if refusal is not None:
            return refusal
        if self._long_jobs or self._job_futures:
            return WAIT_EXPORT
        if session is None or None in (session.runner, session.holder, session.poller):
            return NO_SCANNER
        if status is ScannerStatus.INTERRUPTED:
            return SCANNER_INTERRUPTED
        self._track_release()
        if self._holder_latch is not HolderLatch.RELEASED:
            return NOT_RELEASED
        if status is not ScannerStatus.NOT_READY or sequence <= self._release_mark[1]:
            return SCANNER_NOT_WAITING  # only a poll asked after the release counts
        return None

    def _track_release(self) -> None:
        """Under the lock: remember the latest sequence handed out when RELEASED was first seen."""
        if self._holder_latch is not HolderLatch.RELEASED:
            self._release_mark = None
        elif self._release_mark is None or self._release_mark[0] != self._latch_applied:
            self._release_mark = (self._latch_applied, self._latch_sequence)

    def _load_scan_fields(self, session, status: ScannerStatus, sequence: int) -> dict:
        """Under the lock, on every refresh: the two ``AppState`` fields."""
        runner = session.runner if session is not None else None
        scanning = self._scans_queued or (runner is not None and runner.scan_pending())
        if self._load_scan_phase == "scanning" and not scanning:
            self._load_scan_phase = "idle"
        return {
            "can_load_and_scan": self._load_scan_refusal(session, status, sequence) is None,
            "load_scan_phase": self._load_scan_phase,
        }

    def _reset_load_scan(self) -> None:
        """Under the lock, when a batch is attached or detached."""
        self._load_scan_phase = "idle"
        self._release_mark = None

    # -- the job on the holder worker ---------------------------------------------------------

    def _load_scan_job(self, _runner: BatchRunner) -> None:
        session = self._session
        if self._load_scan_stopping(session):
            self._load_scan_failed(STOPPED_CLOSING, "a close request came before the load")
            return
        if not self._load_holder(session):
            return
        with self._lock:
            mark = self._next_latch_sequence()  # polls asked from now on see the loaded holder
            self._load_scan_phase = "waiting_ready"
        stop = self._await_fresh_ready(session, mark)
        if stop is None:
            stop = self._hand_over_to_scan(session)
        if stop is not None:
            self._load_scan_failed(*stop)

    def _load_holder(self, session) -> bool:
        """One load, never retried; False after the failure was shown and the latch dropped."""
        with self._lock:
            sequence = self._next_latch_sequence()
        try:
            session.holder.load_next_strip()
        except ScannerInUse as exc:
            self._load_scan_failed(ANOTHER_PROGRAM, f"ScannerInUse: {exc}", forget=False)
        except ScannerRefused as exc:  # refused before the driver started: nothing moved
            cut_off = isinstance(exc, ScannerInterrupted)
            text = SCANNER_INTERRUPTED if cut_off else REFUSED
            self._load_scan_failed(text, f"{type(exc).__name__}: {exc}", forget=cut_off)
        except ScanError as exc:
            self._load_scan_failed(holder_failed_text("load"), f"ScanError: {exc}")
        except BaseException:
            self._remember_holder_latch(holder_latch.invalidate())
            raise
        else:
            self._remember_holder_latch(holder_latch.after_load(True), sequence=sequence)
            return True
        session.poller.poll()  # ask again now: the move failed
        return False

    def _await_fresh_ready(self, session, mark: int) -> tuple[str, str] | None:
        """None once a poll asked after the load says READY; else why it stopped."""
        poller, started = session.poller, self._load_clock()
        while True:
            if self._load_scan_stopping(session):
                return STOPPED_CLOSING, "a close request came during the wait"
            status, sequence = poller.snapshot()
            if sequence > mark and status is ScannerStatus.READY:
                log.info("Load & scan: READY %.1f s after the load", self._load_clock() - started)
                return None
            if sequence > mark and status in STOP_STATUSES:
                return STOPPED_ANOMALY, f"status {status.value} after the load"
            if self._load_clock() - started >= self._load_ready_timeout_s:
                elapsed = self._load_clock() - started
                log.warning("Load & scan: no fresh READY %.1f s after the load", elapsed)
                return READY_TIMEOUT, f"no fresh READY within {self._load_ready_timeout_s:g} s"
            poller.poll()
            self._load_scan_cancel.wait(READY_POLL_S)

    def _load_scan_stopping(self, session) -> bool:
        return (
            self._closing.is_set()
            or self._load_scan_cancel.is_set()
            or self._session is not session
        )

    def _hand_over_to_scan(self, session) -> tuple[str, str] | None:
        """Release the holder and queue the scan in one critical section; else why not."""
        with self._lock:
            if self._load_scan_stopping(session) or self._lifecycle_pending:
                return STOPPED_CLOSING, "a close request came before the scan"
            if self._holder_latch is not HolderLatch.LATCHED:
                return STOPPED_ANOMALY, f"holder {self._holder_latch.value} before the scan"
            self._holder_busy = self._load_scan_owned = False
            self._holder_future = None
            self._scans_queued += 1
            self._snapshot_auto_eject()
            skipped = self._take_scan_openings()
            self._load_scan_phase = "scanning"
        self._submit_reserved_scan(
            lambda runner: runner.scan_tray(skipped=skipped), "Could not start a scan"
        )
        return None

    def _load_scan_failed(self, message: str, detail: str, forget: bool = True) -> None:
        """Show why it stopped. ``forget``: the holder state is no longer known (UNKNOWN)."""
        if forget and message != STOPPED_CLOSING:
            self._remember_holder_latch(holder_latch.invalidate())  # a fresh sequence: it wins
        log.warning("Load & scan stopped: %s", detail)
        with self._lock:
            self._load_scan_phase = "failed"
            self._banner = Banner(message, detail)

    def _load_scan_ended(self) -> None:
        """The job ended, was cancelled before it started, or was never submitted."""
        with self._lock:
            if not self._load_scan_owned:
                return  # handed over to the scan: the holder reservation is already released
            self._load_scan_owned = self._holder_busy = False
            self._holder_future = None
            if self._load_scan_phase in ("loading", "waiting_ready"):
                self._load_scan_phase = "failed"
