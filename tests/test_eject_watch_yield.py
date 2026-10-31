"""The app stops the background Eject-button watcher before it opens the scanner."""

import logging
import subprocess
import threading
import time
from pathlib import Path

import pytest

from controller_helpers import open_new, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.eject_watch import (
    UNIT,
    EjectWatch,
    WatchStillActive,
    watch_process_running,
)
from opticfilm_linux.app.status import StatusPoller
from opticfilm_linux.batch.model import BatchDefaults
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner
from test_app_controller_holder import RecordingScanner


class Systemctl:
    """A scripted ``systemctl``: ``states`` answer successive ``is-active`` calls (the last one
    repeats), as ``(exit code, output)`` pairs or bare state words."""

    def __init__(self, states, stop_rc=0, start_rc=0, missing=False, timeout_on=None):
        self.states = list(states)
        self.stop_rc = stop_rc
        self.start_rc = start_rc
        self.missing = missing
        self.timeout_on = timeout_on
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args):
        self.calls.append(tuple(args))
        if self.missing:
            raise FileNotFoundError("systemctl")
        verb = args[1]
        if verb == self.timeout_on:
            raise subprocess.TimeoutExpired(args, 1)
        if verb == "is-active":
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return state if isinstance(state, tuple) else (0, state + "\n")
        return (self.stop_rc if verb == "stop" else self.start_rc), ""

    def verbs(self):
        return [c[1] for c in self.calls]


def make_watch(runner, process=False):
    """An ``EjectWatch`` that never sleeps and sees a ``watch`` process only if told to."""
    return EjectWatch(runner, process_check=lambda: process, sleep=lambda _seconds: None)


def test_active_unit_is_stopped_then_started_again():
    runner = Systemctl(["active", "inactive"])
    watch = make_watch(runner)
    watch.yield_to_app()
    assert runner.verbs() == ["is-active", "stop", "is-active"]
    assert runner.calls[1] == ("--user", "stop", UNIT)
    assert watch.restore() is None
    assert runner.calls[-1] == ("--user", "start", UNIT)


def test_inactive_or_missing_unit_is_left_alone():
    runner = Systemctl(["inactive"])
    watch = make_watch(runner)
    watch.yield_to_app()
    assert watch.restore() is None
    assert runner.verbs() == ["is-active"]


def test_missing_systemctl_is_a_quiet_no_op():
    runner = Systemctl([], missing=True)
    watch = make_watch(runner)
    watch.yield_to_app()
    assert watch.restore() is None


def test_restore_twice_starts_only_once():
    runner = Systemctl(["active", "inactive"])
    watch = make_watch(runner)
    watch.yield_to_app()
    watch.restore()
    watch.restore()
    assert runner.verbs().count("start") == 1


def test_a_second_yield_without_a_restore_keeps_the_unit_to_restart():
    runner = Systemctl(["active", "inactive"])  # inactive from then on: we stopped it
    watch = make_watch(runner)
    watch.yield_to_app()
    watch.yield_to_app()
    assert runner.verbs().count("stop") == 1
    assert watch.restore() is None
    assert runner.verbs().count("start") == 1


def test_a_retrying_unit_counts_as_active():
    runner = Systemctl(["activating", "inactive"])
    watch = make_watch(runner)
    watch.yield_to_app()
    assert "stop" in runner.verbs()
    watch.restore()
    assert "start" in runner.verbs()


def test_refuses_when_still_active_after_the_stop():
    with pytest.raises(WatchStillActive):
        make_watch(Systemctl(["active"])).yield_to_app()


def test_refuses_when_still_deactivating():
    with pytest.raises(WatchStillActive):
        make_watch(Systemctl(["active", "deactivating"])).yield_to_app()


def test_waits_for_a_stop_that_is_still_finishing():
    runner = Systemctl(["active", "deactivating", "deactivating", "inactive"])
    make_watch(runner).yield_to_app()


