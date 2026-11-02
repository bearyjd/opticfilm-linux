import dataclasses
import errno
import os
import stat

import pytest

from opticfilm_linux.batch import export as exports
from test_app_auto import pipeline

pytest_plugins = ("test_app_auto",)


@pytest.mark.parametrize("fault", [errno.ENOSPC, errno.EACCES])
def test_auto_destination_fault_keeps_raw_and_lists_every_failed_slide(store, monkeypatch, fault):
    def fail(*args, **kwargs):
        raise OSError(fault, "destination fault")

    monkeypatch.setattr(exports, "write_slide", fail)
    auto = pipeline(store)
    auto.arm(2)
    auto.run(2)
    tray = store.snapshot().trays[1]
    assert tray.auto == "failed" and tray.auto_failed == (1, 2, 3)
    assert not tray.raw_removed and store.sweep_dir(2, 1).exists()


def test_auto_manifest_directory_fsync_failure_keeps_raw(store, monkeypatch):
    from opticfilm_linux.batch import store as store_module

    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    real = os.fsync
    failed = False

    def fail_directory_once(fd):
        nonlocal failed
        target = os.fstat(fd)
        manifest_dir = store.directory.stat()
        if (
            stat.S_ISDIR(target.st_mode)
            and (target.st_dev, target.st_ino) == (manifest_dir.st_dev, manifest_dir.st_ino)
            and not failed
        ):
            failed = True
            raise OSError(errno.EIO, "manifest directory fsync failed")
        return real(fd)

    monkeypatch.setattr(store_module.os, "fsync", fail_directory_once)
    auto.confirm_free_held(1)
    assert failed, "manifest publication must fsync its directory"
    assert store.sweep_dir(1, 1).exists()
    assert not store.snapshot().trays[0].raw_removed
    assert store.snapshot().trays[0].auto == "failed"


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("intact", "raw data kept"),
        ("partial", "raw data partly removed"),
        ("complete", "raw data removed; cleanup completion saved"),
    ],
)
def test_auto_cleanup_failure_reports_actual_raw_state(store, monkeypatch, fault, expected):
    from opticfilm_linux.app import auto as auto_module

    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    raw = store.sweep_dir(1, 1) / "raw"
    (raw.parent / "second").write_bytes(b"second raw")

    def fail_cleanup(*args, **kwargs):
        if fault == "partial":
            raw.unlink()
            store.update_tray(1, raw_removed=True)
        raise OSError("cleanup failed")

    if fault == "complete":
        real = store.update_tray

        def fail_completion(number, **changes):
            if changes.get("auto") == "freed":
                raise OSError("saving completion failed")
            return real(number, **changes)

        monkeypatch.setattr(store, "update_tray", fail_completion)
    else:
        monkeypatch.setattr(auto_module, "cleanup_raw", fail_cleanup)
    auto.confirm_free_held(1)
    assert expected in store.snapshot().trays[0].auto_error.lower()


@pytest.mark.parametrize("remaining", [False, True])
def test_auto_recovered_cleanup_failure_never_claims_raw_kept(store, monkeypatch, remaining):
    import shutil

    from opticfilm_linux.app import auto as auto_module

    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    store.update(
        lambda m: dataclasses.replace(
            m, first_tray_released=True, released_render=exports._release_defaults(m.defaults)
        )
    )
    store.update_tray(1, auto="verified", raw_removed=True)
    if not remaining:
        shutil.rmtree(store.sweep_dir(1, 1))
    real = auto._update

    def fail_completion(number, **changes):
        if changes.get("auto") == "freed":
            raise OSError("saving completion failed")
        return real(number, **changes)

    def fail_cleanup(*args, **kwargs):
        raise OSError("retry failed")

    monkeypatch.setattr(auto, "_update", fail_completion)
    monkeypatch.setattr(auto_module, "cleanup_raw", fail_cleanup)
    auto.run(1)
    error = store.snapshot().trays[0].auto_error.lower()
    assert "raw data kept" not in error
    assert ("cleanup incomplete" if remaining else "raw data removed") in error


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("state", ["committed", "empty", "gone", "absent"])
def test_auto_cleanup_failure_uses_current_flag_and_folders(store, monkeypatch, held, state):
    import shutil

    from opticfilm_linux.app import auto as auto_module

    auto = pipeline(store)
    number = 1 if held else 2
    if held:
        auto.arm(number)
        auto.run(number)
    else:
        store.update(
            lambda m: dataclasses.replace(
                m, first_tray_released=True, released_render=exports._release_defaults(m.defaults)
            )
        )
        auto.arm(number)
    if state == "absent":
        shutil.rmtree(store.sweep_dir(number, 1))

    def fail_cleanup(*args, **kwargs):
        folder = store.sweep_dir(number, 1)
        if state == "gone":
            shutil.rmtree(folder)
        elif state != "absent":
            store.update_tray(number, raw_removed=True)
            if state == "empty":
                (folder / "raw").unlink()
        raise OSError("cleanup interrupted")

    monkeypatch.setattr(auto_module, "cleanup_raw", fail_cleanup)
    auto.confirm_free_held(number) if held else auto.run(number)
    error = auto._tray(number).auto_error.lower()
    assert "raw data kept" not in error
    expected = {"gone": "raw data removed", "absent": "no raw files were present"}.get(
        state, "cleanup incomplete"
    )
    assert expected in error
    if state == "gone":
        assert (
            "manifest still marks the raw scans as available but sweep folders are absent" in error
        )
    elif state != "absent":
        assert "manifest marks the raw scans as freed but sweep folders remain" in error
    if state == "committed":
        assert "removal pending; raw still present" in error
        assert "verification and cleanup recovery are required" in error


