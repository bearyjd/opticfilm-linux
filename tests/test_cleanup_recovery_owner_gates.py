"""Interrupted removal keeps owner recovery available and records inspection failures."""

from types import SimpleNamespace

import pytest

from opticfilm_linux.app import auto as automatic
from opticfilm_linux.app.commands import ReviewCommands
from opticfilm_linux.batch import export
from opticfilm_linux.batch.cleanup_recovery import can_recover_interrupted_cleanup
from test_app_auto import pipeline

pytest_plugins = ("test_app_auto", "test_export_owner_recovery")


@pytest.mark.parametrize("older", ["empty", "partial", "corrupt"])
def test_recovery_preserves_partial_older_sweeps_and_requires_fresh_exports(interrupted, older):
    store, written = interrupted
    previous = store.sweep_dir(1, 0)
    previous.mkdir()
    if older != "empty":
        (previous / "rgb.npy").write_bytes(b"surviving scan bytes")
    if older == "corrupt":
        (previous / "meta.json").write_bytes(b"interrupted metadata")
    store.update_tray(1, cleanup_sweeps=(0, 1))
    before = {path: path.read_bytes() for path in previous.iterdir()}
    manifest = store.snapshot()
    assert can_recover_interrupted_cleanup(store, manifest, manifest.trays[0])
    export.recover_interrupted_cleanup(store, 1)
    assert {path: path.read_bytes() for path in previous.iterdir()} == before
    assert store.sweep_dir(1, 1).is_dir()
    with pytest.raises(export.CleanupRefused):
        export.cleanup_raw(store, 1)
    assert previous.is_dir()
    export.export_batch(store, written[0].parent, "jpeg")
    export.cleanup_raw(store, 1, release_first=True)
    assert not previous.exists()
    assert not store.sweep_dir(1, 1).exists()


@pytest.mark.parametrize("state", ["verified", "failed", "held", "exported", "pending"])
def test_automatic_paths_preserve_interrupted_cleanup_until_owner_acts(store, monkeypatch, state):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    store.update_tray(1, auto=state, raw_removed=True, cleanup_sweeps=(1,))
    before = store.snapshot().trays[0]
    monkeypatch.setattr(automatic, "cleanup_raw", lambda *_a, **_k: pytest.fail("deleted raw"))
    assert 1 not in auto.resume()
    assert 1 not in auto.retry_completed_exports()
    assert 1 not in auto.retry_missing_destination()
    auto.run(1)
    auto._verify_and_finish(1)
    auto._finish_verified(1, release=True)
    assert store.snapshot().trays[0] == before
    assert store.sweep_dir(1, 1).is_dir()
    manifest = store.snapshot()
    assert can_recover_interrupted_cleanup(store, manifest, manifest.trays[0])


@pytest.mark.parametrize("entry", ["run", "confirm", "verify"])
@pytest.mark.parametrize("transient", [False, True])
def test_inventory_failure_records_failed_progress_and_original_error(
    store, monkeypatch, entry, transient
):
    auto = pipeline(store)
    auto.arm(1)
    if entry == "confirm":
        auto.run(1)
    before = store.snapshot().trays[0]
    original = automatic.raw_inventory
    calls = []

    def fail_inventory(*args):
        calls.append(True)
        if transient and len(calls) > 1:
            return original(*args)
        raise OSError("initial raw inspection failed")

    monkeypatch.setattr(automatic, "raw_inventory", fail_inventory)
    action = {"run": auto.run, "confirm": auto.confirm_free_held, "verify": auto._verify_and_finish}
    action[entry](1)
    after = store.snapshot().trays[0]
    assert after.auto == "failed"
    assert "initial raw inspection failed" in after.auto_error
    assert "outcome unknown" in after.auto_error
    assert "raw data kept" not in after.auto_error
    assert after.slides == before.slides and not after.raw_removed
    assert store.sweep_dir(1, 1).exists()


def test_cleanup_retry_failure_after_tombstone_does_not_rearm_auto(store):
    auto = pipeline(store)
    store.update_tray(1, auto="verified", raw_removed=True, cleanup_sweeps=(1,))
    auto._failed(1, export.CleanupRetryRequired("owner recovery needed"), before={})
    assert auto._tray(1).auto == "failed"
    assert 1 not in auto.resume()
    assert "owner recovery needed" in auto._tray(1).auto_error


def test_controller_does_not_retry_tombstoned_remaining_raw(store):
    import threading

    session = SimpleNamespace(store=store)
    controller = SimpleNamespace(
        _session=session, _lock=threading.Lock(), _auto_retried=set(), _auto_due=set()
    )
    store.update_tray(1, auto="verified", auto_error="retry cleanup", raw_removed=True)
    ReviewCommands._retry_auto_cleanup(controller, session, store.snapshot().trays[0])
    assert controller._auto_due == set()
    assert controller._auto_retried == set()


def test_manual_controller_filter_excludes_remaining_raw_on_worker(store):
    import threading

    queued = []
    session = SimpleNamespace(store=store, runner=object(), auto_export=False)
    store.update_tray(1, auto="verified", raw_removed=True)
    controller = SimpleNamespace(
        _session=session, _lock=threading.Lock(), _auto_due={1}, _cancel_auto=threading.Event()
    )
    controller._run_writer = lambda job, *_: job(None)
    controller._schedule_auto = lambda _session, due: queued.extend(due)
    ReviewCommands._submit_auto_due(controller)
    assert not queued