def test_failed_stop_is_fine_when_the_unit_is_down_anyway():
    make_watch(Systemctl(["active", "inactive"], stop_rc=1)).yield_to_app()


def test_stop_timeout_with_unit_still_active_refuses():
    runner = Systemctl(["active"], timeout_on="stop")
    with pytest.raises(WatchStillActive):
        make_watch(runner).yield_to_app()


def test_unknown_state_after_stop_refuses():
    with pytest.raises(WatchStillActive):
        make_watch(Systemctl(["active", "weird"])).yield_to_app()


def test_failed_start_is_reported_not_raised():
    runner = Systemctl(["active", "inactive"], start_rc=1)
    watch = make_watch(runner)
    watch.yield_to_app()
    assert "Eject button" in (watch.restore() or "")


def test_start_timeout_is_reported_not_raised():
    runner = Systemctl(["active", "inactive"], timeout_on="start")
    watch = make_watch(runner)
    watch.yield_to_app()
    assert watch.restore() is not None


# -- a watch process outside the unit ------------------------------------------------------


def test_a_failed_unit_with_no_watch_process_left_is_stopped_enough():
    make_watch(Systemctl(["active", "failed"])).yield_to_app()


def test_a_failed_unit_whose_watch_process_lives_on_refuses():
    # TimeoutStopSec expired with SendSIGKILL=no: the unit is failed but `watch` is not dead.
    with pytest.raises(WatchStillActive):
        make_watch(Systemctl(["active", "failed"]), process=True).yield_to_app()


def test_an_inactive_unit_with_a_watch_process_refuses():
    # KillMode=process can leave `of135i watch` running outside the unit.
    runner = Systemctl(["active", "inactive"])
    with pytest.raises(WatchStillActive):
        make_watch(runner, process=True).yield_to_app()


def test_a_hand_started_watch_is_refused_without_systemctl():
    with pytest.raises(WatchStillActive):
        make_watch(Systemctl([], missing=True), process=True).yield_to_app()


def test_a_hand_started_watch_is_refused_when_the_unit_is_not_installed():
    runner = Systemctl([(4, "inactive\n")])
    with pytest.raises(WatchStillActive):
        make_watch(runner, process=True).yield_to_app()
    assert "stop" not in runner.verbs()


def test_the_message_names_the_stuck_process():
    with pytest.raises(WatchStillActive, match="of135i watch"):
        make_watch(Systemctl(["inactive"]), process=True).yield_to_app()


def test_no_user_bus_is_logged_as_a_warning_and_not_an_error(caplog):
    runner = Systemctl([(1, "")])  # "Failed to connect to bus": no answer on stdout
    with caplog.at_level(logging.WARNING):
        make_watch(runner).yield_to_app()
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_no_user_bus_still_refuses_for_a_running_watch():
    with pytest.raises(WatchStillActive):
        make_watch(Systemctl([(1, "")]), process=True).yield_to_app()


def test_restore_does_not_start_a_second_watcher():
    alive = {"now": False}
    runner = Systemctl(["active", "inactive"])
    watch = EjectWatch(runner, process_check=lambda: alive["now"], sleep=lambda _seconds: None)
    watch.yield_to_app()
    alive["now"] = True  # the old watcher survived the stop
    problem = watch.restore()
    assert "already running" in (problem or "")
    assert "start" not in runner.verbs()


# -- a unit that is not plainly active at the start ----------------------------------------


def test_a_unit_that_is_deactivating_is_waited_for_not_ignored():
    runner = Systemctl(["deactivating", "deactivating", "inactive"])
    watch = make_watch(runner)
    watch.yield_to_app()
    assert runner.verbs().count("is-active") == 3
    assert watch.restore() is None  # somebody else stopped it: leave it stopped
    assert "start" not in runner.verbs()


