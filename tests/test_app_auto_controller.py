"""Automatic jobs use the jobs worker without entering it under the state lock."""

import dataclasses
import threading

import pytest

from controller_helpers import (
    make_controller,
    open_new,
    scan_when_ready,
    wait_all_in_review,
    wait_until,
)
from opticfilm_linux.app import controller as controller_module
from opticfilm_linux.app.state import FreeSpaceView
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
    c.new_batch(
        tmp_path / "batch",
        "Test",
        "",
        None,
        600,
        auto_export=True,
        export_dir=str(tmp_path / "album"),
    )
    wait_until(c, lambda: c.state().batch_name == "Test")
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
        c.new_batch(
            tmp_path / "batch",
            "Test",
            "",
            None,
            600,
            auto_export=True,
            export_dir=str(tmp_path / "album"),
        )
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
        c.new_batch(
            tmp_path / "on",
            "Test",
            "",
            None,
            600,
            auto_export=True,
            keep_raw=True,
            export_dir=str(tmp_path / "album"),
        )
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
    monkeypatch.setattr(auto, "release_first_tray", _record_test_release)
    monkeypatch.setattr(auto, "cleanup_raw", cleanup)
    c = make_controller(sweep600_dir)
    try:
        c.new_batch(
            tmp_path / "batch",
            "Test",
            "",
            None,
            600,
            auto_export=True,
            export_dir=str(tmp_path / "album"),
        )
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


def test_manual_free_space_does_not_release_automatic_backlog(sweep600_dir, tmp_path, monkeypatch):
    c = make_controller(sweep600_dir)
    calls = []

    class Pipeline:
        def __init__(self, store, **kwargs):
            self.store = store

        def resume(self):
            return (2,) if self.store.snapshot().first_tray_released else ()

        def arm(self, number):
            return False

        def run(self, number):
            calls.append(number)

    monkeypatch.setattr(controller_module, "run_free_space", _confirmed_first_cleanup)
    monkeypatch.setattr(controller_module, "AutoPipeline", Pipeline)
    try:
        c.new_batch(
            tmp_path / "batch",
            "Test",
            "",
            None,
            600,
            auto_export=True,
            export_dir=str(tmp_path / "album"),
        )
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
        c._set(_free_space=FreeSpaceView(((1, 10),), 10))
        c.confirm_free_space()
        wait_until(
            c,
            lambda: c.state().free_space is not None and c.state().free_space.freed == 10,
            timeout=2,
        )
        assert calls == [] and not c._session.store.snapshot().first_tray_released
    finally:
        c.shutdown()


def test_open_resumes_automatic_pipeline_without_controller_lock(
    sweep600_dir, tmp_path, monkeypatch
):
    c = make_controller(sweep600_dir)
    resumed = threading.Event()

    class Pipeline:
        def __init__(self, store, cancel=None, on_update=None):
            self.on_update = on_update

        def resume(self):
            assert c._lock.acquire(timeout=2), "resume ran under controller lock"
            c._lock.release()
            self.on_update()  # rebuilding state must also be safe during resume
            resumed.set()
            return ()

    monkeypatch.setattr(controller_module, "AutoPipeline", Pipeline)
    try:
        open_new(c, tmp_path / "batch")
        assert resumed.wait(2)
        c._command.submit(lambda: None).result(2)
        assert c._lifecycle_pending == 0
    finally:
        c.shutdown()


def test_preview_file_access_does_not_hold_controller_lock(sweep600_dir, tmp_path, monkeypatch):
    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "batch")
        original = c._preview_tokens
        checked = threading.Event()

        def preview_tokens(session, manifest, selected):
            assert c._lock.acquire(timeout=2), "preview file access ran under state lock"
            c._lock.release()
            checked.set()
            return original(session, manifest, selected)

        monkeypatch.setattr(c, "_preview_tokens", preview_tokens)
        c._rebuild()
        assert checked.is_set()
    finally:
        c.shutdown()


