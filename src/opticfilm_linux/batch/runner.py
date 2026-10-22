"""Drives scanning and background enhancement for a batch.

One worker scans (the hardware can do one thing at a time) and one worker enhances, so a tray
is enhanced while the next one scans. The caller never blocks: it asks for a scan, then reads
the manifest or the event queue. Everything the workers do ends in the manifest, so a crash
loses at most the tray being scanned.
"""

import dataclasses
import os
import queue
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait

from opticfilm_linux.batch.enhance import enhance_tray
from opticfilm_linux.batch.events import BatchEvent, SlideUpdated, TrayFailed, TrayStageChanged
from opticfilm_linux.batch.model import BatchManifest, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.model import ScanSettings
from opticfilm_linux.scanner.base import Scanner, ScannerNotReady, ScannerStatus
from opticfilm_linux.sweepstore import save_sweep

RESCANNABLE = (TrayStage.REVIEW, TrayStage.FAILED)


class BatchRunner:
    def __init__(self, store: BatchStore, scanner: Scanner) -> None:
        self._store = store
        self._scanner = scanner
        self.events: queue.Queue[BatchEvent] = queue.Queue()
        self._scan_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scan")
        self._enhance_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="enhance")
        self._pending: set[Future] = set()
        self._lock = threading.Lock()

    # -- requests from the caller (never block) ------------------------------------------

    def scan_tray(self) -> int:
        """Start scanning a new tray; the scanner must be ready. Returns the tray number."""
        self._require_ready()
        defaults = self._store.snapshot().defaults

        def add(manifest: BatchManifest) -> BatchManifest:
            tray = TrayRecord(
                number=len(manifest.trays) + 1, stage=TrayStage.SCANNING, mode=defaults.mode
            )
            return dataclasses.replace(manifest, trays=manifest.trays + (tray,))

        number = self._store.update(add).trays[-1].number
        self._emit(TrayStageChanged(number, TrayStage.SCANNING))
        self._submit(self._scan_pool, self._scan_job, number)
        return number

    def rescan_tray(self, number: int) -> int:
        """Scan a tray again, keeping the old sweep. The tray must be in review or failed."""
        tray = self._tray(number)
        if tray.stage not in RESCANNABLE:
            raise BatchError(
                f"tray {number} is {tray.stage}; it can only be re-scanned from review"
            )
        self._require_ready()
        self._store.update_tray(
            number,
            stage=TrayStage.SCANNING,
            sweep_version=tray.sweep_version + 1,
            slides=(),
            error=None,
        )
        self._emit(TrayStageChanged(number, TrayStage.SCANNING))
        self._submit(self._scan_pool, self._scan_job, number)
        return number

    def resume(self) -> None:
        """Apply crash recovery and queue enhancement for every tray still waiting for it."""
        for number in self._store.recover_interrupted():
            self._submit(self._enhance_pool, self._enhance_job, number)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until all queued and running work is finished; False if the timeout hit first."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                pending = list(self._pending)
            if not pending:
                return True
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            _done, not_done = wait(pending, timeout=remaining)
            if not_done:
                return False

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
            self._fail(number, _describe(exc))
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
                on_progress=lambda done, _t: self._emit(SlideUpdated(number, done)),
            )
        except Exception as exc:
            self._fail(number, _describe(exc))
            return
        tray = self._tray(number)
        self._emit(TrayStageChanged(number, tray.stage))
        if tray.stage is TrayStage.FAILED:
            self._emit(TrayFailed(number, tray.error or "enhancement failed"))

    # -- helpers -------------------------------------------------------------------------

    def _require_ready(self) -> None:
        status = self._scanner.status()
        if status is not ScannerStatus.READY:
            raise ScannerNotReady(f"the scanner is not ready ({status})")

    def _tray(self, number: int) -> TrayRecord:
        for tray in self._store.snapshot().trays:
            if tray.number == number:
                return tray
        raise BatchError(f"no tray {number} in this batch")

    def _fail(self, number: int, message: str) -> None:
        self._store.update_tray(number, stage=TrayStage.FAILED, error=message)
        self._emit(TrayStageChanged(number, TrayStage.FAILED))
        self._emit(TrayFailed(number, message))

    def _emit(self, event: BatchEvent) -> None:
        self.events.put(event)

    def _submit(self, pool: ThreadPoolExecutor, job: Callable[..., None], *args) -> None:
        future = pool.submit(job, *args)
        with self._lock:
            self._pending.add(future)
        future.add_done_callback(self._finished)

    def _finished(self, future: Future) -> None:
        with self._lock:
            self._pending.discard(future)


def _describe(exc: Exception) -> str:
    text = str(exc)
    return text if text and not isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {text}"
