import ast
import dataclasses
import errno
import inspect
import json
import os
import stat
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from opticfilm_linux.batch import export as exports
from opticfilm_linux.batch import review
from opticfilm_linux.batch.manifest import from_json, to_json
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    Flag,
    SlideRecord,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchStore


def pipeline(store, **kwargs):
    from opticfilm_linux.app.auto import AutoPipeline

    return AutoPipeline(store, **kwargs)


@pytest.fixture
def store(tmp_path, monkeypatch):
    opened = BatchStore.create(tmp_path / "batch", name="Auto")
    defaults = dataclasses.replace(
        opened.snapshot().defaults, auto_export=True, export_dir=str(tmp_path / "album")
    )
    slides = tuple(SlideRecord(i) for i in (1, 2, 3))
    opened.update(
        lambda m: dataclasses.replace(
            m,
            defaults=defaults,
            trays=tuple(TrayRecord(i, TrayStage.REVIEW, slides=slides) for i in (1, 2)),
        )
    )
    for i in (1, 2):
        folder = opened.sweep_dir(i, 1)
        folder.mkdir(parents=True)
        (folder / "raw").write_bytes(b"valuable raw")
    monkeypatch.setattr(exports, "load_sweep", lambda _: type("Sweep", (), {"dpi": 7200})())
    monkeypatch.setattr(exports, "split_slides", lambda _: [1, 2, 3])
    monkeypatch.setattr(
        exports,
        "enhance_slide",
        lambda slide, *_: np.full((30 + slide, 40 + slide, 3), 30000, dtype=np.uint16),
    )
    yield opened
    opened.close()


def test_auto_fields_default_off_and_old_manifest_compatible():
    manifest = BatchManifest(
        "old", trays=(TrayRecord(1, TrayStage.REVIEW, slides=(SlideRecord(1),)),)
    )
    data = json.loads(to_json(manifest))
    assert not BatchDefaults().auto_export
    assert not BatchDefaults().keep_raw
    assert "auto_export" not in data["defaults"]
    assert "auto" not in data["trays"][0]
    assert "export_path" not in data["trays"][0]["slides"][0]
    assert "first_tray_released" not in data
    assert from_json(json.dumps(data)) == manifest


def test_accept_tray_accepts_blank_flags_and_only_that_tray(store):
    first = store.snapshot().trays[0]
    slides = (
        dataclasses.replace(first.slides[0], status=SlideStatus.BLANK),
        dataclasses.replace(first.slides[1], status=SlideStatus.NEEDS_LOOK, flags=(Flag.DUST,)),
        first.slides[2],
    )
    store.update_tray(1, slides=slides)
    review.accept_tray(store, 1)
    current = store.snapshot()
    assert all(
        s.status is SlideStatus.ACCEPTED and s.owner_decided for s in current.trays[0].slides
    )
    assert current.trays[0].slides[1].flags == (Flag.DUST,)
    assert all(s.status is SlideStatus.PENDING for s in current.trays[1].slides)


def test_auto_first_tray_held_then_release_allows_later_free(store):
    auto = pipeline(store)
    assert auto.arm(1)
    auto.run(1)
    first = store.snapshot().trays[0]
    assert first.auto == "held" and not first.raw_removed
    assert len(list((store.directory.parent / "album").glob("*.jpg"))) == 3
    auto.confirm_free_held(1)
    assert store.snapshot().first_tray_released
    assert store.snapshot().trays[0].raw_removed
    assert auto.arm(2)
    auto.run(2)
    assert store.snapshot().trays[1].auto == "freed"
    assert not store.sweep_dir(2, 1).exists()


def test_auto_never_free_first_tray_or_later_before_release(store):
    auto = pipeline(store)
    for n in (1, 2):
        auto.arm(n)
        auto.run(n)
    assert all(not t.raw_removed for t in store.snapshot().trays)