@pytest.mark.parametrize("state", ["committed", "gone", "freed"])
def test_auto_slide_export_failure_reports_current_raw_state(store, monkeypatch, state):
    import shutil

    from opticfilm_linux.app import auto as auto_module

    def fail_export(*args, **kwargs):
        if state != "gone":
            store.update_tray(2, raw_removed=True)
        folder = store.sweep_dir(2, 1)
        if state != "committed" and folder.exists():
            shutil.rmtree(folder)
        raise OSError("export interrupted")

    monkeypatch.setattr(auto_module, "export_batch", fail_export)
    auto = pipeline(store)
    auto.arm(2)
    auto.run(2)
    tray = auto._tray(2)
    assert tray.auto == "failed" and tray.auto_failed == (1, 2, 3)
    assert "raw data kept" not in tray.auto_error
    assert "export interrupted" in tray.auto_error
    if state == "committed":
        assert "manifest marks the raw scans as freed but sweep folders remain" in tray.auto_error
    elif state == "gone":
        assert (
            "manifest still marks the raw scans as available but sweep folders are absent"
            in tray.auto_error
        )
    else:
        assert "raw data removed" in tray.auto_error


@pytest.mark.parametrize("evidence", ["raw_inventory", "raw_failure"])
def test_automatic_failure_keeps_original_error_when_evidence_inspection_fails(
    store, monkeypatch, evidence
):
    from opticfilm_linux.app import auto as automatic

    def unavailable(*args, **kwargs):
        raise OSError("inspection unavailable")

    monkeypatch.setattr(automatic, evidence, unavailable)
    auto = pipeline(store)
    auto._failed(1, ValueError("original cleanup failure"), before={})
    tray = auto._tray(1)
    assert tray.auto == "failed"
    assert "original cleanup failure" in tray.auto_error
    assert "outcome unknown" in tray.auto_error
    assert "inspection unavailable" in tray.auto_error
    assert tray.auto_failed == ()


def test_unchanged_committed_raw_requires_verification_without_claiming_unusable(store):
    from opticfilm_linux.app.raw import raw_failure, raw_inventory

    store.update_tray(1, raw_removed=True)
    before = raw_inventory(store, (1,))
    message = raw_failure(store, (1,), before, before)
    assert "raw still present" in message
    assert "verification" in message
    assert "cannot be used" not in message


def test_slide_failures_survive_unavailable_raw_evidence(store, monkeypatch):
    from opticfilm_linux.app import auto as automatic

    inventory = automatic.raw_inventory
    inspected = []

    def failing_inventory(*args):
        if inspected:
            raise OSError("inspection failed")
        inspected.append(True)
        return inventory(*args)

    def failing_export(*args, **kwargs):
        raise OSError("original album failure")

    monkeypatch.setattr(automatic, "raw_inventory", failing_inventory)
    monkeypatch.setattr(automatic, "export_batch", failing_export)
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    tray = auto._tray(1)
    assert tray.auto == "failed"
    assert tray.auto_failed == (1, 2, 3)
    assert "outcome unknown" in tray.auto_error
    assert tray.auto_error.count("original album failure") == 3
