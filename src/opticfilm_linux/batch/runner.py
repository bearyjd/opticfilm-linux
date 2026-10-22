"""Drives scanning and background enhancement for a batch.

One worker scans (the hardware can do one thing at a time) and one worker enhances, so a tray
is enhanced while the next one scans. The caller never blocks: it asks for a scan, then reads
the manifest or the event queue. Everything the workers do ends in the manifest, so a crash
loses at most the tray being scanned. Every state change is a conditional update, so a late or
stale job can never overwrite what happened meanwhile, and a job that dies unexpectedly always
ends as a visible FAILED tray plus a ``TrayFailed`` event.
"""

import dataclasses
import logging
import os
import queue
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait

from opticfilm_linux.batch import review
from opticfilm_linux.batch.enhance import enhance_tray, reenhance_slide
from opticfilm_linux.batch.events import BatchEvent, SlideUpdated, TrayFailed, TrayStageChanged
from opticfilm_linux.batch.model import (
    BatchManifest,
    Mode,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.model import ScanSettings
from opticfilm_linux.scanner.base import Scanner, ScannerNotReady, ScannerStatus
from opticfilm_linux.sweepstore import save_sweep

RESCANNABLE = (TrayStage.REVIEW, TrayStage.FAILED)
IN_FLIGHT = (TrayStage.SCANNING, TrayStage.SCANNED, TrayStage.ENHANCING)

log = logging.getLogger(__name__)


class BatchRunner:
    def __init__(self, store: BatchStore, scanner: Scanner) -> None:
        self._store = store
        self._scanner = scanner
        self.events: queue.Queue[BatchEvent] = queue.Queue()
        self._scan_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scan")
        self._enhance_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="enhance")
        self._pending: set[Future] = set()
        self._scans: set[Future] = set()
        self._lock = threading.Lock()
        self._scan_guard = threading.Lock()

    # -- requests from the caller (never block) ------------------------------------------

    def scan_tray(self) -> int:
        """Start scanning a new tray; the scanner must be ready and no scan pending."""
        self._require_ready()
        defaults = self._store.snapshot().defaults

        def add(manifest: BatchManifest) -> BatchManifest:
            tray = TrayRecord(
                number=len(manifest.trays) + 1, stage=TrayStage.SCANNING, mode=defaults.mode
            )
            return dataclasses.replace(manifest, trays=manifest.trays + (tray,))

        with self._scan_guard:
            self._require_no_scan_pending()
            number = self._store.update(add).trays[-1].number
            self._emit(TrayStageChanged(number, TrayStage.SCANNING))
            self._submit(self._scan_pool, self._scan_job, number, scan=True)
        return number

    def rescan_tray(self, number: int) -> int:
        """Scan a tray again, keeping the old sweep. The tray must be in review or failed."""
        tray = self._tray(number)
        if tray.stage not in RESCANNABLE:
            raise BatchError(
                f"tray {number} is {tray.stage}; it can only be re-scanned from review"
            )
        self._require_ready()
        with self._scan_guard:
            self._require_no_scan_pending()
            started = self._store.update_tray_if(
                number,
                lambda t: t.stage in RESCANNABLE and t.sweep_version == tray.sweep_version,
                stage=TrayStage.SCANNING,
                sweep_version=tray.sweep_version + 1,
                slides=(),
                error=None,
            )
            if not started:
                raise BatchError(f"tray {number} changed meanwhile; try again")
            self._emit(TrayStageChanged(number, TrayStage.SCANNING))
            self._submit(self._scan_pool, self._scan_job, number, scan=True)
        return number

    def resume(self) -> None:
        """Apply crash recovery and queue enhancement for every tray still waiting for it."""
        for number in self._store.recover_interrupted():
            self._submit(self._enhance_pool, self._enhance_job, number)

    def edit_slide(self, number: int, position: int, settings: SlideSettings) -> None:
        """Apply per-slide settings now; the slide is re-enhanced in the background."""
        review.apply_slide_settings(self._store, number, position, settings)
        self._submit(self._enhance_pool, self._reenhance_job, number, position)

    def set_slide_status(self, number: int, position: int, status: SlideStatus) -> None:
        review.set_slide_status(self._store, number, position, status)
        self._emit(SlideUpdated(number, position))

    def accept_all(self) -> None:
        review.accept_all(self._store)
        for tray in self._store.snapshot().trays:
            if tray.stage is TrayStage.REVIEW:
                for slide in tray.slides:
                    self._emit(SlideUpdated(tray.number, slide.position))

    def set_tray_mode(self, number: int, mode: Mode) -> None:
        """Change a tray's mode; the whole tray is re-enhanced, keeping the owner's choices."""
        review.set_tray_mode(self._store, number, mode)
        self._submit(self._enhance_pool, self._enhance_job, number)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until all queued and running work is finished; False if the timeout hit first."""
        return self._wait_for(self._pending, timeout)

    def wait_scans_idle(self, timeout: float | None = None) -> bool:
        """Block until no scan is pending (enhancement may still be running)."""
        return self._wait_for(self._scans, timeout)

    def scan_pending(self) -> bool:
        with self._lock:
            return bool(self._scans)

    def stop(self) -> None:
        """Stop soon: drop jobs that have not started, finish the one running now.

        Trays left half-done are picked up by ``resume()`` the next time the batch is opened.
        """
        self._scan_pool.shutdown(wait=True, cancel_futures=True)
        self._enhance_pool.shutdown(wait=True, cancel_futures=True)

    def close(self) -> None:
        """Finish running work, then stop both workers. Queued enhancement still runs."""
        self._scan_pool.shutdown(wait=True)
        self._enhance_pool.shutdown(wait=True)

    # -- worker jobs ---------------------------------------------------------------------

    def _scan_job(self, number: int) -> None:
        tray = self._tray(number)
        defaults = self._store.snapshot().defaults
        settings = ScanSettings(dpi=defaults.dpi, mode=tray.mode, infrared=defaults.infrared)
        tmp = self._store.sweep_tmp_dir(number, tray.sweep_version)
        final = self._store.sweep_dir(number, tray.sweep_version)
        try:
            save_sweep(self._scanner.scan_holder(settings), tmp)
            os.replace(tmp, final)
        except Exception as exc:  # a failed scan must end as a visible FAILED tray, never vanish
            if tmp.is_dir() and not tmp.is_symlink():
                shutil.rmtree(tmp, ignore_errors=True)
            self._fail(number, _describe(exc), (TrayStage.SCANNING,))
            return
        self._store.update_tray(number, stage=TrayStage.SCANNED)
        self._emit(TrayStageChanged(number, TrayStage.SCANNED))
        self._submit(self._enhance_pool, self._enhance_job, number)

    def _enhance_job(self, number: int) -> None:
        self._emit(TrayStageChanged(number, TrayStage.ENHANCING))
        try:
            enhance_tray(
                self._store,
                number,
                on_progress=lambda done, _total: self._emit(SlideUpdated(number, done)),
            )
        except Exception as exc:
            self._fail(number, _describe(exc), (TrayStage.SCANNED, TrayStage.ENHANCING))
            return
        tray = self._tray(number)
        self._emit(TrayStageChanged(number, tray.stage))
        if tray.stage is TrayStage.FAILED:
            self._emit(TrayFailed(number, tray.error or "enhancement failed"))

    def _reenhance_job(self, number: int, position: int) -> None:
        try:
            reenhance_slide(self._store, number, position)
        except Exception as exc:  # the tray stays in review; the problem is reported as an event
            self._emit(TrayFailed(number, _describe(exc)))
            return
        self._emit(SlideUpdated(number, position))

    # -- helpers -------------------------------------------------------------------------

    def _require_ready(self) -> None:
        status = self._scanner.status()
        if status is not ScannerStatus.READY:
            raise ScannerNotReady(f"the scanner is not ready ({status})")

    def _require_no_scan_pending(self) -> None:
        with self._lock:
            busy = bool(self._scans)
        if busy:
            raise BatchError("a scan is already in progress; wait until it has finished")

    def _tray(self, number: int) -> TrayRecord:
        for tray in self._store.snapshot().trays:
            if tray.number == number:
                return tray
        raise BatchError(f"no tray {number} in this batch")

    def _fail(self, number: int, message: str, expected: tuple[TrayStage, ...]) -> None:
        """Mark a tray FAILED, but only from a stage the failing job owns."""
        applied = self._store.update_tray_if(
            number, lambda t: t.stage in expected, stage=TrayStage.FAILED, error=message
        )
        if applied:
            self._emit(TrayStageChanged(number, TrayStage.FAILED))
        self._emit(TrayFailed(number, message))

    def _emit(self, event: BatchEvent) -> None:
        self.events.put(event)

    def _submit(
        self,
        pool: ThreadPoolExecutor,
        job: Callable[..., None],
        number: int,
        *args,
        scan: bool = False,
    ) -> None:
        future = pool.submit(job, number, *args)
        with self._lock:
            self._pending.add(future)
            if scan:
                self._scans.add(future)
        future.add_done_callback(lambda f: self._finished(f, number))

    def _finished(self, future: Future, number: int) -> None:
        # Report a crash BEFORE leaving the pending sets, so wait_idle() never returns while a
        # failure is still unreported.
        try:
            error = None if future.cancelled() else future.exception()
            if error is not None:
                self._report_crash(number, error)
        finally:
            with self._lock:
                self._pending.discard(future)
                self._scans.discard(future)

    def _report_crash(self, number: int, error: BaseException) -> None:
        """A job died outside its own error handling: never leave the tray stuck or silent."""
        message = _describe(error)  # type: ignore[arg-type]
        log.error("job for tray %s crashed: %s", number, message)
        try:
            applied = self._store.update_tray_if(
                number, lambda t: t.stage in IN_FLIGHT, stage=TrayStage.FAILED, error=message
            )
            if applied:
                self._emit(TrayStageChanged(number, TrayStage.FAILED))
        except Exception:  # the manifest itself may be unwritable (disk full); still report it
            log.exception("could not record the failure of tray %s", number)
        self._emit(TrayFailed(number, message))

    def _wait_for(self, futures: set[Future], timeout: float | None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                pending = list(futures)
            if not pending:
                return True
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            _done, not_done = wait(pending, timeout=remaining)
            if not_done:
                return False


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return text if text and not isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {text}"