@pytest.mark.parametrize("setting", ["keep_raw", "off"])
def test_auto_never_free_when_setting_disallows(store, setting):
    store.update(
        lambda m: dataclasses.replace(
            m,
            first_tray_released=True,
            defaults=dataclasses.replace(
                m.defaults, keep_raw=setting == "keep_raw", auto_export=setting != "off"
            ),
        )
    )
    auto = pipeline(store)
    auto.arm(2)
    auto.run(2)
    assert not store.snapshot().trays[1].raw_removed
    if setting == "off":
        assert not any(s.exported for s in store.snapshot().trays[1].slides)


@pytest.mark.parametrize("damage", ["missing", "short", "undecodable", "wrong_size", "unmarked"])
def test_auto_never_free_verification_failure(store, monkeypatch, damage):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    slide = store.snapshot().trays[0].slides[0]
    path = Path(slide.export_path)
    if damage == "missing":
        path.unlink()
    elif damage == "short":
        path.write_bytes(b"small")
    elif damage == "undecodable":
        path.write_bytes(b"x" * 2048)
    elif damage == "wrong_size":
        Image.new("RGB", (99, 102)).save(path)
    else:
        tray = store.snapshot().trays[0]
        store.update_tray(1, slides=(dataclasses.replace(slide, exported=False), *tray.slides[1:]))
    auto.confirm_free_held(1)
    assert not store.snapshot().trays[0].raw_removed
    assert not store.snapshot().first_tray_released
    assert store.snapshot().trays[0].auto == "failed"
    assert store.snapshot().trays[0].auto_failed == (1,)


def test_auto_flagged_slides_exported_and_listed(store):
    tray = store.snapshot().trays[0]
    store.update_tray(
        1,
        slides=(
            dataclasses.replace(tray.slides[0], status=SlideStatus.BLANK),
            dataclasses.replace(tray.slides[1], status=SlideStatus.NEEDS_LOOK),
            dataclasses.replace(tray.slides[2], flags=(Flag.DUST,)),
        ),
    )
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    tray = store.snapshot().trays[0]
    assert tray.auto_flagged == (1, 2, 3)
    assert all(s.exported for s in tray.slides)


def test_auto_never_free_one_failed_slide_but_exports_others(store, monkeypatch):
    store.update(lambda m: dataclasses.replace(m, first_tray_released=True))
    real = exports.enhance_slide

    def fail(slide, *args):
        if slide == 2:
            raise ValueError("slide broken")
        return real(slide, *args)

    monkeypatch.setattr(exports, "enhance_slide", fail)
    auto = pipeline(store)
    auto.arm(2)
    auto.run(2)
    tray = store.snapshot().trays[1]
    assert [s.exported for s in tray.slides] == [True, False, True]
    assert tray.auto_failed == (2,)
    assert tray.auto == "failed" and "broken" in tray.auto_error
    assert not tray.raw_removed and store.sweep_dir(2, 1).exists()


@pytest.mark.parametrize("phase", ["pending", "exported", "verified"])
def test_auto_never_free_crash_then_reopen_without_verification(store, monkeypatch, phase):
    auto = pipeline(store)
    auto.arm(2)
    auto.run(2)
    store.update(lambda m: dataclasses.replace(m, first_tray_released=True))
    store.update_tray(2, auto=phase)
    path = Path(store.snapshot().trays[1].slides[0].export_path)
    path.unlink()
    directory = store.directory
    store.close()
    reopened = BatchStore.open(directory)
    try:
        resumed = pipeline(reopened)
        assert 2 in resumed.resume()
        resumed.run(2)
        assert not reopened.snapshot().trays[1].raw_removed
        assert reopened.snapshot().trays[1].auto == "failed"
    finally:
        reopened.close()


