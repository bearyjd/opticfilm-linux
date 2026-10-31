"""The app stops the background Eject-button watcher before it opens the scanner."""

import subprocess

import pytest

from controller_helpers import wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.eject_watch import UNIT, EjectWatch, WatchStillActive
from opticfilm_linux.batch.model import BatchDefaults
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.fake import FakeScanner


class Systemctl:
    """A scripted ``systemctl``: ``states`` are the answers to successive ``is-active`` calls."""

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
            return 0, self.states.pop(0) + "\n"
        return (self.stop_rc if verb == "stop" else self.start_rc), ""

    def verbs(self):
        return [c[1] for c in self.calls]


def test_active_unit_is_stopped_then_started_again():
    runner = Systemctl(["active", "inactive"])
    watch = EjectWatch(runner)
    watch.yield_to_app()
    assert runner.verbs() == ["is-active", "stop", "is-active"]
    assert runner.calls[1] == ("--user", "stop", UNIT)
    assert watch.restore() is None
    assert runner.calls[-1] == ("--user", "start", UNIT)


def test_inactive_or_missing_unit_is_left_alone():
    runner = Systemctl(["inactive"])
    watch = EjectWatch(runner)
    watch.yield_to_app()
    assert watch.restore() is None
    assert runner.verbs() == ["is-active"]


def test_missing_systemctl_is_a_quiet_no_op():
    runner = Systemctl([], missing=True)
    watch = EjectWatch(runner)
    watch.yield_to_app()
    assert watch.restore() is None


def test_restore_twice_starts_only_once():
    runner = Systemctl(["active", "inactive"])
    watch = EjectWatch(runner)
    watch.yield_to_app()
    watch.restore()
    watch.restore()
    assert runner.verbs().count("start") == 1


def test_a_retrying_unit_counts_as_active():
    runner = Systemctl(["activating", "inactive"])
    watch = EjectWatch(runner)
    watch.yield_to_app()
    assert "stop" in runner.verbs()
    watch.restore()
    assert "start" in runner.verbs()


def test_refuses_when_still_active_after_the_stop():
    runner = Systemctl(["active", "active"])
    with pytest.raises(WatchStillActive):
        EjectWatch(runner).yield_to_app()


def test_refuses_when_still_deactivating():
    runner = Systemctl(["active", "deactivating"])
    with pytest.raises(WatchStillActive):
        EjectWatch(runner).yield_to_app()


def test_failed_stop_is_fine_when_the_unit_is_down_anyway():
    runner = Systemctl(["active", "inactive"], stop_rc=1)
    EjectWatch(runner).yield_to_app()


def test_stop_timeout_with_unit_still_active_refuses():
    runner = Systemctl(["active", "active"], timeout_on="stop")
    with pytest.raises(WatchStillActive):
        EjectWatch(runner).yield_to_app()


def test_unknown_state_after_stop_refuses():
    runner = Systemctl(["active", "weird"])
    with pytest.raises(WatchStillActive):
        EjectWatch(runner).yield_to_app()


def test_failed_start_is_reported_not_raised():
    runner = Systemctl(["active", "inactive"], start_rc=1)
    watch = EjectWatch(runner)
    watch.yield_to_app()
    assert "Eject button" in (watch.restore() or "")


def test_start_timeout_is_reported_not_raised():
    runner = Systemctl(["active", "inactive"], timeout_on="start")
    watch = EjectWatch(runner)
    watch.yield_to_app()
    assert watch.restore() is not None


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


def _batch(tmp_path):
    BatchStore.create(tmp_path / "b", name="T", defaults=BatchDefaults(dpi=600)).close()
    return tmp_path / "b"


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
        wait_until(c, lambda: not c.state().batch_name)
        assert events[-3:] == ["yield", "open", "restore"]
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
