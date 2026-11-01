"""The real scanner, through the community driver's command line (``python -m of135i``).

The OpticFilm 135i scans a mounted-slide holder one opening at a time. ``scan --slides 1-4`` (in
the patched driver; see its docs/slide-scanning.md) calibrates once, then writes one full-window
frame per opening, with the slide's long side along the transport. This backend runs that command,
turns each frame a quarter turn, and stacks the four into one sweep, so the splitting,
enhancement and batch code work unchanged. It sends nothing to the scanner itself: every hardware
command is the driver's.

The driver must already have *loaded* the holder (an interactive step: ``of135i load``).
``status()`` reads the driver's read-only ``doctor`` report to tell whether that happened.
"""

import atexit
import dataclasses
import fcntl
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO

import numpy as np
import tifffile

from opticfilm_linux.model import HolderKind, RawSweep, ScanSettings
from opticfilm_linux.scanner import interrupted
from opticfilm_linux.scanner.base import (
    BUTTON_STATUSES,
    ScanError,
    ScannerStatus,
    StatusReading,
)
from opticfilm_linux.scanner.interrupted import ScanInterrupted, ScannerRefused

log = logging.getLogger(__name__)

SLIDES = "1-4"  # the four openings of the mounted-slide holder
DARK_FALLBACK = "default"  # use the vendor offset codes if the dark reading is a stale buffer
FRAME_QUARTER_TURNS = 1  # counter-clockwise; turns a frame so the slide's long side lies along x
DRIVER_ENV = "OPTICFILM_DRIVER_DIR"
SCAN_TIMEOUT_S = 3600
STATUS_TIMEOUT_S = 20
STATUS_MAX_AGE_S = 3.0  # the status poll must stay cheap: each answer costs a process start
LOG_TAIL_CHARS = 1500
OUTPUT_TAIL_BYTES = 64 * 1024  # all that is kept in memory of a driver run; the rest is in the log
KEEP_LOGS = 30
PROTECT_LOG_S = 3600  # a log this recent may belong to a scan still running: never pruned
DOCTOR_TAIL_BYTES = 1 << 20
HEAD_BYTES = 8192  # the driver prints the USB address it opened on its first lines
LOCK_POLL_S = 0.2
LOCK_WAIT_S = STATUS_TIMEOUT_S + 2  # outlasts one status check by the app, never a scan
STOP_GRACE_S = 10.0  # how long a driver that was interrupted gets before it is killed
PROBE_TIMEOUT_S = 10
SPACE_MARGIN = 1.25  # the sweep needs this much free disk, beside the driver's own frames
IDLE_HOMED = 0x22
LOADED_STATUS_HIGH_BYTE = 0xDC  # status word 0xdc55 after a completed load (hardware-observed)
POWER_CYCLE_HINT = (
    "The scanner refused or lost the scan. Power-cycle it (off until the lights go out, then on), "
    "eject, load the holder again and retry."
)
TERMINAL_LOAD_HINT = "The first load after power-on must be done in a terminal (of135i load)."
HOLDER_TIMEOUT_S = STATUS_TIMEOUT_S * 6
MARKED_COMMANDS = frozenset({"scan", "eject", "load"})  # they move the scanner: see interrupted
SIGNAL_EXIT_BASE = 128  # exit status 128 + n: the program ended because of signal n


