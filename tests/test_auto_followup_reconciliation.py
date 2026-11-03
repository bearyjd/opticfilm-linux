"""Cleanup reconciliation and controller feedback preserve lock and deletion boundaries."""

import dataclasses
import threading
from types import SimpleNamespace

import pytest

from opticfilm_linux.app import commands
from opticfilm_linux.app.auto import AutoPipeline
from opticfilm_linux.batch.model import TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchStore


@pytest.mark.parametrize("session_changes", [False, True])
def test_cleanup_retry_checks_disk_outside_lock_and_revalidates_session(
    monkeypatch, session_changes
):
    controller = commands.ReviewCommands()
    controller._lock = threading.Lock()
    session = SimpleNamespace(store=object())
    controller._session = session
    controller._auto_due = set()
    controller._auto_retried = set()
    tray = TrayRecord(1, TrayStage.EXPORTED, auto="verified", auto_error="retry cleanup")

    def evidence(store, inspected):
        assert store is session.store and inspected is tray
        assert controller._lock.acquire(blocking=False), "disk lookup held the state lock"
        try:
            if session_changes:
                controller._session = SimpleNamespace(store=object())
        finally:
            controller._lock.release()
        return False

    monkeypatch.setattr(commands, "cleanup_pending", evidence)
    controller._retry_auto_cleanup(session, tray)
    expected = set() if session_changes else {1}
    assert controller._auto_due == expected
    assert controller._auto_retried == expected


def test_busy_held_release_publishes_actionable_banner_outside_lock(monkeypatch):
    controller = commands.ReviewCommands()
    controller._lock = threading.Lock()
    controller._session = SimpleNamespace(runner=object())
    controller._auto_active = {1}
    published, queued = [], []

    def publish(**changes):
        assert controller._lock.acquire(blocking=False), "banner publication reentered the lock"
        controller._lock.release()
        published.append(changes["_banner"])

    monkeypatch.setattr(controller, "_set", publish, raising=False)
    monkeypatch.setattr(controller, "_queue_auto", lambda *a, **kw: queued.append(a))
    controller.confirm_free_held(1)
    assert len(published) == 1
    assert "Tray 1" in published[0].message
    assert "wait" in published[0].message.lower()
    assert "again" in published[0].message
    assert not queued and controller._auto_active == {1}


@pytest.mark.parametrize("auto", ["pending", "exported", "verified", "held", "blocked", "failed"])
@pytest.mark.parametrize("folders", [False, True])
def test_resume_reconciles_every_stale_cleanup_state(tmp_path, auto, folders):
    with BatchStore.create(tmp_path / "batch", name="Reconcile") as store:
        tray = TrayRecord(1, TrayStage.EXPORTED, auto=auto, raw_removed=True, auto_error="raw kept")
        store.update(lambda m: dataclasses.replace(m, trays=(tray,)))
        if folders:
            store.sweep_dir(1, 1).mkdir(parents=True)
        updated = []
        pipeline = AutoPipeline(store, on_update=lambda: updated.append(True))
        assert pipeline.resume() == ()
        current = store.snapshot().trays[0]
        assert current.auto == (auto if folders else "freed")
        assert current.auto_error == ("raw kept" if folders else "")
        assert current.raw_removed
        assert updated == ([] if folders else [True])


@pytest.mark.parametrize("auto", ["", "off", "freed"])
def test_resume_leaves_excluded_cleanup_states_unchanged(tmp_path, auto):
    with BatchStore.create(tmp_path / "batch", name="Unchanged") as store:
        tray = TrayRecord(
            1, TrayStage.EXPORTED, auto=auto, raw_removed=True, auto_error="unchanged"
        )
        store.update(lambda m: dataclasses.replace(m, trays=(tray,)))
        assert AutoPipeline(store).resume() == ()
        assert store.snapshot().trays == (tray,)