def test_auto_handled_tray_never_reaccepted_or_exported_on_reopen(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    store.update_tray(1, stage=TrayStage.REVIEW)
    review.set_tray_mode(store, 1, "negative")
    review.set_slide_status(store, 1, 1, SlideStatus.NEEDS_LOOK)
    assert not auto.arm(1)
    assert 1 not in pipeline(store).resume()
    auto.run(1)
    assert store.snapshot().trays[0].slides[0].status is SlideStatus.NEEDS_LOOK
    assert len(list((store.directory.parent / "album").glob("*.jpg"))) == 3


def test_auto_never_runs_while_same_tray_scan_writes(store):
    store.update_tray(2, stage=TrayStage.SCANNING)
    auto = pipeline(store)
    assert not auto.arm(2)
    auto.run(2)
    assert not store.snapshot().trays[1].raw_removed
    assert not any(s.exported for s in store.snapshot().trays[1].slides)


def test_auto_cancel_never_frees_and_keeps_pending(store):
    cancel = threading.Event()
    cancel.set()
    auto = pipeline(store, cancel=cancel)
    auto.arm(2)
    auto.run(2)
    assert not store.snapshot().trays[1].raw_removed
    assert store.snapshot().trays[1].auto == "pending"


def test_verify_uses_recorded_actual_variable_rotated_scaled_geometry(store):

    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, jpeg_size="3600"))
    )
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    exports.verify_tray_export(store, 1)
    sizes = [s.export_size for s in store.snapshot().trays[0].slides]
    assert sizes == [(21, 16), (21, 16), (22, 17)]
    assert len(set(sizes)) > 1


def test_auto_never_free_through_symlink_outside_batch(store, tmp_path):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    raw = store.sweep_dir(1, 1)
    import shutil

    shutil.rmtree(raw)
    outside = tmp_path / "outside"
    outside.mkdir()
    precious = outside / "raw"
    precious.write_text("keep this")
    raw.symlink_to(outside, target_is_directory=True)
    auto.confirm_free_held(1)
    assert precious.read_text() == "keep this"
    assert not store.snapshot().trays[0].raw_removed
    assert store.snapshot().trays[0].auto == "failed"


def test_auto_never_free_when_owner_edits_during_verification(store):
    store.update(lambda m: dataclasses.replace(m, first_tray_released=True))
    changed = False

    def update():
        nonlocal changed
        if store.snapshot().trays[1].auto == "verified" and not changed:
            changed = True
            tray = store.snapshot().trays[1]
            store.update_tray(
                2,
                stage=TrayStage.REVIEW,
                slides=(
                    dataclasses.replace(tray.slides[0], exported=False, preview_stale=True),
                    *tray.slides[1:],
                ),
            )

    auto = pipeline(store, on_update=update)
    auto.arm(2)
    auto.run(2)
    assert changed
    assert not store.snapshot().trays[1].raw_removed
    assert store.snapshot().trays[1].auto == "failed"


def test_export_geometry_records_rotated_array_not_fixed_dimensions(store, monkeypatch):
    monkeypatch.setattr(
        exports,
        "enhance_slide",
        lambda slide, *_: np.full((50 + slide, 20 + slide, 3), 32000, dtype=np.uint16),
    )
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    sizes = [s.export_size for s in store.snapshot().trays[0].slides]
    assert sizes == [(21, 51), (22, 52), (23, 53)]
    exports.verify_tray_export(store, 1)


def test_auto_owner_release_survives_crash_after_cleanup(store, monkeypatch):
    from opticfilm_linux.app import auto as auto_module

    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    real = auto_module.cleanup_raw

    def crash_after_delete(*args, **kwargs):
        real(*args, **kwargs)
        raise KeyboardInterrupt("power loss")

    monkeypatch.setattr(auto_module, "cleanup_raw", crash_after_delete)
    with pytest.raises(KeyboardInterrupt):
        auto.confirm_free_held(1)
    directory = store.directory
    store.close()
    with BatchStore.open(directory) as reopened:
        assert reopened.snapshot().first_tray_released
        assert reopened.snapshot().trays[0].raw_removed
        monkeypatch.setattr(auto_module, "cleanup_raw", real)
        resumed = pipeline(reopened)
        assert 2 in resumed.resume()
        resumed.run(2)
        assert reopened.snapshot().trays[1].raw_removed


