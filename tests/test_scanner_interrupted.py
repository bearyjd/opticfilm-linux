"""A scanner command cut off mid-run leaves a durable mark that refuses every further command.

The scanner's state after a killed driver is unknown (the E3 slide-4 run), so nothing may move
the holder or scan again, in this program or any other, until the owner confirms the scanner
was power-cycled and the holder reloaded.
"""

import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from opticfilm_linux.model import ScanSettings
from opticfilm_linux.scanner import driver as driver_module
from opticfilm_linux.scanner import interrupted
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.scanner.driver import (
    DriverConfig,
    DriverScanner,
    ScannerInUse,
    confirm_scanner_reset,
)
from opticfilm_linux.scanner.interrupted import (
    INTERRUPTED_TEXT,
    ScanInterrupted,
    ScannerInterrupted,
)

FAKE_DRIVER = Path(__file__).parent / "fake_driver"


@pytest.fixture
def make(sweep600_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    monkeypatch.setenv("FAKE_DRIVER_TRACE", str(tmp_path / "trace.log"))
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "idle")  # looks loaded and ready

    def build():
        return DriverScanner(
            DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs", work_dir=tmp_path / "w")
        )

    return build


def _trace(tmp_path: Path) -> list[str]:
    path = tmp_path / "trace.log"
    return (
        [line.split(maxsplit=1)[1] for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def _wait_for(path: Path, timeout: float = 90.0) -> None:  # a loaded machine starts slowly
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.exists(), f"{path} never appeared"


def _kill_leftovers(pids_file: Path) -> None:
    if not pids_file.exists():
        return
    for pid in json.loads(pids_file.read_text()):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _assert_refuses_everything(scanner: DriverScanner, tmp_path: Path) -> None:
    before = _trace(tmp_path)
    assert scanner.status() is ScannerStatus.INTERRUPTED
    with pytest.raises(ScannerInterrupted):
        scanner.eject()
    with pytest.raises(ScannerInterrupted):
        scanner.load_next_strip()
    with pytest.raises(ScannerInterrupted):
        scanner.scan_holder(ScanSettings(dpi=600))
    assert _trace(tmp_path) == before  # no driver command of any kind was started


# -- in-process interrupt ------------------------------------------------------------------


def test_ctrl_c_during_a_scan_leaves_the_scanner_refusing_until_confirmed(
    make, tmp_path, monkeypatch
):
    pids_file = tmp_path / "pids.json"
    monkeypatch.setenv("FAKE_DRIVER_MODE", "hang")
    monkeypatch.setenv("FAKE_DRIVER_PIDS", str(pids_file))
    monkeypatch.setattr(driver_module, "STOP_GRACE_S", 1.0)
    scanner = make()
    outcome: list[BaseException] = []

    def scan() -> None:
        try:
            scanner.scan_holder(ScanSettings(dpi=600))
        except BaseException as exc:  # noqa: BLE001 - the interrupt is the thing under test
            outcome.append(exc)

    thread = threading.Thread(target=scan)
    thread.start()
    _wait_for(pids_file)
    time.sleep(0.3)
    ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(thread.ident), ctypes.py_object(KeyboardInterrupt)
    )
    thread.join(20)
    assert outcome and isinstance(outcome[0], KeyboardInterrupt)
    monkeypatch.setenv("FAKE_DRIVER_MODE", "ok")
    _assert_refuses_everything(scanner, tmp_path)
    _assert_refuses_everything(make(), tmp_path)  # a new scanner object (another batch) too
    assert confirm_scanner_reset() is True
    assert not interrupted.is_pending()


def test_a_driver_stopped_by_the_signal_handler_is_cut_off_even_if_it_exits_cleanly(
    make, tmp_path, monkeypatch
):
    """SIGTERM/SIGHUP stop drivers from the main thread while a worker waits on one; the
    driver then exits by itself with a code, which must still count as cut off."""
    pids_file = tmp_path / "pids.json"
    monkeypatch.setenv("FAKE_DRIVER_MODE", "hang")
    monkeypatch.setenv("FAKE_DRIVER_PIDS", str(pids_file))
    monkeypatch.setattr(driver_module, "STOP_GRACE_S", 5.0)
    scanner = make()
    outcome: list[BaseException] = []

    def scan() -> None:
        try:
            scanner.scan_holder(ScanSettings(dpi=600))
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            outcome.append(exc)

    thread = threading.Thread(target=scan)
    thread.start()
    try:
        _wait_for(pids_file)
        time.sleep(0.3)
        driver_module._stop_all_drivers()  # what the SIGTERM handler does first
        thread.join(30)
    finally:
        _kill_leftovers(pids_file)
    assert outcome and isinstance(outcome[0], ScanInterrupted)
    assert interrupted.is_pending()


def test_a_driver_we_stopped_never_yields_a_tray_even_with_every_frame_on_disk(
    make, tmp_path, monkeypatch
):
    """A driver this program signalled may exit by itself with all frames written: still a
    partial payload, never a finished tray."""
    scanner = make()
    real_wait = subprocess.Popen.wait

    def wait_then_stopped(proc, timeout=None):
        code = real_wait(proc, timeout)
        if proc.args[3:4] == ["scan"]:
            with driver_module._LIVE_LOCK:
                driver_module._SIGNALLED.add(proc)  # as _stop_group does before it signals
        return code

    monkeypatch.setattr(subprocess.Popen, "wait", wait_then_stopped)
    with pytest.raises(ScanInterrupted, match=re.escape(INTERRUPTED_TEXT)):
        scanner.scan_holder(ScanSettings(dpi=600))
    assert interrupted.is_pending()


def test_a_driver_killed_by_a_signal_from_outside_is_an_interrupted_scan(
    make, tmp_path, monkeypatch
):
    scanner = make()
    real_wait = subprocess.Popen.wait

    def killed(proc, timeout=None):
        real_wait(proc, timeout)
        return -signal.SIGKILL if proc.args[3:4] == ["scan"] else proc.returncode

    monkeypatch.setattr(subprocess.Popen, "wait", killed)
    with pytest.raises(ScanInterrupted, match=re.escape(INTERRUPTED_TEXT)):
        scanner.scan_holder(ScanSettings(dpi=600))
    assert interrupted.is_pending()


def test_a_timed_out_holder_move_is_an_interrupted_move(make, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_LOAD_DELAY", "30")
    monkeypatch.setattr(driver_module, "HOLDER_TIMEOUT_S", 1)
    monkeypatch.setattr(driver_module, "STOP_GRACE_S", 1.0)
    scanner = make()
    with pytest.raises(ScanInterrupted, match="did not finish"):
        scanner.load_next_strip()
    assert interrupted.is_pending()


# -- what still works exactly as before -----------------------------------------------------


def test_a_finished_scan_and_eject_leave_no_mark(make, tmp_path):
    scanner = make()
    assert scanner.scan_holder(ScanSettings(dpi=600)).rgb.shape[0] > 0
    scanner.eject()
    assert not interrupted.is_pending()
    assert scanner.status() is ScannerStatus.READY


def test_a_driver_that_reports_a_failure_keeps_the_power_cycle_detection(make, monkeypatch):
    """The driver ended by itself and failed closed: its state is known, so the existing
    in-memory power-cycle detection applies, not the interrupted mark."""
    scanner = make()
    monkeypatch.setenv("FAKE_DRIVER_MODE", "cal_fail")
    with pytest.raises(ScanError):
        scanner.scan_holder(ScanSettings(dpi=600))
    assert not interrupted.is_pending()
    assert scanner.status() is ScannerStatus.ERROR


def test_the_mark_is_written_before_the_driver_starts(make, tmp_path, monkeypatch):
    seen: list[bool] = []
    real_popen = subprocess.Popen

    def spy(args, *rest, **kwargs):
        if "scan" in args or "eject" in args:
            seen.append(interrupted.is_pending())
        return real_popen(args, *rest, **kwargs)

    monkeypatch.setattr(driver_module.subprocess, "Popen", spy)
    scanner = make()
    scanner.scan_holder(ScanSettings(dpi=600))
    scanner.eject()
    assert seen == [True, True]


def test_no_driver_starts_when_the_mark_cannot_be_written(make, tmp_path, monkeypatch):
    def unwritable(_command: str) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(interrupted, "_write_marker", unwritable)
    scanner = make()
    with pytest.raises(ScanError, match="refusing"):
        scanner.eject()
    with pytest.raises(ScanError, match="refusing"):
        scanner.scan_holder(ScanSettings(dpi=600))
    assert _trace(tmp_path) == []
    assert scanner.status() is ScannerStatus.READY  # nothing ran: no power cycle is needed


def test_a_mark_that_cannot_be_checked_counts_as_interrupted(monkeypatch):
    def unreadable(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(interrupted.os, "stat", unreadable)
    assert interrupted.is_pending()


def test_status_never_runs_the_driver_while_interrupted(make, tmp_path):
    interrupted.begin("scan")
    assert make().status() is ScannerStatus.INTERRUPTED
    assert _trace(tmp_path) == []


# -- confirming -----------------------------------------------------------------------------


def test_confirm_is_refused_while_another_program_holds_the_scanner(make):
    interrupted.begin("scan")
    assert driver_module._HW_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ScannerInUse):
            confirm_scanner_reset()
    finally:
        driver_module._HW_LOCK.release()
    assert interrupted.is_pending()  # a running command's mark is never removed


def test_confirm_with_nothing_pending_says_so_and_never_runs_the_driver(make, tmp_path):
    assert confirm_scanner_reset() is False
    assert _trace(tmp_path) == []


def test_a_status_check_by_another_program_during_a_scan_says_busy_not_interrupted(make):
    interrupted.begin("scan")  # the live mark of a scan that holds the hardware lock
    assert driver_module._HW_LOCK.acquire(blocking=False)
    try:
        assert make().status() is ScannerStatus.BUSY
    finally:
        driver_module._HW_LOCK.release()


# -- the process itself dies ----------------------------------------------------------------

_CHILD = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from opticfilm_linux.model import ScanSettings
    from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner, install_signal_cleanup
    install_signal_cleanup()
    driver_dir, work = Path(sys.argv[1]), Path(sys.argv[2])
    config = DriverConfig(driver_dir=driver_dir, log_dir=work / "logs", work_dir=work)
    DriverScanner(config).scan_holder(ScanSettings(dpi=600))
    """
)


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM])
def test_a_scanning_process_that_dies_leaves_the_scanner_refusing(make, tmp_path, monkeypatch, sig):
    pids_file = tmp_path / "pids.json"
    env = {
        **os.environ,
        "FAKE_DRIVER_MODE": "hang",
        "FAKE_DRIVER_PIDS": str(pids_file),
    }
    env.pop("OPTICFILM_DRIVER_DIR", None)
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(FAKE_DRIVER), str(tmp_path / "child")], env=env
    )
    try:
        _wait_for(pids_file)
        time.sleep(0.3)
        child.send_signal(sig)
        child.wait(30)
        _assert_refuses_everything(make(), tmp_path)
        if sig == signal.SIGKILL:  # nothing stopped the driver: it is still running, orphaned
            with pytest.raises(ScannerInUse, match="still running"):
                confirm_scanner_reset()
            assert interrupted.is_pending()
    finally:
        if child.poll() is None:
            child.kill()
        _kill_leftovers(pids_file)
    group = json.loads(pids_file.read_text())[0]  # the driver leads its own process group
    deadline = time.monotonic() + 60
    while driver_module._group_alive(group) and time.monotonic() < deadline:
        time.sleep(0.1)  # until init has reaped the killed orphans
    assert confirm_scanner_reset() is True
    assert make().status() is ScannerStatus.READY


def test_a_dead_process_leaves_no_driver_behind_after_sigterm(tmp_path):
    pids_file = tmp_path / "pids.json"
    env = {**os.environ, "FAKE_DRIVER_MODE": "hang", "FAKE_DRIVER_PIDS": str(pids_file)}
    env.pop("OPTICFILM_DRIVER_DIR", None)
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(FAKE_DRIVER), str(tmp_path / "child")], env=env
    )
    try:
        _wait_for(pids_file)
        time.sleep(0.3)
        child.send_signal(signal.SIGTERM)
        child.wait(30)
        driver, helper = json.loads(pids_file.read_text())
        deadline = time.monotonic() + 15
        while (_alive(driver) or _alive(helper)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(driver) and not _alive(helper)
    finally:
        if child.poll() is None:
            child.kill()
        _kill_leftovers(pids_file)


# -- review follow-ups ----------------------------------------------------------------------


def test_a_holder_move_clears_its_mark_while_still_holding_the_scanner(make, monkeypatch):
    held: list[bool] = []
    real_clear = interrupted.clear

    def spy() -> None:
        held.append(driver_module._HW_LOCK._thread.locked())
        real_clear()

    monkeypatch.setattr(interrupted, "clear", spy)
    make().eject()
    assert held == [True]


def test_a_driver_exiting_with_128_plus_a_signal_is_cut_off(make, monkeypatch):
    real_wait = subprocess.Popen.wait

    def sigint_exit(proc, timeout=None):
        real_wait(proc, timeout)
        return 130 if proc.args[3:4] == ["eject"] else proc.returncode

    monkeypatch.setattr(subprocess.Popen, "wait", sigint_exit)
    with pytest.raises(ScanInterrupted):
        make().eject()
    assert interrupted.is_pending()


def test_a_new_mark_shows_at_once_despite_a_cached_ready(make):
    scanner = make()
    assert scanner.status() is ScannerStatus.READY  # now cached
    interrupted.begin("scan")  # another program was cut off meanwhile
    assert scanner.status() is ScannerStatus.INTERRUPTED


def test_the_mark_records_the_driver_process_group(make, tmp_path, monkeypatch):
    groups: list[int | None] = []
    real_popen = subprocess.Popen

    class Spy(real_popen):
        def wait(self, timeout=None):
            if self.args[3:4] == ["eject"]:
                groups.append(interrupted.driver_group())
            return super().wait(timeout)

    monkeypatch.setattr(driver_module.subprocess, "Popen", Spy)
    make().eject()
    assert groups and groups[0] is not None and groups[0] > 1


def test_confirm_is_refused_when_the_lock_file_cannot_be_used(make, monkeypatch):
    monkeypatch.setattr(driver_module._HW_LOCK, "_warned", False)  # restored afterwards
    interrupted.begin("scan")

    def unusable(_handle, _flags):
        raise OSError("no locks on this file system")

    monkeypatch.setattr(driver_module.fcntl, "flock", unusable)
    with pytest.raises(interrupted.ScannerRefused, match="confirm refused"):
        confirm_scanner_reset()
    assert interrupted.is_pending()


def test_the_mark_and_its_folder_reach_the_disk(monkeypatch):
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(interrupted.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    interrupted.begin("scan")
    assert len(synced) == 2  # the file, then its folder (the rename)


def test_marker_content_is_shown_short_and_printable():
    path = interrupted.marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"command": "scan\x1b[31m" + "x" * 500, "started": "now"}))
    text = interrupted.refusal_text()
    assert "\x1b" not in text and len(text) < 600
    path.write_text("[" * 100_000)  # not ours, and deeply nested
    assert "details unreadable" in interrupted.refusal_text()


def test_a_relative_state_home_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert interrupted.state_dir() == tmp_path / "home" / ".local" / "state" / "opticfilm-linux"


def test_no_home_folder_counts_as_interrupted_and_refuses_to_start(monkeypatch):
    def no_home():
        raise RuntimeError("Could not determine home directory")

    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setattr(interrupted.Path, "home", no_home)
    assert interrupted.is_pending()
    with pytest.raises(interrupted.ScannerRefused, match="refusing"):
        interrupted.begin("scan")