@dataclasses.dataclass(frozen=True)
class DriverConfig:
    driver_dir: Path | None = None  # a checkout holding the ``of135i`` package; None: importable
    python: str = sys.executable
    log_dir: Path | None = None
    work_dir: Path | None = None  # scratch for the scan; a disk, never a RAM-backed /tmp
    timeout_s: int = SCAN_TIMEOUT_S

    @classmethod
    def from_env(cls) -> "DriverConfig":
        directory = os.environ.get(DRIVER_ENV)
        state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        return cls(
            driver_dir=Path(directory) if directory else None,
            log_dir=state / "opticfilm-linux" / "logs",
            work_dir=cache / "opticfilm-linux" / "work",
        )

    def available(self) -> bool:
        if self.driver_dir is not None:
            return (self.driver_dir / "of135i").is_dir()
        try:
            probe = subprocess.run(
                [self.python, "-c", "import of135i"],
                capture_output=True,
                check=False,
                timeout=PROBE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return False
        return probe.returncode == 0

    def scratch_dir(self) -> Path:
        """Where scans are assembled: configured, else the user's cache (a disk, not /tmp)."""
        if self.work_dir is not None:
            return self.work_dir
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        return cache / "opticfilm-linux" / "work"


def parse_doctor(text: str) -> ScannerStatus:
    """Map the driver's read-only ``doctor`` report to a scanner status.

    Ready means: homed and idle (reg 0x01 = 0x22), the holder present, AND the loaded status
    class (high byte 0xdc, the word the vendor's load ends on). Homed and idle with the holder
    merely present is also what the scanner reports right after an eject, so it is not enough.
    """
    state = re.search(r"reg 0x01 = 0x([0-9a-fA-F]{2})", text)
    if state is None:
        return ScannerStatus.NOT_CONNECTED
    code = int(state.group(1), 16)
    if code == 0x00:  # freshly powered: the holder has not been loaded yet
        return ScannerStatus.NOT_READY
    if code != IDLE_HOMED:
        return ScannerStatus.ERROR  # an engine state the driver refuses to start from
    present = re.search(r"present:\s*(True|False)", text)
    word = re.search(r"status word:\s*0x([0-9a-fA-F]{4})", text)
    if not (present and present.group(1) == "True" and word):
        return ScannerStatus.NOT_READY
    loaded = int(word.group(1), 16) >> 8 == LOADED_STATUS_HIGH_BYTE
    return ScannerStatus.READY if loaded else ScannerStatus.NOT_READY


def parse_doctor_button(text: str) -> bool:
    """True when the ``== Button ==`` section of the ``doctor`` report says ``eject``.

    A missing or garbled section, and every other button name, read as not pressed.
    """
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("== ") and stripped.endswith(" =="):
            in_section = stripped == "== Button =="
        elif in_section and stripped == "eject":
            return True
    return False


# One lock for the whole process: every batch gets its own DriverScanner, but the driver allows
# only one process on the scanner at a time, so a move still running for a closed batch must
# also hold off the next batch's scan and status checks.


class ScannerInUse(ScanError):
    """Another program holds the scanner. The driver was never started, so nothing failed."""


def _lock_path() -> Path:
    return interrupted.state_dir() / "scanner.lock"


class _HardwareLock:
    """One program, and within it one thread, on the scanner at a time.

    The thread lock serialises this process; an advisory file lock keeps a second ``ofscan``
    or the app out. A blocking ``acquire`` refuses at once rather than queue behind another
    program: an eject that ran after someone else's scan finished would surprise them.
    ``acquire(blocking=False)`` just says no. If the lock file cannot be used the process
    still has its own lock, and a warning says the other programs are not excluded; scans and
    holder moves then refuse to start (``holds_file``), status checks still run. The driver
    inherits the locked file, so a driver orphaned by a killed program keeps the scanner locked.
    """

    def __init__(self) -> None:
        self._thread = threading.Lock()
        self._handle: IO[str] | None = None
        self._warned = False

    def acquire(self, blocking: bool = True) -> bool:
        if not self._thread.acquire(blocking):
            return False
        try:
            deadline = time.monotonic() + (LOCK_WAIT_S if blocking else 0.0)
            while not self._take_file():
                if time.monotonic() >= deadline:
                    break
                time.sleep(LOCK_POLL_S)  # the other program may only be checking the status
            else:
                return True
        except BaseException:
            self._thread.release()
            raise
        self._thread.release()
        if blocking:
            raise ScannerInUse(
                "another program is using the scanner; wait for it to finish "
                "or close it, then try again"
            )
        return False

    def _take_file(self) -> bool:
        """True once this process owns the file lock; False when another program does."""
        path = _lock_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path, "w")  # noqa: SIM115 - held until release()
        except OSError:
            self._warn_unlocked(path)
            return True
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        except OSError:
            handle.close()
            self._warn_unlocked(path)
            return True
        self._handle = handle
        return True

    def _warn_unlocked(self, path: Path) -> None:
        if not self._warned:  # status polls come every few seconds: say it once
            self._warned = True
            log.warning("cannot lock %s: other programs are not kept off the scanner", path)

    @property
    def holds_file(self) -> bool:
        """True while this process owns the file lock (not merely its own thread lock)."""
        return self._handle is not None

    def file_descriptor(self) -> int | None:
        """The locked file, for the driver to inherit; None without a file lock."""
        return self._handle.fileno() if self._handle is not None else None

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()  # closing drops the flock
        self._thread.release()

    def __enter__(self) -> "_HardwareLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


