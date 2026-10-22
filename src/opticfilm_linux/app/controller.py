"""The Qt-free brain of the window.

The controller owns the batch store, runner and scanner. Every call into the core, and every
scanner status poll, runs on a worker thread, so none of the public methods block. The UI thread
calls ``tick()`` on a timer and reads ``state()``, an immutable snapshot that is only ever
replaced, never changed.
"""

import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from opticfilm_linux.app.state import (
    EMPTY_STATE,
    AppState,
    Banner,
    ExportView,
    FreeSpaceView,
    build_state,
)
from opticfilm_linux.batch.events import SlideUpdated, TrayFailed, TrayStageChanged
from opticfilm_linux.batch.manifest import ManifestFormatError
from opticfilm_linux.batch.model import BatchDefaults, TrayStage
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchLocked, BatchStore
from opticfilm_linux.scanner.base import Scanner, ScannerStatus

SLIDES_PER_TRAY = 4
LOCKED_PREFIX = "LOCKED:"

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class _Session:
    """What is open right now. Replaced as a whole when a batch is opened or closed."""

    store: BatchStore
    runner: BatchRunner | None  # None for a read-only batch
    scanner: Scanner | None


class Controller:
    def __init__(
        self,
        scanner_factory: Callable[[int], Scanner],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = scanner_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._command = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app-command")
        self._refresher = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app-status")
        self._refreshing = False
        self._session: _Session | None = None
        self._scanner_status = ScannerStatus.UNKNOWN
        self._selected: int | None = None
        self._updating: frozenset[tuple[int, int]] = frozenset()
        self._progress: dict[int, float] = {}
        self._scan_started: dict[int, float] = {}
        self._enhancing: set[int] = set()
        self._banner: Banner | None = None
        self._export: ExportView | None = None
        self._free_space: FreeSpaceView | None = None
        self._cancel_export = threading.Event()
        self._state = EMPTY_STATE
        self._reported = EMPTY_STATE

    # -- what the UI thread uses (never blocks) ------------------------------------------

    def state(self) -> AppState:
        with self._lock:
            return self._state

    def tick(self) -> bool:
        """Drain runner events, start a refresh if none is running; True if the state changed."""
        self._drain_events()
        with self._lock:
            start = not self._refreshing
            self._refreshing = True
        if start:
            self._refresher.submit(self._refresh)
        with self._lock:
            changed = self._state is not self._reported
            self._reported = self._state
        return changed

    def preview_path(self, number: int, position: int, kind: Literal["before", "after"]):
        session = self._session
        if session is None:
            return None
        path = session.store.preview_path(number, position, kind)
        return path if path.is_file() else None

    def dismiss_banner(self) -> None:
        self._set(_banner=None)

    # -- batch lifecycle -----------------------------------------------------------------

    def new_batch(self, directory: Path, name: str, album: str, date: str | None, dpi: int) -> None:
        def job() -> None:
            self._detach()
            defaults = BatchDefaults(dpi=dpi)
            self._attach(BatchStore.create(directory, name, album, date, defaults), False)

        self._run(job, "Could not create the batch")

    def open_batch(self, directory: Path, readonly: bool = False) -> None:
        def job() -> None:
            self._detach()
            self._attach(BatchStore.open(directory, readonly=readonly), readonly)

        self._run(job, "Could not open the batch")

    def close_batch(self) -> None:
        self._run(self._detach, "Could not close the batch")

    # -- scanning ------------------------------------------------------------------------

    def scan(self) -> None:
        self._run_writer(lambda runner: runner.scan_tray(), "Could not start a scan")

    def rescan(self, number: int) -> None:
        def job(runner: BatchRunner) -> None:
            self._drop_progress(number)
            runner.rescan_tray(number)

        self._run_writer(job, f"Could not re-scan tray {number}")

    def select_tray(self, number: int) -> None:
        self._set(_selected=number)

    # -- closing the window --------------------------------------------------------------

    def shutdown_decision(self) -> Literal["ok", "scan_running"]:
        session = self._session
        if session is not None and session.runner is not None and session.runner.scan_pending():
            return "scan_running"
        return "ok"

    def shutdown(self) -> None:
        """Cancel an export, finish the job running now, drop queued work, release the batch."""
        self._cancel_export.set()
        self._command.shutdown(wait=True)
        self._detach()
        self._refresher.shutdown(wait=True)

    # -- internals shared with the other controller modules ------------------------------

    def _run(self, job: Callable[[], None], message: str) -> None:
        """Run ``job`` on the command worker; any failure becomes a banner, never an exception."""

        def guarded() -> None:
            try:
                job()
            except BatchLocked as exc:
                self._set(_banner=Banner("This batch is open in another window", _locked(exc)))
            except (BatchError, ManifestFormatError, OSError, ValueError, LookupError) as exc:
                self._set(_banner=Banner(f"{message}: {exc}", f"{type(exc).__name__}: {exc}"))
            except Exception as exc:  # last resort: a banner beats a silent dead worker
                log.exception("%s", message)
                self._set(_banner=Banner(message, f"{type(exc).__name__}: {exc}"))

        try:
            self._command.submit(guarded)
        except RuntimeError:  # shut down already
            log.debug("command ignored after shutdown")

    def _run_writer(self, job: Callable[[BatchRunner], None], message: str) -> None:
        def guarded() -> None:
            session = self._session
            if session is None:
                raise BatchError("no batch is open")
            if session.runner is None:
                raise BatchError("this batch is open read-only")
            job(session.runner)

        self._run(guarded, message)

    def _set(self, **changes) -> None:
        """Replace controller fields (names start with ``_``) under the lock."""
        with self._lock:
            for key, value in changes.items():
                setattr(self, key, value)

    def _drop_progress(self, number: int) -> None:
        with self._lock:
            self._progress = {k: v for k, v in self._progress.items() if k != number}

    def _attach(self, store: BatchStore, readonly: bool) -> None:
        runner = None
        scanner = None
        if not readonly:
            scanner = self._factory(store.snapshot().defaults.dpi)
            runner = BatchRunner(store, scanner)
            runner.resume()
        first = store.snapshot().trays[0].number if store.snapshot().trays else None
        with self._lock:
            self._session = _Session(store, runner, scanner)
            self._selected = first
            self._banner = None
            self._scanner_status = ScannerStatus.UNKNOWN
            self._updating = frozenset()
            self._progress = {}
            self._scan_started = {}
            self._enhancing = set()
            self._export = None

    def _detach(self) -> None:
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            if session.runner is not None:
                session.runner.stop()
            session.store.close()
        with self._lock:
            self._selected = None
            self._scanner_status = ScannerStatus.UNKNOWN
            self._state = EMPTY_STATE if self._banner is None else self._banner_only()

    def _banner_only(self) -> AppState:
        return dataclasses.replace(EMPTY_STATE, banner=self._banner)

    # -- events and refresh --------------------------------------------------------------

    def _drain_events(self) -> None:
        session = self._session
        if session is None or session.runner is None:
            return
        events = session.runner.events
        while not events.empty():
            event = events.get_nowait()
            with self._lock:
                self._apply_event(event)

    def _apply_event(self, event) -> None:
        if isinstance(event, TrayStageChanged):
            self._on_stage(event.number, event.stage)
        elif isinstance(event, SlideUpdated):
            self._updating = self._updating - {(event.number, event.position)}
            if event.number in self._enhancing:
                self._progress = {
                    **self._progress,
                    event.number: min(event.position / SLIDES_PER_TRAY, 1.0),
                }
        elif isinstance(event, TrayFailed):
            self._banner = Banner(f"Tray {event.number} failed", event.message)

    def _on_stage(self, number: int, stage: TrayStage) -> None:
        if stage is TrayStage.SCANNING:
            self._scan_started = {**self._scan_started, number: self._clock()}
        else:
            self._scan_started = {k: v for k, v in self._scan_started.items() if k != number}
        if stage is TrayStage.ENHANCING:
            self._enhancing = self._enhancing | {number}
        else:
            self._enhancing = self._enhancing - {number}
            self._progress = {k: v for k, v in self._progress.items() if k != number}

    def _refresh(self) -> None:
        try:
            self._rebuild()
        except Exception:  # a failed refresh must not kill the polling worker
            log.exception("could not refresh the window state")
        finally:
            with self._lock:
                self._refreshing = False

    def _rebuild(self) -> None:
        session = self._session
        status = self._poll_scanner(session)
        manifest = session.store.snapshot() if session is not None else None
        with self._lock:
            if session is not self._session:
                return  # the batch changed while we were polling; the next refresh will do
            selected = self._selected
            tokens = self._preview_tokens(session, manifest, selected)
            self._state = build_state(
                manifest,
                preview_tokens=tokens,
                scanner=status,
                scan_pending=bool(session and session.runner and session.runner.scan_pending()),
                readonly=bool(session and session.runner is None),
                selected=selected,
                updating=self._updating,
                progress=self._progress,
                scan_started=self._scan_started,
                now=self._clock(),
                banner=self._banner,
                export=self._export,
                free_space=self._free_space,
            )

    def _poll_scanner(self, session: _Session | None) -> ScannerStatus:
        if session is None or session.scanner is None:
            return ScannerStatus.UNKNOWN
        try:
            status = session.scanner.status()
        except Exception as exc:  # the backend may raise anything; show it, keep polling
            status = ScannerStatus.ERROR
            if self._scanner_status is not ScannerStatus.ERROR:
                self._set(_banner=Banner("Scanner error", f"{type(exc).__name__}: {exc}"))
        self._scanner_status = status
        return status

    @staticmethod
    def _preview_tokens(session, manifest, selected) -> dict[tuple[int, int], int]:
        if session is None or manifest is None:
            return {}
        tray = next((t for t in manifest.trays if t.number == selected), None)
        if tray is None:
            return {}
        tokens = {}
        for slide in tray.slides:
            path = session.store.preview_path(tray.number, slide.position, "after")
            try:
                tokens[(tray.number, slide.position)] = path.stat().st_mtime_ns
            except OSError:
                continue
        return tokens


def _locked(exc: Exception) -> str:
    return f"{LOCKED_PREFIX} {exc}"
