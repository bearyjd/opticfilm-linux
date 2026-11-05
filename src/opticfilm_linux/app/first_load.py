"""Interactive first load, on the holder worker and the controller's event lock."""

import queue
import re
import threading
from dataclasses import dataclass, replace

from opticfilm_linux.app.holder import ANOTHER_PROGRAM, SCANNER_INTERRUPTED
from opticfilm_linux.app.state import Banner, LoadPhase
from opticfilm_linux.scanner import holder_latch
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.driver import ScannerInUse
from opticfilm_linux.scanner.interrupted import ScannerInterrupted, ScannerRefused

CANCELLED = "First load stopped. The scanner needs a power cycle before the next load."


@dataclass(frozen=True)
class LoadChanged:
    token: int
    phase: LoadPhase
    exit_code: int | None = None
    banner: Banner | None = None


class FirstLoadCommands:
    """Mixed into Controller; phase events are consumed with its lock held."""

    def _init_first_load(self) -> None:
        self._load_phase: LoadPhase = "idle"
        self._load_exit_code: int | None = None
        self._load_token = 0
        self._load_done, self._load_cancel = threading.Event(), threading.Event()
        self._load_events: queue.SimpleQueue[LoadChanged] = queue.SimpleQueue()

    def _reset_load_phase(self) -> None:
        """Called under the lock on attach/detach; discard old session events."""
        self._load_token += 1
        self._load_phase, self._load_exit_code = "idle", None

    def first_load(self) -> None:
        with self._lock:
            session = self._session
        status = session.poller.current() if session and session.poller else ScannerStatus.UNKNOWN
        with self._lock:
            if session is not self._session:
                return
            load = getattr(session.scanner, "load_interactive", None) if session else None
            refusal = (
                SCANNER_INTERRUPTED
                if status is ScannerStatus.INTERRUPTED
                else self._holder_refusal()
            )
            if self._holder_busy:
                return
            if refusal is None and (not session or not session.runner or not callable(load)):
                refusal = (
                    "This scanner cannot load the first holder in the app. Use the terminal help."
                )
            if refusal is None and status is not ScannerStatus.NOT_READY:
                refusal = "First load is available when the scanner is not loaded."
            if refusal:
                self._banner = Banner(refusal)
                return
            self._holder_busy = True
            self._load_phase, self._load_exit_code = "jogging", None
            self._load_token += 1
            token = self._load_token
            done, cancel = threading.Event(), threading.Event()
            self._load_done, self._load_cancel = done, cancel
            self._banner = None

        def job(_runner):
            self._first_load_job(session, load, token, done, cancel)

        future = self._run_writer(job, "Could not load the first holder", self._holder_pool)
        self._track(future, lambda: self._set(_holder_busy=False, _holder_future=None))
        with self._lock:
            if future is not None and not future.done():
                self._holder_future = future

    def first_load_done(self) -> None:
        with self._lock:
            if self._holder_busy and self._load_phase == "await_reinsert":
                self._load_done.set()
                self._load_phase = "loading"

    def first_load_cancel(self) -> None:
        with self._lock:
            if self._holder_busy and self._load_phase in ("jogging", "await_reinsert", "loading"):
                self._load_cancel.set()

    def _first_load_job(self, session, load, token, done, cancel) -> None:
        with self._lock:
            self._forget_scan_openings()  # a holder is being loaded
            sequence = self._next_latch_sequence()

        def prompt():
            self._load_events.put(LoadChanged(token, "await_reinsert"))

        try:
            code = load(prompt, done, cancel)
            self._remember_holder_latch(holder_latch.after_load(True), sequence=sequence)
            self._load_events.put(LoadChanged(token, "done", code))
        except (ScannerInUse, ScannerRefused) as exc:
            if isinstance(exc, ScannerInterrupted):
                self._remember_holder_latch(holder_latch.invalidate(), sequence=sequence)
            text = ANOTHER_PROGRAM if isinstance(exc, ScannerInUse) else str(exc)
            self._load_events.put(LoadChanged(token, "failed", banner=Banner(text, str(exc))))
        except Exception as exc:
            self._remember_holder_latch(holder_latch.invalidate(), sequence=sequence)
            match = re.search(r"exit code (-?\d+)", str(exc))
            code = int(match.group(1)) if match else None
            text = CANCELLED if cancel.is_set() else "First load failed. Power cycle the scanner."
            self._load_events.put(LoadChanged(token, "failed", code, Banner(text, str(exc))))
        finally:
            if session.poller is not None:
                session.poller.poll()

    def _apply_load_event(self, event: LoadChanged) -> None:
        """Called under the controller lock: never call _set or submit jobs here."""
        if event.token != self._load_token:
            return
        self._load_phase = event.phase
        self._load_exit_code = event.exit_code
        if event.banner is not None:
            self._banner = event.banner

    def _with_first_load(self, state):
        """Add first-load fields to a snapshot while the controller lock is held."""
        session = self._session
        supported = bool(session and callable(getattr(session.scanner, "load_interactive", None)))
        can_load = bool(
            session
            and session.holder
            and supported
            and not (state.readonly or state.scan_pending or state.holder_busy)
            and state.scanner is ScannerStatus.NOT_READY
        )
        return replace(
            state,
            can_first_load=can_load,
            load_phase=self._load_phase,
            load_exit_code=self._load_exit_code,
        )