_HW_LOCK = _HardwareLock()
_LIVE: set[subprocess.Popen] = set()
_SIGNALLED: set[subprocess.Popen] = set()  # drivers this program told to stop: cut off
_LIVE_LOCK = threading.RLock()  # re-entrant: the SIGTERM handler may interrupt a holder


def _stop_group(proc: subprocess.Popen) -> None:
    """Stop the driver and any helper it started: interrupt first (the driver handles Ctrl-C and
    fails closed), kill after a grace period, then sweep up stragglers in the process group.
    A further Ctrl-C while waiting is ignored: the driver is given its time to fail closed."""
    deadline = time.monotonic() + STOP_GRACE_S
    with _LIVE_LOCK:
        _SIGNALLED.add(proc)  # before the signal: a driver that then exits was still cut off
    try:
        os.killpg(proc.pid, signal.SIGINT)
        while True:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
                break
            except KeyboardInterrupt:
                continue
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                break
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # a helper that outlived the driver itself
    except (ProcessLookupError, PermissionError):
        pass


@atexit.register
def _stop_all_drivers() -> None:
    with _LIVE_LOCK:
        running = list(_LIVE)
    for proc in running:
        _stop_group(proc)


def _register(text: str) -> int | None:
    match = re.search(r"reg 0x01 = 0x([0-9a-fA-F]{2})", text)
    return int(match.group(1), 16) if match else None


def _read_tail(path: Path, limit: int = OUTPUT_TAIL_BYTES) -> str:
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - limit))
        return handle.read().decode("utf-8", errors="replace")