def test_auto_missing_destination_lists_unexported_slides_without_freeing(store):
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, export_dir=""))
    )
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    tray = store.snapshot().trays[0]
    assert tray.auto == "failed" and tray.auto_failed == (1, 2, 3)
    assert not tray.raw_removed


@pytest.mark.parametrize("damage", [False, True])
def test_auto_release_resumes_held_backlog_with_fresh_verification(store, damage):
    auto = pipeline(store)
    for n in (1, 2):
        auto.arm(n)
        auto.run(n)
    tray = store.snapshot().trays[1]
    files_before = tuple(s.export_path for s in tray.slides)
    if damage:
        Path(tray.slides[1].export_path).unlink()
    auto.confirm_free_held(1)
    assert auto.resume() == (2,)
    auto.run(2)
    tray = store.snapshot().trays[1]
    assert tray.raw_removed is not damage
    assert tray.auto == ("failed" if damage else "freed")
    assert tuple(s.export_path for s in tray.slides) == files_before


@pytest.mark.parametrize("option", ["keep_raw", "auto_export"])
def test_auto_held_backlog_never_free_when_settings_disallow(store, option):
    auto = pipeline(store)
    for n in (1, 2):
        auto.arm(n)
        auto.run(n)
    auto.confirm_free_held(1)
    store.update(
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, **{option: option == "keep_raw"})
        )
    )
    assert auto.resume() == ()
    auto.run(2)
    assert not store.snapshot().trays[1].raw_removed


@pytest.mark.parametrize("damage", [False, True])
def test_auto_cleanup_crash_reopen_reverifies_before_deleting_remaining_raw(
    store, monkeypatch, damage
):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    real = exports._delete

    def crash_before_delete(*_args):
        raise KeyboardInterrupt("power loss after removed mark")

    monkeypatch.setattr(exports, "_delete", crash_before_delete)
    with pytest.raises(KeyboardInterrupt):
        auto.confirm_free_held(1)
    assert store.snapshot().trays[0].raw_removed
    assert store.sweep_dir(1, 1).exists()
    if damage:
        Path(store.snapshot().trays[0].slides[0].export_path).unlink()
    directory = store.directory
    store.close()
    monkeypatch.setattr(exports, "_delete", real)
    with BatchStore.open(directory) as reopened:
        resumed = pipeline(reopened)
        assert 1 in resumed.resume()
        resumed.run(1)
        tray = reopened.snapshot().trays[0]
        assert tray.auto == ("failed" if damage else "freed")
        assert reopened.sweep_dir(1, 1).exists() is damage


def test_auto_run_coordinates_work_in_under_fifty_lines():
    from opticfilm_linux.app.auto import AutoPipeline

    tree = ast.parse(inspect.getsource(AutoPipeline))
    run = next(
        node
        for node in tree.body[0].body
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )
    assert run.end_lineno - run.lineno + 1 < 50


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
        ("complete", "raw data removed; could not save cleanup completion"),
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
    store.update(lambda m: dataclasses.replace(m, first_tray_released=True))
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


@pytest.mark.parametrize("reopened", [False, True])
def test_auto_preserves_owner_needs_look_before_queued_or_resumed_run(store, reopened):
    auto = pipeline(store)
    auto.arm(2)
    review.set_slide_status(store, 2, 2, SlideStatus.NEEDS_LOOK)
    if reopened:
        directory = store.directory
        store.close()
        store = BatchStore.open(directory)
        auto = pipeline(store)
        assert 2 in auto.resume()
    try:
        auto.run(2)
        tray = store.snapshot().trays[1]
        assert tray.slides[1].status is SlideStatus.NEEDS_LOOK
        assert not tray.slides[1].exported
        assert not tray.raw_removed and store.sweep_dir(2, 1).exists()
    finally:
        if reopened:
            store.close()