def test_manual_review_does_not_queue_automatic_work_or_block_rescan(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)
    started, release = threading.Event(), threading.Event()

    def blocked_job():
        started.set()
        assert release.wait(30)

    try:
        open_new(c, tmp_path / "batch")
        assert not c._session.store.snapshot().defaults.auto_export
        c._run(blocked_job, "stand-in jobs worker", pool=c._jobs)
        assert started.wait(2)
        scan_when_ready(c)
        wait_all_in_review(c, 1)
        assert 1 not in c._auto_active
        assert c._long_jobs == 0
        assert len(c._job_futures) == 1  # only the explicitly blocked stand-in
        c.rescan(1)
        second = tmp_path / "batch" / "trays" / "0001" / "sweep-2"
        wait_until(c, second.is_dir)
        wait_all_in_review(c, 1)
        assert (second.parent / "sweep-1").is_dir()
    finally:
        release.set()
        c.shutdown()


def test_auto_scheduling_never_reads_manifest_on_ui_thread(sweep600_dir, tmp_path, monkeypatch):
    c = make_controller(sweep600_dir)
    queued = []
    try:
        c.new_batch(
            tmp_path / "batch",
            "Test",
            "",
            None,
            600,
            auto_export=True,
            export_dir=str(tmp_path / "album"),
        )
        wait_until(c, lambda: c.state().batch_name == "Test")
        c._command.submit(lambda: None).result(2)
        c._refresher.submit(lambda: None).result(2)

        def forbidden_snapshot():
            raise AssertionError("automatic scheduling read the manifest on the UI thread")

        monkeypatch.setattr(c._session.store, "snapshot", forbidden_snapshot)
        monkeypatch.setattr(c, "_queue_auto", lambda session, number: queued.append(number))
        c._submit_auto_due()  # idle timer ticks must remain free of store access
        with c._lock:
            c._auto_due.add(1)
        c._submit_auto_due()  # review events use the cached batch option too
        assert queued == [1]
        assert c._auto_active == {1}
    finally:
        monkeypatch.undo()
        c.shutdown()


@pytest.mark.parametrize("destination", ["", "   ", "batch", "file"])
def test_auto_create_requires_usable_destination(sweep600_dir, tmp_path, destination):
    c = make_controller(sweep600_dir)
    folder = tmp_path / "batch"
    (tmp_path / "file").write_text("occupied")
    export_dir = str(tmp_path / destination) if destination.strip() else destination
    try:
        c.new_batch(folder, "Test", "", None, 600, auto_export=True, export_dir=export_dir)
        wait_until(c, lambda: c.state().banner is not None, timeout=2)
        assert c.state().batch_name is None
        assert not folder.exists()
        assert "folder" in c.state().banner.message.lower()
    finally:
        c.shutdown()


@pytest.mark.parametrize("damaged", [False, True])
def test_setting_destination_retries_only_missing_destination_failure(
    sweep600_dir, tmp_path, monkeypatch, damaged
):
    from opticfilm_linux.app import auto

    c = make_controller(sweep600_dir)
    verified = []

    def verify(store, number):
        verified.append(number)
        if damaged:
            raise RuntimeError("verification failed")

    monkeypatch.setattr(auto, "verify_tray_export", verify)
    monkeypatch.setattr(auto, "cleanup_raw", lambda *a, **kw: pytest.fail("raw cleanup attempted"))
    try:
        c.new_batch(
            tmp_path / "batch",
            "Test",
            "",
            None,
            600,
            auto_export=True,
            export_dir=str(tmp_path / "album"),
        )
        wait_until(c, lambda: c.state().batch_name == "Test")
        store = c._session.store
        _fail_without_destination(store, auto)
        c.set_export_settings(tmp_path / "album", "jpeg", "full")
        expected = "failed" if damaged else "held"
        wait_until(
            c, lambda: verified == [1] and store.snapshot().trays[0].auto == expected, timeout=2
        )
        assert verified == [1]
        trays = store.snapshot().trays
        assert all(t.auto == "failed" for t in trays[1:])
        assert all(not t.raw_removed for t in (trays[0], trays[1]))
        if damaged:
            assert "verification failed" in trays[0].auto_error
        else:
            assert trays[0].auto_error == "" and trays[0].auto_failed == ()
    finally:
        c.shutdown()