def _prune_logs(directory: Path) -> None:
    """Keep the newest logs; older ones go, but nothing recent (it may still be written to)."""
    now = time.time()
    logs = sorted(directory.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in logs[KEEP_LOGS:]:
        if now - old.stat().st_mtime > PROTECT_LOG_S:
            old.unlink(missing_ok=True)
    if len(logs) <= KEEP_LOGS:
        return
    cutoff = logs[KEEP_LOGS - 1].stat().st_mtime - PROTECT_LOG_S
    for sidecar in [*directory.glob("*.diag.json"), *directory.glob("*.cal.npz")]:
        if sidecar.stat().st_mtime < cutoff:
            sidecar.unlink(missing_ok=True)


def _address_in(path: Path) -> str | None:
    """The USB address the driver opened: it changes when the scanner is power-cycled."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(HEAD_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"addr=(\d+)", head)
    return match.group(1) if match else None


def confirm_scanner_reset() -> bool:
    """The owner says the scanner was power-cycled and the holder loaded again: allow commands.

    Never runs the driver. Refused while any program or thread holds the scanner, so the mark
    of a command still running is never removed. Returns whether there was a mark to clear.
    """
    if not _HW_LOCK.acquire(blocking=False):
        raise ScannerInUse(
            "another program is using the scanner; wait for it to finish, then confirm again"
        )
    try:
        if not _HW_LOCK.holds_file:  # another program could be mid-command: never clear
            raise ScannerRefused(
                f"cannot lock {_lock_path()}, so another program may be using the scanner; "
                "confirm refused"
            )
        group = interrupted.driver_group()
        if group is not None and _group_alive(group):
            raise ScannerInUse(
                f"the driver of the cut-off command is still running (process group {group}); "
                "wait for it to end or stop it, then confirm again"
            )
        pending = interrupted.is_pending()
        interrupted.clear()
        return pending
    finally:
        _HW_LOCK.release()


def _group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)  # signal 0: only asks whether the group exists
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _note_driver(command: str, group: int) -> None:
    """Record the driver's process group in the mark (it runs in its own session, so it can
    outlive this program). If that fails the mark stays as it was: still a refusal."""
    try:
        interrupted.note_driver(command, group)
    except OSError:
        log.warning("could not record driver group %s in %s", group, interrupted.marker_path())


def _ended_by_itself() -> None:
    """The driver ended on its own: its state is known (a failure it reports keeps the power-cycle
    check). If the mark cannot be removed it stays: refusing too much is the safe side."""
    try:
        interrupted.clear()
    except OSError:
        log.warning("could not clear %s; confirm the scanner reset", interrupted.marker_path())


def install_signal_cleanup() -> "Callable[[], None]":
    """On SIGTERM / SIGHUP stop any running driver first (it runs in its own session, so it
    would otherwise outlive the app and keep the scanner), then die of the signal as usual.
    Returns a function that restores the previous handlers. Main thread only."""
    previous: dict[int, object] = {}

    def handler(signum: int, _frame: object) -> None:
        _stop_all_drivers()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for number in (signal.SIGTERM, signal.SIGHUP):
        try:
            previous[number] = signal.signal(number, handler)
        except ValueError:  # not the main thread
            continue

    def restore() -> None:
        for number, old in previous.items():
            signal.signal(number, old)  # type: ignore[arg-type]

    return restore


class DriverScanner:
    def __init__(self, config: DriverConfig | None = None) -> None:
        self._config = config or DriverConfig.from_env()
        self._scanning = threading.Event()
        self._status_lock = threading.Lock()
        self._hw_lock = _HW_LOCK
        self._status_cache: tuple[float, StatusReading] | None = None
        self._needs_power_cycle = False  # set when the driver failed; cleared by a power cycle
        self._failed_address: str | None = None  # the USB address it failed on
        self._last_address: str | None = None

    @property
    def usb_address(self) -> tuple[int, int] | None:
        """Parse bounded, already-recorded output lazily; never run or read the driver."""
        try:
            output = getattr(self, "_latch_output", "")[:HEAD_BYTES]
            match = re.search(r"\bbus=([0-9]{1,3})\s+addr=([0-9]{1,3})(?=\s|$)", output)
            if match is None:
                return None
            bus, address = int(match[1]), int(match[2])
            return (bus, address) if bus <= 255 and address <= 255 else None
        except Exception:  # noqa: BLE001 - informational data must never affect scanner calls
            return None

    @property
    def eject_outcome(self) -> str | None:
        """Only a successful, explicitly confirmed eject releases the informational latch."""
        try:
            result = getattr(self, "_eject_result", None)
            if result is None or result.stopped or result.returncode != 0:
                return None
            return (
                "ejected"
                if any(
                    (line == "ejected" or line.startswith("ejected ("))
                    and not any(negative in line.lower() for negative in ("fail", "refus", "error"))
                    for line in result.output[:OUTPUT_TAIL_BYTES].splitlines()
                )
                else None
            )
        except Exception:  # noqa: BLE001 - tolerate unavailable informational observations
            return None

    # -- Scanner protocol ----------------------------------------------------------------

    def scan_holder(self, settings: ScanSettings) -> RawSweep:
        self._require_driver()
        self._scanning.set()  # status() answers BUSY from now on, without touching the scanner
        try:
            with self._hw_lock:  # waits for a status check already in progress
                return self._scan_locked(settings)
        finally:
            self._scanning.clear()
            self._status_cache = None

    def _scan_locked(self, settings: ScanSettings) -> RawSweep:
        base = self._config.scratch_dir()
        base.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="opticfilm-scan-", dir=base))
        try:
            output = work / "tray.tiff"
            command = ["scan", "--slides", SLIDES, "--dpi", str(settings.dpi), "--ir"]
            command += ["--dark-fallback", DARK_FALLBACK, "-o", str(output)]
            self._require_file_lock()
            interrupted.require_clear()  # refused before anything starts: nothing failed
            interrupted.begin("scan")
            try:
                result = self._run(command, self._config.timeout_s, keep_log=True)
            except ScanError:
                self._note_failure()  # a driver that did not finish: state unknown
                self._keep_sidecars(work, failed=True)
                raise
            except BaseException:
                self._note_failure()  # cut off (Ctrl-C): the mark stays until confirmed
                raise
            if result.stopped:  # never stitch what a stopped driver left: a partial payload
                self._note_failure()
                self._keep_sidecars(work, failed=True)
                raise ScanInterrupted(
                    f"the driver was stopped mid-scan. Driver said: {_tail(result.output)}"
                )
            _ended_by_itself()
            failed = _missing_frames(work)
            if failed:
                self._note_failure()
            self._keep_sidecars(work, failed=failed)
            return _stitch(work, settings.dpi, result)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _keep_sidecars(self, work: Path, failed: bool) -> None:
        """Keep the driver's calibration sidecars beside the log: they explain a bad scan."""
        directory = self._config.log_dir
        if directory is None:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        patterns = ["*.diag.json", "*.cal.npz"] if failed else ["*.diag.json"]
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for pattern in patterns:
                for path in work.glob(pattern):
                    shutil.copy2(path, directory / f"{stamp}-{path.name}")
        except OSError:
            log.warning("could not keep the driver's sidecar files in %s", directory)

    def status(self) -> ScannerStatus:
        return self.status_reading().status

    def status_reading(self) -> StatusReading:
        """The status together with the Eject button from the same doctor report."""
        if self._scanning.is_set():
            return StatusReading(ScannerStatus.BUSY)
        with self._status_lock:
            if self._scanning.is_set():  # a scan began while this call waited for the lock
                return StatusReading(ScannerStatus.BUSY)
            if not self._hw_lock.acquire(blocking=False):
                return StatusReading(ScannerStatus.BUSY)  # a scan or eject holds the driver
            try:
                if interrupted.is_pending():  # checked under the lock: never a live command's
                    return StatusReading(ScannerStatus.INTERRUPTED)  # not cached, no driver run
                cached = self._status_cache
                if cached is not None and time.monotonic() - cached[0] < STATUS_MAX_AGE_S:
                    return cached[1]
                reading = self._read_status()
                self._status_cache = (time.monotonic(), reading)  # before the lock is released
            finally:
                self._hw_lock.release()
            return reading

    def eject(self) -> None:
        try:
            self._eject_result = None
        except BaseException:  # informational recording cannot change driver behavior
            pass
        self._holder_command(["eject"], "eject failed.")

    def load_next_strip(self) -> None:
        """Load the next holder without a terminal (``load --next-strip``): only after the
        first, interactive load since power-on."""
        self._holder_command(
            ["load", "--next-strip"], f"loading the next holder failed. {TERMINAL_LOAD_HINT}"
        )

    def _holder_command(self, args: list[str], failure: str) -> None:
        """Run a holder movement under the hardware lock; any failure needs a power cycle."""
        self._require_driver()
        self._status_cache = None
        try:
            with self._hw_lock:
                self._require_file_lock()
                interrupted.require_clear()
                interrupted.begin(args[0])
                try:
                    result = self._run(args, HOLDER_TIMEOUT_S, keep_log=True)
                finally:
                    self._status_cache = None  # whatever happened, the old answer is stale
                if not result.stopped:
                    _ended_by_itself()  # still under the lock: never another command's mark
        except (ScannerInUse, ScannerRefused):
            raise  # the driver never ran: nothing to recover from
        except ScanError:
            self._note_failure()
            raise
        except BaseException:
            self._note_failure()  # cut off (Ctrl-C): the mark stays until confirmed
            raise
        try:
            if args[0] == "eject":
                self._eject_result = result
        except BaseException:  # informational recording cannot change driver behavior
            pass
        if result.stopped:
            self._note_failure()
            raise ScanInterrupted("the driver was stopped mid-move")
        if result.returncode != 0:
            self._note_failure()
            raise ScanError(f"{failure} Driver said: {_tail(result.output)}")

    def _require_file_lock(self) -> None:
        """A command that moves the scanner needs other programs kept off it: without that,
        two commands could overlap and one could clear the other's interrupted mark."""
        if not self._hw_lock.holds_file:
            raise ScannerRefused(
                f"cannot lock {_lock_path()}, so other programs are not kept off the scanner; "
                "refusing to start it"
            )

    def _note_failure(self) -> None:
        """The driver failed or did not finish: only a power cycle recovers the scanner. A power
        cycle shows as a new USB address, or as the cold register state."""
        self._needs_power_cycle = True
        self._failed_address = self._last_address

    # -- internals -----------------------------------------------------------------------

    def _require_driver(self) -> None:
        if not self._config.available():
            where = self._config.driver_dir or "the Python path"
            raise ScanError(
                f"the scanner driver (of135i) was not found in {where}; "
                f"set {DRIVER_ENV} to a checkout of opticfilm135i-linux"
            )

    def _read_status(self) -> StatusReading:
        try:
            self._require_driver()
            result = self._run(["doctor"], STATUS_TIMEOUT_S, keep_log=False, tail=DOCTOR_TAIL_BYTES)
        except ScanError:
            return StatusReading(ScannerStatus.ERROR)
        status = parse_doctor(result.output)
        if result.returncode != 0 and status is ScannerStatus.READY:
            return StatusReading(ScannerStatus.ERROR)  # a failing report is not a ready scanner
        if self._needs_power_cycle:
            address = re.search(r"addr=(\d+)", result.output)
            moved = bool(
                self._failed_address and address and address.group(1) != self._failed_address
            )
            if _register(result.output) == 0x00 or moved:  # cold, or re-enumerated on USB
                self._needs_power_cycle = False
            else:
                return StatusReading(ScannerStatus.ERROR)  # only a power cycle recovers it
        if status not in BUTTON_STATUSES:
            return StatusReading(status)  # a press seen with anything else going on is not acted on
        return StatusReading(status, parse_doctor_button(result.output))

    def _run(
        self, args: Sequence[str], timeout: int, keep_log: bool, tail: int = OUTPUT_TAIL_BYTES
    ) -> "_Result":
        env = dict(os.environ)
        if self._config.driver_dir is not None:
            extra = str(self._config.driver_dir)
            env["PYTHONPATH"] = extra + os.pathsep + env.get("PYTHONPATH", "")
        sink_path, temporary = self._output_file(args[0], keep_log)
        lock_fd = self._hw_lock.file_descriptor()
        held = (lock_fd,) if lock_fd is not None else ()
        try:
            with open(sink_path, "wb") as sink:  # output goes to disk, never all into memory
                proc = subprocess.Popen(
                    [self._config.python, "-m", "of135i", *args],
                    stdin=subprocess.DEVNULL,  # a driver that asks for Enter fails, not hangs
                    pass_fds=held,  # the scanner lock: an orphaned driver still holds it
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    env=env,
                    start_new_session=True,  # its own process group, so helpers can be stopped
                )
                with _LIVE_LOCK:
                    _LIVE.add(proc)
                if args[0] in MARKED_COMMANDS:  # its driver's group joins the mark
                    _note_driver(args[0], proc.pid)
                try:
                    returncode = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    _stop_group(proc)
                    where = sink_path if not temporary else _tail(_read_tail(sink_path, tail))
                    raise ScanInterrupted(  # stopped by this program: its state is unknown
                        f"the driver did not finish within {timeout} s. {POWER_CYCLE_HINT} "
                        f"Log: {where}"
                    ) from exc
                except BaseException:  # Ctrl-C, SystemExit: never leave the driver running
                    _stop_group(proc)
                    raise
                finally:
                    with _LIVE_LOCK:
                        _LIVE.discard(proc)
                        stopped = proc in _SIGNALLED
                        _SIGNALLED.discard(proc)
            # killed by a signal, or exited the way a program does when it was (128 + signal)
            stopped = stopped or returncode < 0 or returncode >= SIGNAL_EXIT_BASE
            output = _read_tail(sink_path, tail)
            try:
                self._latch_output = output
            except BaseException:  # recording alone: no parsing, I/O, or escaped errors
                pass
            return _Result(returncode, output, stopped)
        finally:
            self._last_address = _address_in(sink_path) or self._last_address
            if temporary:
                sink_path.unlink(missing_ok=True)
            elif self._config.log_dir is not None:
                try:
                    _prune_logs(self._config.log_dir)
                except Exception:  # noqa: BLE001 - housekeeping must never lose a finished scan
                    log.warning("could not prune the driver logs", exc_info=True)

    def _output_file(self, name: str, keep_log: bool) -> tuple[Path, bool]:
        """Where a run's output goes: a kept log, or a temporary file for a short query."""
        directory = self._config.log_dir
        if keep_log and directory is not None:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                return directory / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.log", False
            except OSError:
                log.warning("could not write the driver log to %s", directory)
        handle, path = tempfile.mkstemp(prefix="opticfilm-driver-", suffix=".log")
        os.close(handle)
        return Path(path), True


