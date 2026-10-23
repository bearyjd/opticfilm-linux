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
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from opticfilm_linux.app.jobs import free_space_candidates, run_export, run_free_space
from opticfilm_linux.app.state import (
    EMPTY_STATE,
    AppState,
    Banner,
    ExportView,
    FreeSpaceView,
    build_state,
)
from opticfilm_linux.app.status import CachedStatusScanner, StatusPoller
from opticfilm_linux.batch.events import SlideUpdated, TrayFailed, TrayStageChanged
from opticfilm_linux.batch.manifest import ManifestFormatError
from opticfilm_linux.batch.model import BatchDefaults, SlideSettings, SlideStatus, TrayStage
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchLocked, BatchStore
from opticfilm_linux.output import Format
from opticfilm_linux.scanner.base import Scanner, ScannerStatus

SLIDES_PER_TRAY = 4
STATUS_TIMEOUT_S = 5.0  # a scanner that has not answered for this long is shown as an error
LOCKED_PREFIX = "LOCKED:"

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class _Session:
    """What is open right now. Replaced as a whole when a batch is opened or closed."""

    store: BatchStore
    runner: BatchRunner | None  # None for a read-only batch
    poller: StatusPoller | None


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
        self._closing = threading.Event()
        self._job_futures: set[Future] = set()
        self._jobs_idle = threading.Condition(self._lock)
        self._selected: int | None = None
        self._updating: frozenset[tuple[int, int]] = frozenset()
        self._progress: dict[int, float] = {}
        self._scan_started: dict[int, float] = {}
        self._enhancing: set[int] = set()
        self._banner: Banner | None = None
        self._export: ExportView | None = None
        self._free_space: FreeSpaceView | None = None
        self._cancel_export = threading.Event()
        self._cancel_free = threading.Event()
        self._jobs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app-jobs")
        self._state = EMPTY_STATE
        self._reported = EMPTY_STATE

    # -- what the UI thread uses (never blocks) ------------------------------------------

    def state(self) -> AppState:
        with self._lock:
            return self._state

    def tick(self) -> bool:
        """Drain runner events, start a refresh if none is running; True if the state changed."""
        self._drain_events()
        session = self._session
        if session is not None and session.poller is not None:
            session.poller.poll()
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

        self._run(job, "Could not create the batch", droppable=True)

    def open_batch(self, directory: Path, readonly: bool = False) -> None:
        def job() -> None:
            self._detach()
            self._attach(BatchStore.open(directory, readonly=readonly), readonly)

        self._run(job, "Could not open the batch", droppable=True)

    def close_batch(self) -> None:
        self._run(self._detach, "Could not close the batch", droppable=True)

    # -- scanning ------------------------------------------------------------------------

    def scan(self) -> None:
        self._run_writer(
            lambda runner: runner.scan_tray(), "Could not start a scan", droppable=True
        )

    def rescan(self, number: int) -> None:
        def job(runner: BatchRunner) -> None:
            self._drop_progress(number)
            runner.rescan_tray(number)

        self._run_writer(job, f"Could not re-scan tray {number}", droppable=True)

    def select_tray(self, number: int) -> None:
        self._set(_selected=number)

    # -- review --------------------------------------------------------------------------

    def set_status(self, number: int, position: int, status: SlideStatus) -> None:
        self._run_writer(
            lambda runner: runner.set_slide_status(number, position, status),
            f"Could not change slide {position} of tray {number}",
        )

    def edit_slide(self, number: int, position: int, settings: SlideSettings) -> None:
        self._run_writer(
            lambda runner: self._edit(runner, number, position, settings),
            f"Could not adjust slide {position} of tray {number}",
        )

    def adjust_slide(
        self, number: int, position: int, changes: Mapping[str, float | int | None]
    ) -> None:
        """Change only the named ``SlideSettings`` fields, merged with what is stored now."""

        def job(runner: BatchRunner) -> None:
            current = self._slide_settings(number, position)
            self._edit(runner, number, position, dataclasses.replace(current, **changes))

        self._run_writer(job, f"Could not adjust slide {position} of tray {number}")

    def rotate(self, number: int, position: int, quarter_turns: int) -> None:
        def job(runner: BatchRunner) -> None:
            current = self._slide_settings(number, position)
            turned = (current.rotation + 90 * quarter_turns) % 360
            self._edit(runner, number, position, dataclasses.replace(current, rotation=turned))

        self._run_writer(job, f"Could not rotate slide {position} of tray {number}")

    def reset_slide(self, number: int, position: int) -> None:
        self.edit_slide(number, position, SlideSettings())

    def accept_all(self) -> None:
        self._run_writer(lambda runner: runner.accept_all(), "Could not accept all slides")

    def _edit(
        self, runner: BatchRunner, number: int, position: int, settings: SlideSettings
    ) -> None:
        key = (number, position)
        with self._lock:
            self._updating = self._updating | {key}
        try:
            runner.edit_slide(number, position, settings)
        except Exception:
            with self._lock:
                self._updating = self._updating - {key}
            raise

    def _slide_settings(self, number: int, position: int) -> SlideSettings:
        session = self._session
        if session is None:
            raise BatchError("no batch is open")
        for tray in session.store.snapshot().trays:
            for slide in tray.slides:
                if (tray.number, slide.position) == (number, position):
                    return slide.settings
        raise BatchError(f"no slide {position} in tray {number}")

    # -- export and free disk space ------------------------------------------------------

    def export(self, directory: Path, fmt: Format) -> None:
        def job(_runner: BatchRunner) -> None:
            run_export(
                self._open_store(),
                directory,
                fmt,
                self._cancel_export,
                lambda view: self._set(_export=view),
            )

        if not self._closing.is_set():
            self._cancel_export.clear()  # at submit: a queued job must not undo a later cancel
        self._after_pending_commands(job, "Could not export")

    def cancel_export(self) -> None:
        self._cancel_export.set()

    def dismiss_export(self) -> None:
        self._set(_export=None)

    def request_free_space(self) -> None:
        self._after_pending_commands(
            lambda _runner: self._set(_free_space=free_space_candidates(self._open_store())),
            "Could not check disk space",
        )

    def confirm_free_space(self) -> None:
        """Remove the raw scans of exactly the trays the owner was shown, once."""

        def job(_runner: BatchRunner) -> None:
            view = self._free_space
            if view is None or not view.trays:
                raise BatchError("check which trays can be freed before removing anything")
            if view.running or view.freed is not None:
                return  # a second click: already running or done
            run_free_space(
                self._open_store(),
                self._cancel_free,
                lambda update: self._set(_free_space=update),
                tuple(number for number, _ in view.trays),
            )

        if not self._closing.is_set():
            self._cancel_free.clear()
        self._after_pending_commands(job, "Could not free disk space")

    def _after_pending_commands(self, job: Callable[[BatchRunner], None], message: str) -> None:
        """Queue a long job behind every command already submitted (an edit made a moment
        ago must be stored before an export reads the slides), then run it on the jobs worker."""
        self._run_writer(lambda _runner: self._run_writer(job, message, pool=self._jobs), message)

    def cancel_free_space(self) -> None:
        self._cancel_free.set()

    def dismiss_free_space(self) -> None:
        self._set(_free_space=None)

    def _open_store(self) -> BatchStore:
        session = self._session
        if session is None:
            raise BatchError("no batch is open")
        return session.store

    # -- closing the window --------------------------------------------------------------

    def shutdown_decision(self) -> Literal["ok", "scan_running"]:
        session = self._session
        if session is not None and session.runner is not None and session.runner.scan_pending():
            return "scan_running"
        return "ok"

    def shutdown(self) -> None:
        """Cancel an export, finish the job running now, drop queued work, release the batch."""
        self._closing.set()
        self._cancel_export.set()
        self._cancel_free.set()
        self._jobs.shutdown(wait=True, cancel_futures=True)
        self._command.shutdown(wait=True)  # queued edits are kept; queued lifecycle commands skip
        self._detach()
        self._refresher.shutdown(wait=True)

    # -- internals shared with the other controller modules ------------------------------

    def _run(
        self,
        job: Callable[[], None],
        message: str,
        pool: ThreadPoolExecutor | None = None,
        droppable: bool = False,
    ) -> None:
        """Run ``job`` on a worker; any failure becomes a banner, never an exception.

        A ``droppable`` job (scan, open, close) is skipped if the window is closing by the
        time its turn comes; edits are not, so the owner's last changes are saved.
        """

        def guarded() -> None:
            if droppable and self._closing.is_set():
                return
            try:
                job()
            except BatchLocked as exc:
                self._set(_banner=Banner("This batch is open in another window", _locked(exc)))
            except (BatchError, ManifestFormatError, OSError, ValueError, LookupError) as exc:
                self._set(_banner=Banner(f"{message}: {exc}", f"{type(exc).__name__}: {exc}"))
            except Exception as exc:  # last resort: a banner beats a silent dead worker
                log.exception("%s", message)
                self._set(_banner=Banner(message, f"{type(exc).__name__}: {exc}"))

        if self._closing.is_set():
            return
        target = pool or self._command
        try:
            future = target.submit(guarded)
        except RuntimeError:  # shut down already
            log.debug("command ignored after shutdown")
            return
        if target is self._jobs:
            with self._lock:
                self._job_futures.add(future)
            future.add_done_callback(self._job_done)

    def _job_done(self, future: Future) -> None:
        with self._jobs_idle:  # also runs for a job cancelled before it started
            self._job_futures.discard(future)
            self._jobs_idle.notify_all()

    def _run_writer(
        self,
        job: Callable[[BatchRunner], None],
        message: str,
        pool: ThreadPoolExecutor | None = None,
        droppable: bool = False,
    ) -> None:
        requested = self._session  # the batch the owner meant, not whichever is open later

        def guarded() -> None:
            if requested is None:
                raise BatchError("no batch is open")
            current = self._session
            if current is None or current.store.directory != requested.store.directory:
                return  # a different batch is open now: drop, never retarget
            if requested.runner is None or current.runner is None:
                raise BatchError("this batch is open read-only")  # asked while read-only: refuse
            job(current.runner)  # the same batch, possibly reopened meanwhile

        self._run(guarded, message, pool, droppable)

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
        poller = None
        try:
            if not readonly:
                scanner = self._factory(store.snapshot().defaults.dpi)
                poller = StatusPoller(scanner, self._clock, STATUS_TIMEOUT_S, self._scanner_problem)
                runner = BatchRunner(store, CachedStatusScanner(scanner, poller))
                runner.resume()
        except BaseException:
            if runner is not None:
                runner.stop()
            store.close()  # never leave the writer lock held after a failed open
            raise
        first = store.snapshot().trays[0].number if store.snapshot().trays else None
        with self._lock:
            self._session = _Session(store, runner, poller)
            self._selected = first
            self._banner = None
            self._updating = frozenset()
            self._progress = {}
            self._scan_started = {}
            self._enhancing = set()
            self._export = None
            self._free_space = None

    def _scanner_problem(self, message: str, detail: str) -> None:
        self._set(_banner=Banner(message, detail))

    def _stop_jobs(self) -> None:
        """Cancel a running export or free-space job and wait until every job has ended."""
        self._cancel_export.set()
        self._cancel_free.set()
        with self._jobs_idle:
            self._jobs_idle.wait_for(lambda: not self._job_futures)

    def _detach(self) -> None:
        self._stop_jobs()  # a job writes through the store, so it must finish before it closes
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            if session.poller is not None:
                session.poller.close()
            if session.runner is not None:
                session.runner.stop()
            session.store.close()
        with self._lock:
            self._selected = None
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
            self._updating = frozenset(k for k in self._updating if k[0] != event.number)

    def _on_stage(self, number: int, stage: TrayStage) -> None:
        if stage is TrayStage.SCANNING:
            self._scan_started = {**self._scan_started, number: self._clock()}
            if self._selected is None:
                self._selected = number  # show the first tray without making the owner click
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
        status = session.poller.current() if session and session.poller else ScannerStatus.UNKNOWN
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