def test_a_unit_that_stays_deactivating_refuses():
    with pytest.raises(WatchStillActive):
        make_watch(Systemctl(["deactivating"])).yield_to_app()


def test_a_failed_unit_is_not_started_again_and_is_checked_for_a_process():
    runner = Systemctl(["failed"])
    watch = make_watch(runner)
    watch.yield_to_app()
    assert watch.restore() is None
    assert "stop" not in runner.verbs() and "start" not in runner.verbs()
    with pytest.raises(WatchStillActive):
        make_watch(Systemctl(["failed"]), process=True).yield_to_app()


# -- finding the process in /proc ----------------------------------------------------------


def _proc(root: Path, pid: str, *args: str) -> None:
    (root / pid).mkdir()
    (root / pid / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")


def test_finds_a_watch_process(tmp_path):
    _proc(tmp_path, "10", "python3", "-m", "of135i", "watch")
    assert watch_process_running(tmp_path)


def test_finds_the_console_script_form(tmp_path):
    _proc(tmp_path, "10", "/usr/bin/python3", "/home/x/bin/of135i", "watch")
    assert watch_process_running(tmp_path)


def test_ignores_other_commands(tmp_path):
    _proc(tmp_path, "10", "python3", "-m", "of135i", "load")
    _proc(tmp_path, "11", "bash", "-lc", "cd -- d && exec python3 -m of135i watch")
    _proc(tmp_path, "12", "tail", "-f", "watch")
    _proc(tmp_path, "13", "python3", "-m", "pytest", "of135i_watch")
    assert not watch_process_running(tmp_path)


def test_tolerates_unreadable_and_vanishing_entries(tmp_path):
    (tmp_path / "self").mkdir()  # not a pid
    (tmp_path / "20").mkdir()  # gone before its cmdline was read
    (tmp_path / "21").mkdir()
    (tmp_path / "21" / "cmdline").mkdir()  # reading it fails
    _proc(tmp_path, "22")  # a kernel thread: empty command line
    _proc(tmp_path, "23", "python3", "-m", "of135i", "watch")
    assert watch_process_running(tmp_path)


def test_an_unlistable_proc_fails_closed(tmp_path):
    assert watch_process_running(tmp_path / "missing")


@pytest.mark.parametrize(
    "args",
    [
        ("python3", "-mof135i", "watch"),
        ("python3", "-m", "of135i.__main__", "watch"),
        ("python3", "/d/of135i/__main__.py", "watch"),
        ("python3", "-m", "of135i", "--verbose", "watch"),
        ("python3", "-m", "of135i", "--poll", "0.5", "watch", "--once"),
        ("grep", "of135i", "watch"),  # fail-safe: an editor or grep on it also blocks
    ],
)
def test_finds_every_way_of_starting_a_watch(tmp_path, args):
    _proc(tmp_path, "10", *args)
    assert watch_process_running(tmp_path)


@pytest.mark.parametrize(
    "args",
    [
        ("python3", "-m", "of135i", "watchdog"),
        ("python3", "-m", "of135i", "--watch-me"),
        ("python3", "-m", "of135i"),
        ("python3", "watch", "of135i"),  # `watch` must come after the driver, not before
    ],
)
def test_ignores_commands_that_only_look_similar(tmp_path, args):
    _proc(tmp_path, "10", *args)
    assert not watch_process_running(tmp_path)


# -- wired into the controller ------------------------------------------------------------


class RecordingWatch:
    def __init__(self, events, refuse=False, problem=None):
        self.events = events
        self.refuse = refuse
        self.problem = problem

    def yield_to_app(self):
        self.events.append("yield")
        if self.refuse:
            raise WatchStillActive("the Eject-button watcher is still running")

    def restore(self):
        self.events.append("restore")
        return self.problem


def _batch(tmp_path, name="b"):
    BatchStore.create(tmp_path / name, name="T", defaults=BatchDefaults(dpi=600)).close()
    return tmp_path / name


def test_watcher_is_stopped_before_the_scanner_opens_and_restored_on_close(sweep600_dir, tmp_path):
    events: list[str] = []

    def factory(_dpi):
        events.append("open")
        return FakeScanner(sweep600_dir)

    c = Controller(factory, eject_watch=RecordingWatch(events))
    try:
        c.open_batch(_batch(tmp_path))
        wait_until(c, lambda: c.state().batch_name == "T")
        assert events[-2:] == ["yield", "open"]
        c.close_batch()
        wait_until(c, lambda: "restore" in events[2:])  # the batch name clears before it
        assert events[-3:] == ["yield", "open", "restore"]
    finally:
        c.shutdown()


def test_switching_batches_does_not_start_the_watcher_in_between(sweep600_dir, tmp_path):
    events: list[str] = []

    def factory(_dpi):
        events.append("open")
        return FakeScanner(sweep600_dir)

    first, second = _batch(tmp_path, "a"), _batch(tmp_path, "b")
    c = Controller(factory, eject_watch=RecordingWatch(events))
    try:
        c.open_batch(first)
        wait_until(c, lambda: events.count("open") == 1)
        c.open_batch(second)
        wait_until(c, lambda: events.count("open") == 2)
        assert "restore" not in events
        c.close_batch()
        wait_until(c, lambda: "restore" in events)
    finally:
        c.shutdown()


def test_watcher_is_restored_when_the_next_batch_cannot_be_read(sweep600_dir, tmp_path):
    events: list[str] = []
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir), eject_watch=RecordingWatch(events))
    try:
        c.open_batch(_batch(tmp_path))
        wait_until(c, lambda: events.count("yield") == 1)
        c.open_batch(tmp_path / "missing")
        wait_until(c, lambda: "restore" in events)
        wait_until(c, lambda: c.state().banner is not None)
    finally:
        c.shutdown()