@dataclasses.dataclass(frozen=True)
class _Result:
    returncode: int
    output: str
    stopped: bool = False  # this program stopped it, or a signal killed it: cut off


def _tail(text: str) -> str:
    text = text.strip()
    return text[-LOG_TAIL_CHARS:]


def _frame_files(work: Path, number: int) -> tuple[Path, Path]:
    return work / f"tray-f{number}.overscan.tiff", work / f"tray-f{number}-ir.overscan.tiff"


def _missing_frames(work: Path) -> bool:
    return any(not p.is_file() for n in range(1, 5) for p in _frame_files(work, n))


def _read_ir(path: Path) -> np.ndarray:
    plane = tifffile.imread(path)
    if plane.ndim == 3:  # the driver stores the infrared plane in three equal channels
        plane = plane[:, :, 0]
    return np.ascontiguousarray(plane)


def _page(path: Path) -> tuple[tuple[int, ...], np.dtype]:
    """Shape and pixel type of a frame, read without loading the pixels."""
    with tifffile.TiffFile(path) as handle:
        page = handle.pages[0]
        return tuple(page.shape), np.dtype(page.dtype)


def _check_frames(paths: list[tuple[Path, Path]]) -> None:
    """Refuse a damaged or unexpected frame before anything is stitched: a wrong pixel type or
    size would otherwise broadcast or scale silently into a plausible-looking sweep."""
    for rgb_path, ir_path in paths:
        try:
            rgb_shape, rgb_type = _page(rgb_path)
            ir_shape, ir_type = _page(ir_path)
        except (OSError, ValueError, tifffile.TiffFileError) as exc:
            raise ScanError(f"{rgb_path.name} or its infrared frame is unreadable: {exc}") from exc
        if rgb_type != np.uint16 or ir_type != np.uint16:
            raise ScanError(f"{rgb_path.name}: expected 16-bit frames, got {rgb_type}/{ir_type}")
        if len(rgb_shape) != 3 or rgb_shape[2] != 3:
            raise ScanError(f"{rgb_path.name}: expected 3 colour channels, got shape {rgb_shape}")
        if tuple(ir_shape[:2]) != tuple(rgb_shape[:2]):
            raise ScanError(
                f"{ir_path.name}: the infrared frame {ir_shape[:2]} does not match the "
                f"visible frame {rgb_shape[:2]}"
            )