def _fail_without_destination(store, auto):
    store.update(
        lambda m: dataclasses.replace(
            m,
            defaults=dataclasses.replace(m.defaults, export_dir=""),
            trays=(TrayRecord(1, TrayStage.REVIEW, auto="pending"),),
        )
    )
    auto.AutoPipeline(store).run(1)
    missing = store.snapshot().trays[0]
    assert missing.auto == "failed" and "Choose an album folder" in missing.auto_error
    store.update(
        lambda m: dataclasses.replace(
            m,
            trays=(
                missing,
                dataclasses.replace(missing, number=2, auto_error="verification failed"),
                dataclasses.replace(missing, number=3, raw_removed=True),
                dataclasses.replace(missing, number=4, stage=TrayStage.FAILED),
            ),
        )
    )


def test_invalid_destination_settings_leave_missing_destination_failure(sweep600_dir, tmp_path):
    from opticfilm_linux.app import auto

    c = make_controller(sweep600_dir)
    try:
        c.new_batch(
            tmp_path / "batch",
            "Test",
            "",
            None,
            600,
            auto_export=True,
            export_dir=str(tmp_path / "album"),
        )
        wait_until(c, lambda: c.state().batch_name == "Test")
        store = c._session.store
        _fail_without_destination(store, auto)
        before = store.snapshot()
        invalid = tmp_path / "file"
        invalid.write_text("occupied")
        c.set_export_settings(invalid, "jpeg", "full")
        wait_until(c, lambda: c.state().banner is not None, timeout=2)
        assert store.snapshot() == before
    finally:
        c.shutdown()


def _confirmed_first_cleanup(store, cancel, publish, numbers, *, release_first=False):
    assert not release_first
    store.update(
        lambda m: dataclasses.replace(
            m,
            trays=(dataclasses.replace(m.trays[0], auto="freed", raw_removed=True), m.trays[1]),
        )
    )
    publish(FreeSpaceView(((1, 10),), 10, freed=10))


def test_disabled_auto_still_dispatches_committed_cleanup(sweep600_dir, tmp_path, monkeypatch):
    called = threading.Event()

    class Pipeline:
        def __init__(self, store, **kwargs):
            self.store = store

        def resume(self):
            return (1, 2)

        def arm(self, number):
            return False

        def run(self, number):
            assert number == 1
            called.set()

    monkeypatch.setattr(controller_module, "AutoPipeline", Pipeline)
    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "batch")
        store = c._session.store
        store.update(
            lambda m: dataclasses.replace(
                m,
                trays=(
                    TrayRecord(1, TrayStage.EXPORTED, auto="verified", raw_removed=True),
                    TrayRecord(2, TrayStage.REVIEW, auto="pending"),
                ),
            )
        )
        c._auto_due.update((1, 2))
        c._submit_auto_due()
        assert called.wait(3)
    finally:
        c.shutdown()


def _record_test_release(store, number):
    store.update(
        lambda m: dataclasses.replace(
            m,
            first_tray_released=True,
            released_render=dataclasses.replace(m.defaults, auto_export=False, keep_raw=False),
        )
    )


def test_manual_free_cancel_does_not_cancel_automatic_job(auto_controller):
    c, calls, started, release = auto_controller
    c._session.runner.events.put(TrayStageChanged(1, TrayStage.REVIEW))
    c._drain_events()
    assert started.wait(2)
    c.cancel_free_space()
    assert c._cancel_free.is_set()
    assert not calls[0][2].is_set()
    release.set()


def test_owner_decision_requeues_automatic_tray(auto_controller):
    from opticfilm_linux.batch.model import SlideRecord, SlideStatus

    c, calls, started, release = auto_controller
    c._session.store.update_tray(
        1, auto="blocked", slides=(SlideRecord(1, status=SlideStatus.NEEDS_LOOK),)
    )
    c.set_status(1, 1, SlideStatus.ACCEPTED)
    assert started.wait(2), "owner decision did not queue the blocked automatic tray"
    assert calls[0][0] == 1
    release.set()
