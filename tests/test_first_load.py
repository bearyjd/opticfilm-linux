"""First-load regressions use only a tiny driver process and fake scanners."""

import os
import threading

import pytest

from controller_helpers import open_new, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.scanner import interrupted
from opticfilm_linux.scanner.base import ScanError, ScannerStatus, StatusReading
from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner
from opticfilm_linux.scanner.fake import FakeScanner

SCRIPT = r"""
import os, signal, sys, time
from pathlib import Path
mode = os.environ.get("LOAD_MODE", "normal")
trace = Path(os.environ["LOAD_TRACE"])
def note(text):
    with trace.open("a") as out:
        out.write(text + "\n")
def stop(*args):
    note("signal")
    if mode == "shutdown":
        import select
        ready = select.select([sys.stdin], [], [], .1)[0]
        note("stdin-open" if not ready else "EOF" if not os.read(0, 1) else "extra")
    sys.exit(0 if mode == "signal_zero" else 130)
signal.signal(signal.SIGINT, stop)
if mode == "broken":
    os.close(0)
if mode == "stdout_closed":
    os.close(1)
    os.close(2)
    time.sleep(.15)
    sys.exit(0)
if mode == "long_split":
    os.write(1, b"Press Enter when it ")
    time.sleep(.05)
    os.write(1, b"is at the stop" + b"x" * 8192)
elif mode == "split":
    os.write(1, b"Press Enter when it ")
    time.sleep(.05)
    os.write(1, b"is at the stop")
else:
    os.write(1, b"Press Enter when it is at the stop")
if mode == "early":
    sys.exit(7)
if mode == "broken":
    time.sleep(.15)
    sys.exit(0)
line = sys.stdin.buffer.readline()
if not line:
    note("EOF")
    sys.exit(130)
note(repr(line))
time.sleep(.05)
if mode == "eof":
    # Success requires stdin to stay open after the one Enter too.
    import select
    if select.select([sys.stdin], [], [], .15)[0]:
        note("EOF" if not sys.stdin.buffer.read(1) else "extra")
        sys.exit(130)
sys.exit(0)
"""


@pytest.fixture
def driver(tmp_path, monkeypatch):
    package = tmp_path / "driver" / "of135i"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(SCRIPT)
    trace = tmp_path / "trace"
    monkeypatch.setenv("LOAD_TRACE", str(trace))
    return DriverScanner(DriverConfig(driver_dir=package.parent)), trace


@pytest.mark.parametrize("mode", ["normal", "split", "eof"])
def test_prompt_without_newline_and_exactly_one_enter(driver, monkeypatch, mode):
    scanner, trace = driver
    monkeypatch.setenv("LOAD_MODE", mode)
    done, cancel = threading.Event(), threading.Event()
    prompts = []

    def prompt():
        prompts.append(True)
        done.set()

    assert scanner.load_interactive(prompt, done, cancel) == 0
    assert prompts == [True]
    assert trace.read_text().splitlines() == ["b'\\n'"]
    assert not interrupted.is_pending()


def test_done_before_prompt_does_not_skip_prompt(driver):
    scanner, trace = driver
    done = threading.Event()
    done.set()
    prompts = []
    assert scanner.load_interactive(lambda: prompts.append(True), done, threading.Event()) == 0
    assert prompts == [True] and "EOF" not in trace.read_text()


def test_exit_before_done_reports_code_without_waiting_for_human(driver, monkeypatch):
    scanner, _ = driver
    monkeypatch.setenv("LOAD_MODE", "early")
    with pytest.raises(ScanError, match="exit code 7"):
        scanner.load_interactive(lambda: None, threading.Event(), threading.Event())


def test_cancel_signals_child_without_sending_eof(driver):
    scanner, trace = driver
    cancel = threading.Event()
    with pytest.raises(ScanError, match="exit code"):
        scanner.load_interactive(cancel.set, threading.Event(), cancel)
    assert trace.read_text().splitlines() == ["signal"]
    assert interrupted.is_pending()


