"""Eject and load-next-holder for the controller, kept apart from scans and long jobs.

A holder movement and a scan share one reservation under the controller's lock: a movement is
refused while a scan is queued or running, and a scan while a movement is. A movement never
waits behind an export either: it is refused at once, runs on its own worker, and at most one is
ever pending. Closing or reopening the batch cancels one that has not started; while one is
running the batch is left as it is and the owner is told to wait.
"""

from collections.abc import Callable
from concurrent.futures import Future

from opticfilm_linux.app.messages import holder_failed_text
from opticfilm_linux.app.state import Banner
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError
from opticfilm_linux.scanner.base import HolderControl, ScanError, ScannerStatus
from opticfilm_linux.scanner.driver import ScannerInUse, confirm_scanner_reset
from opticfilm_linux.scanner.interrupted import ScannerInterrupted, ScannerRefused

ANOTHER_PROGRAM = "Another program is using the scanner. Close it or wait for it to finish."
WAIT_SCAN = "Wait for the scan to finish"
WAIT_HOLDER = "Wait for the eject/load to finish"
WAIT_EXPORT = "Wait for the export to finish, then try again"
SCANNER_INTERRUPTED = (
    "A scan or holder move was cut off, so nothing moves the scanner until you confirm it was "
    "reset: power-cycle it, load the holder again, then press Confirm scanner reset."
)
REFUSED = "The scanner command was refused before it started; nothing moved. See Details."
NEXT_STRIP_OFF = (
    "Load next holder is switched off: it is not proven on this scanner yet. "
    'Follow "How to load the scanner" instead.'
)


class HolderCommands:
    """Mixed into ``Controller``, whose lock, session, workers and counters it uses."""

    @property
    def next_strip_enabled(self) -> bool:
        return self._next_strip

    def eject_holder(self) -> None:
        self._move_holder("eject", lambda holder: holder.eject(), "Could not eject the holder")

    def load_next_holder(self) -> None:
        if not self._next_strip:
            self._set(_banner=Banner(NEXT_STRIP_OFF))
            return
        self._move_holder(
            "load", lambda holder: holder.load_next_strip(), "Could not load the next holder"
        )

    def _move_holder(
        self, action: str, move: Callable[[HolderControl], None], message: str
    ) -> None:
        def job(_runner: BatchRunner) -> None:
            session = self._session
            if session is None or session.holder is None:
                raise BatchError("this scanner cannot move the holder")
            try:
                move(session.holder)
            except ScannerInUse as exc:
                self._set(_banner=Banner(ANOTHER_PROGRAM, f"ScannerInUse: {exc}"))
            except ScannerRefused as exc:  # refused before the driver started: not a failure
                text = SCANNER_INTERRUPTED if isinstance(exc, ScannerInterrupted) else REFUSED
                self._set(_banner=Banner(text, f"{type(exc).__name__}: {exc}"))
            except ScanError as exc:
                self._set(_banner=Banner(holder_failed_text(action), f"ScanError: {exc}"))
            finally:
                if session.poller is not None:
                    session.poller.poll()  # ask again now: the holder moved, or the move failed

        cut_off = self._scanner_cut_off()  # read outside the lock: the poller may report
        with self._lock:
            if self._holder_busy:
                return  # a second click while one is pending
            refusal = SCANNER_INTERRUPTED if cut_off else self._holder_refusal()
            self._holder_busy = refusal is None
        if refusal is not None:
            self._set(_banner=Banner(refusal))
            return
        future = self._run_writer(job, message, pool=self._holder_pool)
        self._track(future, lambda: self._set(_holder_busy=False, _holder_future=None))
        with self._lock:
            if future is not None and not future.done():
                self._holder_future = future

    def confirm_scanner_reset(self) -> None:
        """The owner says the scanner was power-cycled and reloaded. Never talks to the scanner;
        refused while a scan or holder move is queued or running, here or in another program."""

        def job(_runner: BatchRunner) -> None:
            session = self._session
            try:
                confirm_scanner_reset()
                self._set(_banner=None)
            except ScannerInUse as exc:
                self._set(_banner=Banner(ANOTHER_PROGRAM, f"ScannerInUse: {exc}"))
            except ScannerRefused as exc:
                self._set(_banner=Banner(REFUSED, f"ScannerRefused: {exc}"))
            finally:
                if session is not None and session.poller is not None:
                    session.poller.poll()

        with self._lock:
            refusal = WAIT_HOLDER if self._holder_busy else self._holder_refusal()
            self._holder_busy = refusal is None
        if refusal is not None:
            self._set(_banner=Banner(refusal))
            return
        message = "Could not confirm the scanner reset"
        future = self._run_writer(job, message, pool=self._holder_pool)
        self._track(future, lambda: self._set(_holder_busy=False, _holder_future=None))

    def _scanner_cut_off(self) -> bool:
        session = self._session
        poller = session.poller if session is not None else None
        return poller is not None and poller.current() is ScannerStatus.INTERRUPTED

    def _holder_refusal(self) -> str | None:
        """Why a movement cannot start now; called under the lock."""
        session = self._session
        runner = session.runner if session is not None else None
        if self._scans_queued or (runner is not None and runner.scan_pending()):
            return WAIT_SCAN
        if self._long_jobs or self._job_futures:
            return WAIT_EXPORT
        return None

    def _reserved_scan(self, job: Callable[[BatchRunner], None], message: str) -> None:
        """Submit a scan, unless the holder is moving; the scan counts as queued until the
        runner has it."""
        with self._lock:
            refused = self._holder_busy
            if not refused:
                self._scans_queued += 1
        if refused:
            self._set(_banner=Banner(WAIT_HOLDER))
            return
        future = self._run_writer(job, message, droppable=True)
        self._track(future, self._scan_handed_over)

    def _scan_handed_over(self) -> None:
        with self._lock:
            self._scans_queued -= 1

    def _cancel_queued_holder(self) -> None:
        """A movement that has not started never runs once the batch is closing."""
        with self._lock:
            future = self._holder_future
        if future is not None:
            future.cancel()  # a running one cannot be cancelled: it completes

    def _holder_move_running(self) -> bool:
        """Cancel a movement that has not started; if one is still running, tell the owner to wait
        and return True, so the caller leaves the batch as it is."""
        self._cancel_queued_holder()
        with self._lock:
            running = self._holder_busy
        if running:
            self._set(_banner=Banner(WAIT_HOLDER))
        return running

    @staticmethod
    def _track(future: Future | None, done: Callable[[], None]) -> None:
        """Call ``done`` when the job has ended, was cancelled, or was never submitted."""
        if future is None:
            done()
        else:
            future.add_done_callback(lambda _f: done())