def test_watcher_is_restored_when_a_read_only_batch_replaces_the_scanner_session(
    sweep600_dir, tmp_path
):
    events: list[str] = []
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir), eject_watch=RecordingWatch(events))
    try:
        c.open_batch(_batch(tmp_path, "a"))
        wait_until(c, lambda: events.count("yield") == 1)
        c.open_batch(_batch(tmp_path, "b"), readonly=True)
        wait_until(c, lambda: "restore" in events)
        assert events.count("yield") == 1
    finally:
        c.shutdown()


def test_read_only_batch_does_not_touch_the_watcher(sweep600_dir, tmp_path):
    events: list[str] = []
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir), eject_watch=RecordingWatch(events))
    try:
        c.open_batch(_batch(tmp_path), readonly=True)
        wait_until(c, lambda: c.state().batch_name == "T")
        assert "yield" not in events
    finally:
        c.shutdown()


def test_scanner_is_not_opened_while_the_watcher_still_runs(sweep600_dir, tmp_path):
    events: list[str] = []

    def factory(_dpi):
        events.append("open")
        return FakeScanner(sweep600_dir)

    c = Controller(factory, eject_watch=RecordingWatch(events, refuse=True))
    try:
        c.open_batch(_batch(tmp_path))
        wait_until(c, lambda: c.state().banner is not None)
        assert "open" not in events
        assert "still running" in c.state().banner.detail
        with BatchStore.open(tmp_path / "b"):  # the writer lock was released
            pass
    finally:
        c.shutdown()


def test_watcher_is_restored_when_the_scanner_fails_to_open(tmp_path):
    events: list[str] = []

    def broken(_dpi):
        raise OSError("no scanner backend")

    c = Controller(broken, eject_watch=RecordingWatch(events))
    try:
        c.open_batch(_batch(tmp_path))
        wait_until(c, lambda: c.state().banner is not None)
        assert events[-2:] == ["yield", "restore"]
    finally:
        c.shutdown()