class InteractiveScanner(FakeScanner):
    def __init__(self, sweep):
        super().__init__(sweep, (ScannerStatus.NOT_READY,))
        self.calls = []

    def load_interactive(self, on_prompt, done, cancel):
        self.calls.append("load")
        on_prompt()
        while not done.wait(0.01):
            if cancel.is_set():
                self.calls.append("cancel")
                raise ScanError("cancelled, exit code 130")
        self.calls.append("done")
        return 0


@pytest.fixture
def first_controller(sweep600_dir):
    scanner = InteractiveScanner(sweep600_dir)
    controller = Controller(lambda _dpi: scanner)
    yield controller, scanner
    controller.shutdown()


def test_first_load_controller_phases_and_reservation(first_controller, tmp_path):
    c, scanner = first_controller
    open_new(c, tmp_path / "batch")
    wait_until(c, lambda: c.state().can_first_load)
    c.first_load_done()  # stray Done must not acknowledge a future prompt
    c.first_load()
    c.first_load()
    wait_until(c, lambda: c.state().load_phase == "await_reinsert")
    assert c.state().holder_busy and not c.state().can_scan and not c.state().can_eject
    c.close_batch()
    c.scan()
    c.eject_holder()
    assert c._holder_busy and c.state().batch_name == "Test"
    c.first_load_done()
    c.first_load_done()
    wait_until(c, lambda: c.state().load_phase == "done" and not c.state().holder_busy)
    assert scanner.calls == ["load", "done"]
    assert c.state().load_exit_code == 0
    assert "latched" in c.state().holder_latch_text.lower()


def test_first_load_cancel_and_shutdown_are_bounded(first_controller, tmp_path):
    c, scanner = first_controller
    open_new(c, tmp_path / "batch")
    c.first_load()
    wait_until(c, lambda: c.state().load_phase == "await_reinsert")
    c.first_load_cancel()
    wait_until(c, lambda: c.state().load_phase == "failed" and not c.state().holder_busy)
    assert scanner.calls == ["load", "cancel"]
    assert "power cycle" in c.state().banner.message.lower()
    c.first_load()
    wait_until(c, lambda: scanner.calls == ["load", "cancel", "load"])
    c.shutdown()
    assert scanner.calls[-1] == "cancel"


def test_first_load_shutdown_reaps_driver_before_closing_stdin(driver, monkeypatch, tmp_path):
    scanner, trace = driver
    monkeypatch.setenv("LOAD_MODE", "shutdown")
    monkeypatch.setattr(scanner, "status_reading", lambda: StatusReading(ScannerStatus.NOT_READY))
    spawned = []
    spawn = scanner._spawn_first_load

    def capture_spawn():
        proc = spawn()
        spawned.append((proc, threading.current_thread()))
        return proc

    monkeypatch.setattr(scanner, "_spawn_first_load", capture_spawn)
    c = Controller(lambda _dpi: scanner)
    shutdown = threading.Thread(target=c.shutdown, daemon=True)
    try:
        open_new(c, tmp_path / "batch")
        wait_until(c, lambda: c.state().can_first_load)
        c.first_load()
        wait_until(c, lambda: c.state().load_phase == "await_reinsert")
        proc, worker = spawned[0]
        future = c._holder_future
        assert proc.returncode is None and not proc.stdin.closed
        shutdown.start()
        shutdown.join(timeout=3)
        assert not shutdown.is_alive(), "shutdown did not stop the first-load worker"
        assert future.done() and not worker.is_alive()
        assert proc.returncode == 130
        with pytest.raises(ChildProcessError):
            os.waitpid(proc.pid, os.WNOHANG)
        assert proc.stdin.closed and proc.stdout.closed
        assert trace.read_text().splitlines() == ["signal", "stdin-open"]
    finally:
        c.first_load_cancel()
        if shutdown.ident is not None:
            shutdown.join(timeout=3)
        if not shutdown.is_alive():
            c.shutdown()


def test_first_load_unavailable_on_old_holder_backend(sweep600_dir, tmp_path):
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir, (ScannerStatus.NOT_READY,)))
    try:
        open_new(c, tmp_path / "batch")
        wait_until(c, lambda: c.state().can_eject)
        assert not c.state().can_first_load
        c.first_load()
        wait_until(c, lambda: c.state().banner is not None)
        assert not c._holder_busy
    finally:
        c.shutdown()


