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
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path as Path
from typing import Literal

from opticfilm_linux.app.auto import AutoPipeline as AutoPipeline
from opticfilm_linux.app.commands import ReviewCommands
from opticfilm_linux.app.eject_watch import UNIT, EjectWatch
from opticfilm_linux.app.first_load import FirstLoadCommands
from opticfilm_linux.app.holder import WAIT_HOLDER, HolderCommands
from opticfilm_linux.app.jobs import (
    free_space_candidates as free_space_candidates,
)
from opticfilm_linux.app.jobs import (
    run_export as run_export,
)
from opticfilm_linux.app.jobs import (
    run_free_space as run_free_space,
)
from opticfilm_linux.app.lifecycle_commands import LifecycleCommands
from opticfilm_linux.app.lifecycle_commands import (
    _check_export_directory as _check_export_directory,
)
from opticfilm_linux.app.load_and_scan import LOAD_READY_TIMEOUT_S, LoadAndScanCommands
from opticfilm_linux.app.messages import failure_text
from opticfilm_linux.app.preview_tokens import PreviewTokens
from opticfilm_linux.app.recovery_checks import RecoveryChecks
from opticfilm_linux.app.sandbox import host_terminal_hint
from opticfilm_linux.app.scan_openings import ScanOpeningsCommands
from opticfilm_linux.app.selection import Selection, prune, selectable_keys
from opticfilm_linux.app.selection_commands import SelectionCommands
from opticfilm_linux.app.state import (
    EMPTY_STATE,
    AppState,
    Banner,
    ExportView,
    FreeSpaceView,
    build_state,
)
from opticfilm_linux.app.status import CachedStatusScanner, StatusPoller
from opticfilm_linux.app.workflow_settings import WorkflowSettings
from opticfilm_linux.batch.events import ScanFinished, SlideUpdated, TrayFailed, TrayStageChanged
from opticfilm_linux.batch.manifest import ManifestFormatError
from opticfilm_linux.batch.model import (
    TrayStage,
)
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchLocked, BatchStore
from opticfilm_linux.scanner.base import BUTTON_STATUSES, HolderControl, Scanner, ScannerStatus
from opticfilm_linux.scanner.holder_latch import HolderLatch
from opticfilm_linux.scanner.select import auto_eject_enabled, next_strip_enabled
from opticfilm_linux.worker import lower_worker_priority

SLIDES_PER_TRAY = 4
STATUS_TIMEOUT_S = 5.0  # a scanner that has not answered for this long is shown as an error
LOCKED_PREFIX = "LOCKED:"
# How long closing a batch waits for a holder move or status request to end before it will
# start the Eject-button watcher again (a second program on the scanner mid-move is unsafe).
WATCH_RESTORE_WAIT_S = 120.0

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class _Session:
    """What is open right now. Replaced as a whole when a batch is opened or closed."""

    store: BatchStore
    runner: BatchRunner | None  # None for a read-only batch
    poller: StatusPoller | None
    holder: HolderControl | None = None  # None: the scanner cannot move the holder
    scanner: Scanner | None = None  # identity may become known only after the first status poll
    auto_export: bool = False  # batch setting is immutable throughout this session
    previews: PreviewTokens = dataclasses.field(default_factory=PreviewTokens)


