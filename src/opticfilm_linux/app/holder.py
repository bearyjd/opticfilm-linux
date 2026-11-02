"""Eject and load-next-holder for the controller, kept apart from scans and long jobs.

A holder movement and a scan share one reservation under the controller's lock: a movement is
refused while a scan is queued or running, and a scan while a movement is. A movement runs on
its own worker alongside exports and free-space jobs, and at most one is ever pending. Closing
or reopening the batch cancels one that has not started; while one is running the batch is left
as it is and the owner is told to wait.
"""

import logging
from collections.abc import Callable
from concurrent.futures import Future

from opticfilm_linux.app.messages import holder_failed_text
from opticfilm_linux.app.state import Banner
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError
from opticfilm_linux.scanner import holder_latch
from opticfilm_linux.scanner.base import HolderControl, ScanError, ScannerStatus
from opticfilm_linux.scanner.driver import ScannerInUse, confirm_scanner_reset
from opticfilm_linux.scanner.interrupted import ScannerInterrupted, ScannerRefused

log = logging.getLogger(__name__)

ANOTHER_PROGRAM = "Another program is using the scanner. Close it or wait for it to finish."
WAIT_SCAN = "Wait for the scan to finish"
WAIT_HOLDER = "Wait for the eject/load to finish"
WAIT_BATCH = "Wait for the batch change to finish"
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
            with self._lock:
                sequence = self._next_latch_sequence()
            try:
                move(session.holder)
                outcome = getattr(session.holder, "eject_outcome", None)
                latch = (
                    holder_latch.after_load(True)
                    if action == "load"
                    else holder_latch.after_eject(outcome)
                )
                self._remember_holder_latch(latch, sequence=sequence)
            except ScannerInUse as exc:
                self._set(_banner=Banner(ANOTHER_PROGRAM, f"ScannerInUse: {exc}"))
            except ScannerRefused as exc:  # refused before the driver started: not a failure
                if isinstance(exc, ScannerInterrupted):
                    self._remember_holder_latch(holder_latch.invalidate(), sequence=sequence)
                text = SCANNER_INTERRUPTED if isinstance(exc, ScannerInterrupted) else REFUSED
                self._set(_banner=Banner(text, f"{type(exc).__name__}: {exc}"))
            except ScanError as exc:
                self._remember_holder_latch(holder_latch.invalidate(), sequence=sequence)
                self._set(_banner=Banner(holder_failed_text(action), f"ScanError: {exc}"))
            except BaseException:
                self._remember_holder_latch(holder_latch.invalidate(), sequence=sequence)
                raise
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
            with self._lock:
                sequence = self._next_latch_sequence()
            try:
                confirm_scanner_reset()
                self._remember_holder_latch(holder_latch.invalidate(), sequence=sequence)
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
            if refusal is None and (self._long_jobs or self._job_futures):
                refusal = WAIT_EXPORT
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

    def _begin_latch_operation(self) -> int:
        with self._lock:
            return self._next_latch_sequence()

    def _next_latch_sequence(self) -> int:
        """Allocate when an operation starts, with the controller lock held."""
        self._latch_sequence += 1
        return self._latch_sequence

    def _queue_latch_io(self, job) -> None:
        """Only the dedicated worker accesses storage; the caller never waits for it."""

        def run():
            # Submission can happen under the lock, but storage never does.
            with self._lock:
                pass
            try:
                job()
            except Exception:
                log.exception("could not persist holder latch information")

        try:
            self._latch_pool.submit(run)
        except Exception:
            log.exception("could not schedule holder latch persistence")

    def _sync_holder_latch(self, scanner) -> None:
        """Under lock: compare cached identity and enqueue persistence snapshots."""
        address = getattr(scanner, "usb_address", None)
        if address == self._latch_address:
            return
        previous, self._latch_address = self._latch_address, address
        if previous is None:
            self._latch_loss = None
            # First discovery identifies the same in-flight operation; it must not
            # outrank its result or restore a record from a previous session.
            self._save_holder_latch()
            return
        applied = self._latch_applied
        sequence = self._next_latch_sequence()
        self._latch_loss = (sequence, applied) if address is None else None
        if address is None:
            self._queue_latch_io(
                lambda: holder_latch.save_latch(*previous, holder_latch.HolderLatch.UNKNOWN)
            )
        self._holder_latch = holder_latch.invalidate()
        self._latch_applied = sequence
        self._latch_invalidated = True
        self._save_holder_latch()

    def _save_holder_latch(self) -> None:
        """Under lock: enqueue immutable state; in-memory knowledge is authoritative."""
        address, state = self._latch_address, self._holder_latch
        if address is not None:
            self._queue_latch_io(lambda: holder_latch.save_latch(*address, state))

    def _remember_holder_latch(
        self, latch: holder_latch.HolderLatch, scanner=None, *, sequence: int | None = None
    ) -> None:
        with self._lock:
            if sequence is None:
                sequence = self._next_latch_sequence()
            session = self._session
            self._sync_holder_latch(scanner or (session.scanner if session else None))
            loss = self._latch_loss
            if (
                latch is holder_latch.HolderLatch.RELEASED
                and self._latch_address is None
                and loss is not None
                and self._latch_applied == loss[0]
                and sequence >= loss[1]
            ):
                # Missing identity cannot negate the completion of this eject.
                sequence = loss[0]
            if sequence < self._latch_applied:
                return
            self._holder_latch = latch
            self._latch_applied = sequence
            self._latch_invalidated = latch is holder_latch.HolderLatch.UNKNOWN
            self._save_holder_latch()

    def confirm_holder_latched(self) -> None:
        """Record the owner's physical confirmation without moving the scanner."""
        with self._lock:
            if (
                self._session is None
                or self._session.runner is None
                or self._holder_busy
                or self._scans_queued
                or self._session.runner.scan_pending()
                or self._long_jobs
                or self._job_futures
                or self._holder_latch is not holder_latch.HolderLatch.RELEASED
            ):
                return
            self._holder_latch = holder_latch.HolderLatch.LATCHED
            self._latch_applied = self._next_latch_sequence()
            self._latch_invalidated = False
            self._save_holder_latch()
            self._banner = None

    def _holder_refusal(self) -> str | None:
        """Why a movement cannot start now; called under the lock."""
        if self._lifecycle_pending or self._closing.is_set():
            return WAIT_BATCH
        session = self._session
        runner = session.runner if session is not None else None
        if self._scans_queued or (runner is not None and runner.scan_pending()):
            return WAIT_SCAN
        return None

    def _reserved_scan(self, job: Callable[[BatchRunner], None], message: str) -> None:
        """Submit a scan, unless the holder is moving; the scan counts as queued until the
        runner has it."""
        with self._lock:
            refused = self._holder_busy
            released = self._holder_latch is holder_latch.HolderLatch.RELEASED
            if released:
                self._banner = Banner(
                    "Seat and latch the holder, then confirm it is latched before scanning."
                )
                return
            if not refused:
                self._scans_queued += 1
        if refused:
            self._set(_banner=Banner(WAIT_HOLDER))
            return

        def sequenced_job(runner):
            with self._lock:
                runner.operation_sequence = self._next_latch_sequence()
            try:
                job(runner)
            except BaseException:
                self._remember_holder_latch(
                    holder_latch.HolderLatch.UNKNOWN, sequence=runner.operation_sequence
                )
                raise

        future = self._run_writer(sequenced_job, message, droppable=True)
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
