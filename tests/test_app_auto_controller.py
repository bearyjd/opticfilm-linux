"""Automatic jobs use the jobs worker without entering it under the state lock."""

import dataclasses
import threading

import pytest

from controller_helpers import make_controller, open_new, wait_until
from opticfilm_linux.app import controller as controller_module
from opticfilm_linux.batch.events import TrayStageChanged
from opticfilm_linux.batch.model import TrayRecord, TrayStage


@pytest.fixture
def auto_controller(sweep600_dir, tmp_path, monkeypatch):
    calls = []
    started = threading.Event()
    release = threading.Event()

    class Pipeline:
        def __init__(self, store, cancel=None, on_update=None):
            self.cancel = cancel
            self.on_update = on_update

        def arm(self, number):
            return number == 1

        def resume(self):
            return ()

        def run(self, number):
            calls.append((number, threading.current_thread().name, self.cancel))
            started.set()
            assert release.wait(5)

        def confirm_free_held(self, number):
            calls.append(("release", number))

    monkeypatch.setattr(controller_module, "AutoPipeline", Pipeline, raising=False)
    c = make_controller(sweep600_dir)
    open_new(c, tmp_path / "batch")
    c._session.store.update(
        lambda m: dataclasses.replace(m, trays=(TrayRecord(1, TrayStage.REVIEW, "positive"),))
    )
    yield c, calls, started, release
    release.set()
    c.shutdown()


def test_review_event_starts_auto_job_after_releasing_lock_without_deadlock(auto_controller):
    c, calls, started, release = auto_controller

    # Exercise _apply_event under exactly the non-reentrant lock used by event draining.
    def drain():
        with c._lock:
            c._apply_event(TrayStageChanged(1, TrayStage.REVIEW))
        c._drain_events()

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    thread.join(2)
    assert not thread.is_alive(), "auto submission re-entered the controller lock"
    assert started.wait(2), "review did not start an automatic job"
    assert calls[0][0] == 1
    assert calls[0][1].startswith("app-jobs")


def test_manual_export_cancel_does_not_cancel_automatic_job(auto_controller):
    c, calls, started, release = auto_controller
    c._session.runner.events.put(TrayStageChanged(1, TrayStage.REVIEW))
    c._drain_events()
    assert started.wait(2)
    c.cancel_export()
    assert c._cancel_export.is_set()
    assert not calls[0][2].is_set()
    release.set()


def test_close_sets_auto_cancel_and_waits_for_running_job(auto_controller):
    c, calls, started, release = auto_controller
    c._session.runner.events.put(TrayStageChanged(1, TrayStage.REVIEW))
    c._drain_events()
    assert started.wait(2)
    c.close_batch()
    assert calls[0][2].wait(2), "close did not cancel automatic export"
    assert c._session is not None
    release.set()
    wait_until(c, lambda: c.state().batch_name is None, timeout=3)


def test_rescan_of_tray_with_automatic_job_is_refused(auto_controller):
    c, calls, started, release = auto_controller
    c._session.runner.events.put(TrayStageChanged(1, TrayStage.REVIEW))
    c._drain_events()
    assert started.wait(2)
    c.rescan(1)
    wait_until(c, lambda: c.state().banner is not None, timeout=3)
    assert "automatic" in c.state().banner.message.lower()
    assert c._session.store.snapshot().trays[0].stage is TrayStage.REVIEW


def test_auto_resumes_incomplete_tray_on_reopen(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)
    try:
        c.new_batch(tmp_path / "batch", "Test", "", None, 600, auto_export=True)
        wait_until(c, lambda: c.state().batch_name == "Test")
        c._session.store.update(
            lambda m: dataclasses.replace(
                m, trays=(TrayRecord(1, TrayStage.REVIEW, auto="verified"),)
            )
        )
        c.close_batch()
        wait_until(c, lambda: c.state().batch_name is None)
        c.open_batch(tmp_path / "batch")
        wait_until(c, lambda: bool(c.state().trays) and c.state().trays[0].auto == "failed")
        assert not c._session.store.snapshot().trays[0].raw_removed
        assert "failed" in c.state().banner.message
    finally:
        c.shutdown()


def test_auto_setting_is_persisted_per_batch_and_defaults_off(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "off")
        assert not c._session.store.snapshot().defaults.auto_export
        c.new_batch(tmp_path / "on", "Test", "", None, 600, auto_export=True, keep_raw=True)
        wait_until(
            c, lambda: c._session is not None and c._session.store.directory == tmp_path / "on"
        )
        defaults = c._session.store.snapshot().defaults
        assert defaults.auto_export and defaults.keep_raw
    finally:
        c.shutdown()