class Controller(
    FirstLoadCommands,
    HolderCommands,
    LifecycleCommands,
    LoadAndScanCommands,
    ReviewCommands,
    ScanOpeningsCommands,
    SelectionCommands,
    WorkflowSettings,
):
    def __init__(
        self,
        scanner_factory: Callable[[int], Scanner],
        clock: Callable[[], float] = time.monotonic,
        next_strip: bool | None = None,
        eject_button: bool = True,
        eject_watch: EjectWatch | None = None,
        skip_empty: bool | None = None,  # None: read OPTICFILM_SKIP_EMPTY
        skip_gaps: bool | None = None,  # None: read OPTICFILM_SKIP_GAPS
        auto_eject: bool | None = None,  # None: read OPTICFILM_AUTO_EJECT
        env_locked: frozenset[str] = frozenset(),  # switches an environment variable decides
        load_and_scan: bool = False,  # Load & scan stays off until the hardware check passes
        load_ready_timeout_s: float = LOAD_READY_TIMEOUT_S,
        load_clock: Callable[[], float] = time.monotonic,  # the READY wait's own clock
    ) -> None:
        self._factory = scanner_factory
        self._watch_poller: StatusPoller | None = None  # the last session's, for the idle check
        self._eject_watch = eject_watch  # the background watcher to step aside; None: no such unit
        self._next_strip = next_strip_enabled() if next_strip is None else next_strip
        self._eject_button = eject_button  # the front-panel Eject button works (kill switch)
        self._auto_eject = auto_eject_enabled() if auto_eject is None else auto_eject
        self._init_scan_openings(skip_empty, skip_gaps)
        self._init_workflow_settings(env_locked)
        self._init_holder_workers()
        self._init_load_and_scan(load_and_scan, load_ready_timeout_s, load_clock)
        self._scans_queued = 0  # scan commands submitted but not yet handed to the runner
        self._long_jobs = 0  # exports and free-space jobs on their way to the jobs worker
        self._clock = clock
        self._lock = threading.Lock()
        self._command = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app-command")
        self._refresher = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app-status")
        self._refreshing = False
        self._recovery_checks = RecoveryChecks()
        self._session: _Session | None = None
        self._batch_session = 0
        self._closing = threading.Event()
        self._initialize_jobs()
        self._state = EMPTY_STATE
        self._reported = EMPTY_STATE

    def _initialize_jobs(self) -> None:
        self._job_futures: set[Future] = set()
        self._jobs_idle = threading.Condition(self._lock)
        self._selected: int | None = None
        self._selection = Selection()
        self._selection_order = ()
        self._manifest = None  # last manifest a refresh read; selection gestures patch from it
        self._updating: frozenset[tuple[int, int]] = frozenset()
        self._progress: dict[int, float] = {}
        self._scan_started: dict[int, float] = {}
        self._enhancing: set[int] = set()
        self._banner: Banner | None = None
        self._export: ExportView | None = None
        self._free_space: FreeSpaceView | None = None
        self._cancel_export = threading.Event()
        self._cancel_free = threading.Event()
        self._cancel_auto = threading.Event()
        self._auto_due: set[int] = set()
        self._auto_active: set[int] = set()
        self._auto_retried: set[int] = set()
        self._jobs = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="app-jobs", initializer=lower_worker_priority
        )

    def _init_holder_workers(self) -> None:
        self._init_first_load()
        self._holder_busy = False  # an eject or load is queued or running
        self._lifecycle_pending = 0  # accepted batch changes exclude new holder movements
        self._holder_latch = HolderLatch.UNKNOWN
        self._latch_address: tuple[int, int] | None = None
        self._latch_invalidated = False
        self._latch_sequence = 0
        self._latch_applied = 0
        self._latch_loss = None
        self._latch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app-latch")
        self._holder_future: Future | None = None  # that eject or load, while it may not start
        self._holder_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app-holder")

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
            self._act_on_eject_button(session.poller)
        self._kick_refresh()
        with self._lock:
            changed = self._state is not self._reported
            self._reported = self._state
        return changed

    def _kick_refresh(self) -> None:
        with self._lock:
            start = not self._refreshing
            self._refreshing = True
        if start:
            self._refresher.submit(self._refresh)

    def _act_on_eject_button(self, poller: StatusPoller) -> None:
        # A press is always taken and handled now, never queued behind a scan.
        if not poller.take_eject_press() or not self._eject_button:
            return
        with self._lock:
            refusal = self._holder_refusal() or (WAIT_HOLDER if self._load_scan_moving() else None)
        if refusal is not None:
            self._set(_banner=Banner(refusal))
            return
        # ``state()`` is a poll behind: also ask the poller's own answer, so a scanner that has just
        # gone to an error or interrupted state is not moved.
        if self.state().can_eject and poller.current() in BUTTON_STATUSES:
            self.eject_holder()

    def preview_path(self, number: int, position: int, kind: Literal["before", "after"]):
        session = self._session
        if session is None:
            return None
        path = session.store.preview_path(number, position, kind)
        return path if path.is_file() else None

    def dismiss_banner(self) -> None:
        self._set(_banner=None)

    # -- scanning ------------------------------------------------------------------------

    # -- closing the window --------------------------------------------------------------

    def shutdown_decision(self) -> Literal["ok", "scan_running", "first_load_running"]:
        with self._lock:
            if self._holder_busy and self._load_phase in ("jogging", "await_reinsert", "loading"):
                return "first_load_running"
            if self._load_scan_moving():
                return "scan_running"  # Load & scan is the start of a scan
            session = self._session
        if session is not None and session.runner is not None and session.runner.scan_pending():
            return "scan_running"
        return "ok"

    def shutdown(self) -> None:
        """Cancel an export, finish the job running now, drop queued work, release the batch."""
        self._closing.set()
        self._load_cancel.set()
        self._cancel_export.set()
        self._cancel_free.set()
        self._cancel_auto.set()
        self._holder_pool.shutdown(wait=True, cancel_futures=True)  # a running move completes
        self._jobs.shutdown(wait=True, cancel_futures=True)
        self._command.shutdown(wait=True)  # queued edits are kept; queued lifecycle commands skip
        self._detach()
        self._refresher.shutdown(wait=True)
        self._recovery_checks.close()
        self._latch_pool.shutdown(wait=True)

    # -- internals shared with the other controller modules ------------------------------

    def _run(
        self,
        job: Callable[[], None],
        message: str,
        pool: ThreadPoolExecutor | None = None,
        droppable: bool = False,
    ) -> Future | None:
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
            return None
        target = pool or self._command
        try:
            future = target.submit(guarded)
        except RuntimeError:  # shut down already
            log.debug("command ignored after shutdown")
            return None
        if target is self._jobs:
            with self._lock:
                self._job_futures.add(future)
            future.add_done_callback(self._job_done)
        return future

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
    ) -> Future | None:
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

        return self._run(guarded, message, pool, droppable)

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
        holder = None
        scanner = None
        try:
            if not readonly:
                if self._eject_watch is not None:
                    self._eject_watch.yield_to_app()  # raises if it stays up: no scanner open
                scanner = self._factory(store.snapshot().defaults.dpi)
                poller = StatusPoller(
                    scanner,
                    self._clock,
                    STATUS_TIMEOUT_S,
                    self._scanner_problem,
                    begin_operation=self._begin_latch_operation,
                )
                runner = BatchRunner(store, CachedStatusScanner(scanner, poller))
                runner.resume()
                holder = scanner if isinstance(scanner, HolderControl) else None
        except BaseException:
            self._remember_holder_latch(HolderLatch.UNKNOWN, scanner)
            if runner is not None:
                runner.stop()
            store.close()  # never leave the writer lock held after a failed open
            self._restore_eject_watch()
            raise
        self._attach_session(
            _Session(store, runner, poller, holder, scanner, store.snapshot().defaults.auto_export)
        )
        if not readonly:
            due = self._auto_pipeline(store).resume()
            with self._lock:
                self._auto_due.update(due)
            self._submit_auto_due()
        if readonly:
            self._restore_eject_watch()  # no scanner is opened for a read-only batch

    def _attach_session(self, session: _Session) -> None:
        manifest = session.store.snapshot()
        trays = manifest.trays
        order = self._sheet_selection_order(manifest)
        with self._lock:
            self._next_latch_sequence()
            self._latch_applied = self._latch_sequence
            self._session = session
            self._batch_session += 1
            self._forget_scan_openings()
            self._reset_load_phase()
            self._reset_load_scan()
            self._holder_latch = HolderLatch.UNKNOWN
            self._latch_address = None
            self._latch_loss = None
            self._latch_invalidated = True
            self._sync_holder_latch(session.scanner)
            self._selected = trays[0].number if trays else None
            self._selection = Selection()
            self._selection_order = order
            self._manifest = manifest
            self._banner = None
            self._updating = frozenset()
            self._progress = {}
            self._scan_started = {}
            self._enhancing = set()
            self._export = None
            self._free_space = None
            self._auto_due = set()
            self._auto_active = set()
            self._auto_retried = set()
            self._cancel_auto = threading.Event()

    def _scanner_problem(self, message: str, detail: str) -> None:
        self._set(_banner=Banner(message, detail))

    def _stop_jobs(self) -> None:
        """Cancel a running export or free-space job and wait until every job has ended."""
        self._cancel_export.set()
        self._cancel_free.set()
        self._cancel_auto.set()
        with self._jobs_idle:
            self._jobs_idle.wait_for(lambda: not self._job_futures)

    def _scanner_idle(self) -> bool:
        """No holder move and no status request is out right now (asked fresh every time)."""
        poller = self._watch_poller
        return not self._holder_busy and (poller is None or poller.wait_idle(0.0))

    def _wait_scanner_idle(self) -> None:
        """Wait, bounded, for the scanner to go idle; returns early when the window is closing."""
        deadline = time.monotonic() + WATCH_RESTORE_WAIT_S
        while not self._scanner_idle() and not self._closing.is_set():
            if time.monotonic() >= deadline:
                return
            time.sleep(0.05)

    def _restore_eject_watch(self) -> None:
        """Start the background watcher again if it was running; a failure is a banner only.

        Never while the scanner is busy: a second program on it mid-move is unsafe."""
        if self._eject_watch is None:
            return
        if not self._scanner_idle():
            text = (
                "The Eject button watcher was left stopped: the scanner was still busy. "
                f"Start it with `systemctl --user start {UNIT}`{host_terminal_hint()} "
                "when it is idle."
            )
            log.warning(text)
            if not self._closing.is_set():
                with self._lock:
                    self._banner = Banner(text)
            return
        problem = self._eject_watch.restore()
        if problem is not None:
            log.warning(problem)
            with self._lock:
                self._banner = Banner(problem)

    def _banner_only(self) -> AppState:
        return dataclasses.replace(EMPTY_STATE, banner=self._banner)

    # -- events and refresh --------------------------------------------------------------

    def _drain_events(self, session: _Session | None = None) -> None:
        while not self._load_events.empty():
            event = self._load_events.get_nowait()
            with self._lock:
                self._apply_load_event(event)
        session = session or self._session
        if session is None or session.runner is None:
            return
        events, finished = session.runner.events, []
        while not events.empty():
            event = events.get_nowait()
            if isinstance(event, ScanFinished):
                finished.append(event)
            if isinstance(event, TrayFailed):
                self._remember_holder_latch(
                    HolderLatch.UNKNOWN, session.scanner, sequence=event.operation_sequence
                )
            with self._lock:
                self._apply_event(event)
        if session is self._session:  # never for a session being closed
            self._submit_auto_due()
            for event in finished:
                self._auto_eject_after(event)

    def _apply_event(self, event) -> None:
        if isinstance(event, TrayStageChanged):
            self._on_stage(event.number, event.stage)
        elif isinstance(event, SlideUpdated):
            if self._session is not None:
                self._session.previews.invalidate(event.number, event.position)
            self._auto_due.add(event.number)
            self._updating = self._updating - {(event.number, event.position)}
            if event.number in self._enhancing:
                self._progress = {
                    **self._progress,
                    event.number: min(event.position / SLIDES_PER_TRAY, 1.0),
                }
        elif isinstance(event, TrayFailed):
            fallback = f"Tray {event.number} failed"
            self._banner = Banner(failure_text(event.message, fallback), event.message)
            self._updating = frozenset(k for k in self._updating if k[0] != event.number)

    def _on_stage(self, number: int, stage: TrayStage) -> None:
        if stage is TrayStage.REVIEW:
            self._auto_due.add(number)
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
        status, sequence = self._poll_status(session)
        manifest = session.store.snapshot() if session is not None else None
        with self._lock:
            selected = self._selected
        tokens = self._preview_tokens(session, manifest)
        marker = self._load_scan_marker()  # storage: read outside the lock
        raw_pending = self._raw_pending(session, manifest)
        raw_recoverable = self._recovery_checks.poll(
            session, manifest, raw_pending, self._raw_recoverable
        )
        with self._lock:
            if session is not self._session or selected != self._selected:
                return  # the batch or tray changed while we were polling; the next refresh will do
            self._prune_selection(manifest)
            self._update_holder_status(session, status, sequence)
            self._publish_state(
                session=session,
                manifest=manifest,
                selected=selected,
                status=status,
                tokens=tokens,
                raw_pending=raw_pending,
                raw_recoverable=raw_recoverable,
            )
            self._state = dataclasses.replace(
                self._state, **self._load_scan_fields(session, status, sequence, marker)
            )

    def _publish_state(
        self, *, session, manifest, selected, status, tokens, raw_pending, raw_recoverable
    ):
        """Build an immutable snapshot while holding the lock; no storage access."""
        self._state = self._with_first_load(
            build_state(
                manifest,
                preview_tokens=tokens,
                scanner=status,
                scan_pending=bool(
                    self._scans_queued
                    or self._load_scan_moving()  # Load & scan is the start of a scan
                    or (session and session.runner and session.runner.scan_pending())
                ),
                readonly=bool(session and session.runner is None),
                selected=selected,
                selection=self._selection,
                updating=self._updating,
                progress=self._progress,
                scan_started=self._scan_started,
                now=self._clock(),
                banner=self._banner,
                export=self._export,
                free_space=self._free_space,
                holder_control=session is not None and session.holder is not None,
                holder_busy=self._holder_busy,
                next_strip=self._next_strip,
                jobs_running=bool(self._long_jobs or self._job_futures),
                batch_session=self._batch_session if session else 0,
                runner_busy=bool(
                    self._lifecycle_pending
                    or (session and session.runner and session.runner.work_pending())
                ),
                holder_latch=self._holder_latch if session else HolderLatch.UNKNOWN,
                raw_pending=raw_pending,
                raw_recoverable=raw_recoverable,
            )
        )
        self._state = dataclasses.replace(
            self._state,
            undo_label=self._selection_undo_label(),
            scan_openings=self._scan_openings,
        )

    @staticmethod
    def _poll_status(session):
        return (
            session.poller.snapshot() if session and session.poller else (ScannerStatus.UNKNOWN, 0)
        )

    def _prune_selection(self, manifest):
        self._manifest = manifest
        self._selection_order = self._sheet_selection_order(manifest)
        self._selection = prune(self._selection, self._selection_order)
        available = {tray.number for tray in manifest.trays} if manifest else set()
        self._selection = dataclasses.replace(
            self._selection, trays=self._selection.trays & available
        )

    @staticmethod
    def _sheet_selection_order(manifest):
        keys = selectable_keys(manifest) if manifest else ()
        return tuple(sorted(keys, key=lambda key: (-key[0], key[1])))

    @staticmethod
    def _raw_recoverable(session, manifest, pending) -> frozenset[int]:
        from opticfilm_linux.batch.cleanup_recovery import can_recover_interrupted_cleanup

        if session is None or manifest is None:
            return frozenset()
        return frozenset(
            tray.number
            for tray in manifest.trays
            if tray.number in pending
            and can_recover_interrupted_cleanup(session.store, manifest, tray)
        )

    @staticmethod
    def _raw_pending(session, manifest) -> frozenset[int]:
        if session is None or manifest is None:
            return frozenset()
        return frozenset(
            t.number
            for t in manifest.trays
            if t.raw_removed and any(session.store.tray_dir(t.number).glob("sweep-*"))
        )

    def _update_holder_status(self, session, status: ScannerStatus, sequence: int) -> None:
        """Apply the scanner status while the controller lock is held."""
        if session is not None:
            self._sync_holder_latch(session.scanner)
        if (
            status in (ScannerStatus.INTERRUPTED, ScannerStatus.ERROR, ScannerStatus.NOT_CONNECTED)
            and sequence >= self._latch_applied
            and (self._holder_latch is not HolderLatch.UNKNOWN or not self._latch_invalidated)
        ):
            self._latch_applied = sequence
            self._holder_latch = HolderLatch.UNKNOWN
            self._latch_invalidated = True
            self._save_holder_latch()

    @staticmethod
    def _preview_tokens(session, manifest) -> dict[tuple[int, int], int]:
        if session is None or manifest is None:
            return {}
        cache = getattr(session, "previews", None) or PreviewTokens()
        return cache.read(session.store, manifest)


def _skipped_text(numbers: tuple[int, ...]) -> str:
    names = ", ".join(f"tray {n}" for n in numbers)
    return (
        f"The batch orientation was changed, but {names} kept the old one: "
        "its raw scans were removed, so it cannot be redone."
    )


def _locked(exc: Exception) -> str:
    return f"{LOCKED_PREFIX} {exc}"