def test_failed_restore_shows_a_banner(sweep600_dir, tmp_path):
    events: list[str] = []
    watch = RecordingWatch(events, problem="The Eject button watcher did not restart.")
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir), eject_watch=watch)
    try:
        c.open_batch(_batch(tmp_path))
        wait_until(c, lambda: c.state().batch_name == "T")
        c.close_batch()
        wait_until(c, lambda: c.state().banner is not None)
        assert "did not restart" in c.state().banner.message
    finally:
        c.shutdown()


def test_a_refused_open_does_not_start_a_second_watcher(sweep600_dir, tmp_path):
    runner = Systemctl(["active", "inactive"])
    watch = EjectWatch(runner, process_check=lambda: True, sleep=lambda _seconds: None)
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir), eject_watch=watch)
    try:
        c.open_batch(_batch(tmp_path))
        wait_until(c, lambda: c.state().banner is not None)
        assert "start" not in runner.verbs()
    finally:
        c.shutdown()


def test_a_failed_restore_after_a_read_only_open_stays_on_screen(sweep600_dir, tmp_path):
    events: list[str] = []
    watch = RecordingWatch(events, problem="The Eject button watcher did not restart.")
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir), eject_watch=watch)
    try:
        c.open_batch(_batch(tmp_path, "a"))
        wait_until(c, lambda: events.count("yield") == 1)
        c.open_batch(_batch(tmp_path, "b"), readonly=True)
        wait_until(c, lambda: c.state().batch_name == "T" and c.state().readonly)
        assert c.state().banner is not None and "did not restart" in c.state().banner.message
    finally:
        c.shutdown()


def _eject_then_close(sweep600_dir, tmp_path, events):
    gate = threading.Event()
    scanner = RecordingScanner(sweep600_dir, (ScannerStatus.NOT_READY,), gate=gate)
    c = Controller(lambda _dpi: scanner, eject_watch=RecordingWatch(events))
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    c.eject_holder()
    wait_until(c, lambda: scanner.calls == ["eject"])  # the move is running now
    c.close_batch()
    return c, gate


def test_the_watcher_is_not_started_while_the_holder_is_moving(sweep600_dir, tmp_path, monkeypatch):
    monkeypatch.setattr("opticfilm_linux.app.controller.WATCH_RESTORE_WAIT_S", 30.0)
    events: list[str] = []
    c, gate = _eject_then_close(sweep600_dir, tmp_path, events)
    try:
        time.sleep(0.5)
        assert "restore" not in events  # the move is still running
        gate.set()
        wait_until(c, lambda: "restore" in events)
    finally:
        gate.set()
        c.shutdown()


def test_a_move_that_outlasts_the_wait_leaves_the_watcher_stopped_with_a_banner(
    sweep600_dir, tmp_path, monkeypatch
):
    monkeypatch.setattr("opticfilm_linux.app.controller.WATCH_RESTORE_WAIT_S", 0.3)
    events: list[str] = []
    c, gate = _eject_then_close(sweep600_dir, tmp_path, events)
    try:
        wait_until(c, lambda: c.state().banner is not None)
        assert "stopped" in c.state().banner.message and UNIT in c.state().banner.message
        gate.set()
        time.sleep(0.3)
        assert "restore" not in events
    finally:
        gate.set()
        c.shutdown()


# -- the status poller's request in flight ---------------------------------------------------


class _BlockedStatus:
    def __init__(self) -> None:
        self.release = threading.Event()

    def status(self):
        self.release.wait(30)
        return ScannerStatus.READY


def test_wait_idle_covers_a_request_still_out():
    scanner = _BlockedStatus()
    poller = StatusPoller(scanner, time.monotonic, 30.0, lambda _m, _d: None)
    assert poller.wait_idle(0.1)  # nothing asked yet
    poller.poll()
    assert not poller.wait_idle(0.1)
    scanner.release.set()
    assert poller.wait_idle(5.0)