def test_queued_auto_job_also_prevents_same_tray_rescan(auto_controller):
    c, calls, started, release = auto_controller
    gate = threading.Event()
    c._run(lambda: gate.wait(5), "job already running", pool=c._jobs)
    try:
        c._session.runner.events.put(TrayStageChanged(1, TrayStage.REVIEW))
        c._drain_events()
        c.rescan(1)
        wait_until(c, lambda: c.state().banner is not None, timeout=2)
        assert "automatic" in c.state().banner.message.lower()
        assert not started.is_set()
    finally:
        gate.set()
        release.set()


def test_releasing_first_tray_schedules_verified_held_backlog(sweep600_dir, tmp_path, monkeypatch):
    from opticfilm_linux.app import auto

    cleaned = []
    verified = []

    def verify(store, number):
        verified.append(number)

    def cleanup(store, number, *, release_first=False):
        cleaned.append((number, threading.current_thread().name))
        store.update_tray(number, raw_removed=True)
        if release_first:
            store.update(lambda m: dataclasses.replace(m, first_tray_released=True))

    monkeypatch.setattr(auto, "verify_tray_export", verify)
    monkeypatch.setattr(auto, "cleanup_raw", cleanup)
    c = make_controller(sweep600_dir)
    try:
        c.new_batch(tmp_path / "batch", "Test", "", None, 600, auto_export=True)
        wait_until(c, lambda: c.state().batch_name == "Test")
        c._session.store.update(
            lambda m: dataclasses.replace(
                m,
                trays=(
                    TrayRecord(1, TrayStage.EXPORTED, auto="held"),
                    TrayRecord(2, TrayStage.EXPORTED, auto="held"),
                ),
            )
        )
        c.confirm_free_held(1)
        wait_until(c, lambda: c._session.store.snapshot().first_tray_released, timeout=2)
        wait_until(c, lambda: c._session.store.snapshot().trays[1].raw_removed, timeout=2)
        assert [number for number, _ in cleaned] == [1, 2]
        assert all(name.startswith("app-jobs") for _, name in cleaned)
        assert verified.count(2) >= 1
    finally:
        c.shutdown()


def test_stale_auto_callback_cannot_clear_reopened_session_reservation(
    sweep600_dir, tmp_path, monkeypatch
):
    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "batch")
        old = c._session
        captured = []
        monkeypatch.setattr(c, "_after_pending_commands", lambda job, message: captured.append(job))
        c._queue_auto(old, 1)
        c.close_batch()
        wait_until(c, lambda: c.state().batch_name is None)
        c.open_batch(tmp_path / "batch")
        wait_until(c, lambda: c.state().batch_name == "Test")
        assert c._session is not old
        with c._lock:
            c._auto_active.add(1)
        captured[0](c._session.runner)
        assert 1 in c._auto_active
    finally:
        c.shutdown()


def test_manual_first_hold_release_resumes_backlog(sweep600_dir, tmp_path, monkeypatch):
    from opticfilm_linux.app.state import FreeSpaceView

    c = make_controller(sweep600_dir)
    calls = []

    def confirmed_cleanup(store, cancel, publish, numbers, *, release_first=False):
        assert release_first
        store.update(
            lambda m: dataclasses.replace(
                m,
                first_tray_released=True,
                trays=(dataclasses.replace(m.trays[0], auto="freed", raw_removed=True), m.trays[1]),
            )
        )
        publish(FreeSpaceView(((1, 10),), 10, freed=10))

    class Pipeline:
        def __init__(self, store, **kwargs):
            self.store = store

        def resume(self):
            return (2,) if self.store.snapshot().first_tray_released else ()

        def arm(self, number):
            return False

        def run(self, number):
            calls.append(number)

    monkeypatch.setattr(controller_module, "run_free_space", confirmed_cleanup)
    monkeypatch.setattr(controller_module, "AutoPipeline", Pipeline)
    try:
        open_new(c, tmp_path / "batch")
        c._session.store.update(
            lambda m: dataclasses.replace(
                m,
                defaults=dataclasses.replace(m.defaults, auto_export=True),
                trays=(
                    TrayRecord(1, TrayStage.EXPORTED, auto="held"),
                    TrayRecord(2, TrayStage.EXPORTED, auto="held"),
                ),
            )
        )
        c._set(_free_space=FreeSpaceView(((1, 10),), 10))
        c.confirm_free_space()
        wait_until(
            c,
            lambda: c.state().free_space is not None and c.state().free_space.freed == 10,
            timeout=2,
        )
        wait_until(c, lambda: calls == [2], timeout=0.5)
    finally:
        c.shutdown()