def test_broken_pipe_at_done_is_quiet(driver, monkeypatch):
    scanner, _ = driver
    monkeypatch.setenv("LOAD_MODE", "broken")
    done = threading.Event()
    assert scanner.load_interactive(done.set, done, threading.Event()) == 0


def test_callback_failure_reaps_without_eof(driver):
    scanner, trace = driver

    def fail():
        raise ValueError("UI callback failed")

    with pytest.raises(ScanError, match="exit code 130.*UI callback failed"):
        scanner.load_interactive(fail, threading.Event(), threading.Event())
    assert trace.read_text().splitlines() == ["signal"]
    assert interrupted.is_pending()


def test_human_timeout_stops_before_closing_stdin(driver, monkeypatch):
    scanner, trace = driver
    monkeypatch.setattr("opticfilm_linux.scanner.driver.HUMAN_WAIT_S", 0.02)
    with pytest.raises(ScanError, match="timed out.*exit code 130"):
        scanner.load_interactive(lambda: None, threading.Event(), threading.Event())
    assert trace.read_text().splitlines() == ["signal"]
    assert interrupted.is_pending()


def test_new_batch_clears_finished_first_load(first_controller, tmp_path):
    c, _ = first_controller
    open_new(c, tmp_path / "batch")
    c.first_load()
    wait_until(c, lambda: c.state().load_phase == "await_reinsert")
    c.first_load_done()
    wait_until(c, lambda: c.state().load_phase == "done" and not c.state().holder_busy)
    c.close_batch()
    wait_until(c, lambda: c.state().batch_name is None)
    assert c.state().load_phase == "idle" and c.state().load_exit_code is None
    open_new(c, tmp_path / "next")
    assert c.state().load_phase == "idle"


def test_marker_split_before_a_full_chunk_is_detected(driver, monkeypatch):
    scanner, trace = driver
    monkeypatch.setenv("LOAD_MODE", "long_split")
    monkeypatch.setattr("opticfilm_linux.scanner.driver.HOLDER_TIMEOUT_S", 0.5)
    done = threading.Event()
    assert scanner.load_interactive(done.set, done, threading.Event()) == 0
    assert trace.read_text().splitlines() == ["b'\\n'"]


def test_signalled_child_that_exits_zero_keeps_interrupted_mark(driver, monkeypatch):
    from opticfilm_linux.scanner.driver import _stop_all_drivers

    scanner, trace = driver
    monkeypatch.setenv("LOAD_MODE", "signal_zero")
    with pytest.raises(ScanError, match="exit code 0"):
        scanner.load_interactive(_stop_all_drivers, threading.Event(), threading.Event())
    assert trace.read_text().splitlines() == ["signal"]
    assert interrupted.is_pending()


def test_closed_stdout_does_not_spin_while_child_finishes(driver, monkeypatch):
    import opticfilm_linux.scanner.driver as driver_module

    scanner, _ = driver
    monkeypatch.setenv("LOAD_MODE", "stdout_closed")
    original_read = driver_module.os.read
    reads = []

    def counted_read(fd, count):
        reads.append(True)
        return original_read(fd, count)

    monkeypatch.setattr(driver_module.os, "read", counted_read)
    assert scanner.load_interactive(lambda: None, threading.Event(), threading.Event()) == 0
    assert len(reads) < 20


def test_first_load_status_callback_runs_outside_controller_lock(first_controller, tmp_path):
    c, _ = first_controller
    open_new(c, tmp_path / "batch")
    poller = c._session.poller
    original = poller.current
    calls = []

    def reporting_status():
        # A stale StatusPoller.current() can report through _scanner_problem -> _set.
        assert c._lock.acquire(blocking=False), "status callback would deadlock"
        c._lock.release()
        calls.append(True)
        c._scanner_problem("Status refresh", "stale status callback")
        return ScannerStatus.NOT_READY

    poller.current = reporting_status
    try:
        c.first_load()
    finally:
        poller.current = original
    wait_until(c, lambda: c.state().load_phase == "await_reinsert")
    assert calls
    c.first_load_cancel()
    wait_until(c, lambda: not c.state().holder_busy)