def _turned_shape(path: Path) -> tuple[int, int]:
    """(rows, columns) of a frame after the quarter turn, read without loading the pixels."""
    with tifffile.TiffFile(path) as handle:
        height, width = handle.pages[0].shape[:2]
    return (width, height) if FRAME_QUARTER_TURNS % 2 else (height, width)


def _stitch(work: Path, dpi: int, result: _Result) -> RawSweep:
    if _missing_frames(work):
        raise ScanError(f"{POWER_CYCLE_HINT} Driver said: {_tail(result.output)}")
    paths = [_frame_files(work, n) for n in range(1, 5)]
    _check_frames(paths)
    shapes = [_turned_shape(rgb_path) for rgb_path, _ in paths]
    rows, width = sum(h for h, _ in shapes), max(w for _, w in shapes)
    needed = rows * width * (3 * 2 + 2)  # visible 3 x 16-bit + infrared 16-bit
    free = shutil.disk_usage(work.parent).free
    if free < needed * SPACE_MARGIN:  # a full disk would otherwise end in SIGBUS, not an error
        raise ScanError(
            f"not enough disk space to assemble the tray: about {needed / 1e9:.1f} GB needed, "
            f"{free / 1e9:.1f} GB free in {work.parent}"
        )
    # The sweep lives in files beside the scratch folder, one frame in memory at a time: a
    # 7200 dpi tray is several gigabytes. The files are unlinked once mapped.
    stack = Path(tempfile.mkdtemp(prefix="opticfilm-sweep-", dir=work.parent))
    try:
        rgb_all = np.lib.format.open_memmap(
            stack / "rgb.npy", mode="w+", dtype=np.uint16, shape=(rows, width, 3)
        )
        ir_all = np.lib.format.open_memmap(
            stack / "ir.npy", mode="w+", dtype=np.uint16, shape=(rows, width)
        )
        row = 0
        for (rgb_path, ir_path), (height, frame_width) in zip(paths, shapes, strict=True):
            rgb_all[row : row + height, :frame_width] = np.rot90(
                tifffile.imread(rgb_path), FRAME_QUARTER_TURNS
            )
            ir_all[row : row + height, :frame_width] = np.rot90(
                _read_ir(ir_path), FRAME_QUARTER_TURNS
            )
            row += height
        return RawSweep(rgb=rgb_all, ir=ir_all, dpi=dpi, holder=HolderKind.SLIDE4)
    finally:
        shutil.rmtree(stack, ignore_errors=True)  # the mappings stay valid after unlinking
