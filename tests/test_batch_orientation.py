"""Changing a slide's mirror and the whole batch's default orientation after scanning."""

import dataclasses
import shutil

import pytest
from PIL import Image

from opticfilm_linux.batch import enhance as enhance_module
from opticfilm_linux.batch import export as export_module
from opticfilm_linux.batch.enhance import reenhance_slide
from opticfilm_linux.batch.export import export_batch
from opticfilm_linux.batch.model import BatchDefaults, SlideSettings, SlideStatus, TrayStage
from opticfilm_linux.batch.review import apply_slide_settings, set_batch_orientation
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.scanner.fake import FakeScanner

TIMEOUT = 120


@pytest.fixture(scope="module")
def template(tmp_path_factory, sweep600_dir):
    """A batch with two trays already scanned and enhanced (both in review)."""
    root = tmp_path_factory.mktemp("orient") / "batch"
    store = BatchStore.create(root, name="T", defaults=BatchDefaults(dpi=600))
    runner = BatchRunner(store, FakeScanner(sweep600_dir))
    runner.scan_tray()
    assert runner.wait_scans_idle(TIMEOUT)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    runner.close()
    store.close()
    return root


@pytest.fixture
def store(tmp_path, template):
    shutil.copytree(template, tmp_path / "b")
    with BatchStore.open(tmp_path / "b") as opened:
        yield opened


def _tray(store, number):
    return next(t for t in store.snapshot().trays if t.number == number)


def _slide(store, number, position):
    return next(s for s in _tray(store, number).slides if s.position == position)


def _changed(tray, changes):
    return dataclasses.replace(
        tray, slides=tuple(dataclasses.replace(s, **changes) for s in tray.slides)
    )


def _mark(store, number, **changes):
    def change(manifest):
        trays = tuple(_changed(t, changes) if t.number == number else t for t in manifest.trays)
        return dataclasses.replace(manifest, trays=trays)

    store.update(change)


def test_toggling_mirror_marks_the_preview_stale_and_the_export_obsolete(store):
    _mark(store, 1, exported=True)
    apply_slide_settings(store, 1, 1, SlideSettings(mirror=True))
    slide = _slide(store, 1, 1)
    assert slide.settings.mirror and slide.preview_stale and not slide.exported
    assert _slide(store, 1, 2).exported  # the other slides keep their mark


def test_set_batch_orientation_updates_defaults_and_marks_every_slide(store):
    _mark(store, 1, exported=True)
    apply_slide_settings(store, 2, 3, SlideSettings(brightness_ev=1.0))
    before = store.snapshot()
    assert set_batch_orientation(store, 90, True) == ()
    after = store.snapshot()
    assert (after.defaults.rotation, after.defaults.mirror) == (90, True)
    for old_tray, new_tray in zip(before.trays, after.trays, strict=True):
        for old, new in zip(old_tray.slides, new_tray.slides, strict=True):
            assert new.preview_stale and not new.exported
            assert (new.status, new.settings, new.owner_decided) == (
                old.status,
                old.settings,
                old.owner_decided,
            )


def test_set_batch_orientation_to_the_current_value_changes_nothing(store):
    _mark(store, 1, exported=True)
    before = store.snapshot()
    assert set_batch_orientation(store, 0, False) == ()
    assert store.snapshot() == before


def test_setting_the_current_orientation_again_reports_no_skipped_trays(store):
    store.update_tray(1, stage=TrayStage.EXPORTED, raw_removed=True)
    assert set_batch_orientation(store, 0, False) == ()


def test_blank_slides_are_not_marked_stale(store):
    _mark(store, 1, status=SlideStatus.BLANK)
    set_batch_orientation(store, 180, False)
    assert not any(s.preview_stale for s in _tray(store, 1).slides)
    assert all(s.preview_stale for s in _tray(store, 2).slides)


@pytest.mark.parametrize("stage", [TrayStage.SCANNING, TrayStage.ENHANCING])
def test_set_batch_orientation_refuses_while_a_tray_is_busy(store, stage):
    store.update_tray(2, stage=stage)
    before = store.snapshot()
    with pytest.raises(BatchError, match="tray 2"):
        set_batch_orientation(store, 90, False)
    assert store.snapshot() == before


def test_set_batch_orientation_rejects_a_bad_rotation(store):
    before = store.snapshot()
    with pytest.raises(ValueError):
        set_batch_orientation(store, 45, False)
    assert store.snapshot() == before


def test_trays_whose_raw_scans_were_removed_are_skipped_and_reported(store):
    store.update_tray(1, stage=TrayStage.EXPORTED, raw_removed=True)
    _mark(store, 1, exported=True)
    untouched = _tray(store, 1)
    assert set_batch_orientation(store, 270, False) == (1,)
    assert _tray(store, 1) == untouched
    assert all(s.preview_stale for s in _tray(store, 2).slides)


def test_an_exported_tray_with_raw_scans_goes_back_to_review(store):
    store.update_tray(1, stage=TrayStage.EXPORTED)
    _mark(store, 1, exported=True)
    set_batch_orientation(store, 90, False)
    tray = _tray(store, 1)
    assert tray.stage is TrayStage.REVIEW and not any(s.exported for s in tray.slides)


def test_a_preview_rendered_with_the_old_orientation_is_not_taken_as_current(store, monkeypatch):
    _mark(store, 1, preview_stale=True)
    real = enhance_module.enhance_slide

    def change_meanwhile(slide, options, dpi):
        image = real(slide, options, dpi)
        set_batch_orientation(store, 90, False)
        return image

    monkeypatch.setattr(enhance_module, "enhance_slide", change_meanwhile)
    reenhance_slide(store, 1, 1)
    assert _slide(store, 1, 1).preview_stale  # dropped: a newer job is queued for it


def test_a_file_exported_with_the_old_orientation_is_not_marked_exported(
    store, tmp_path, monkeypatch
):
    _mark(store, 1, status=SlideStatus.ACCEPTED)
    _mark(store, 2, status=SlideStatus.NEEDS_LOOK)
    real = export_module.enhance_slide
    calls = []

    def change_meanwhile(slide, options, dpi):
        image = real(slide, options, dpi)
        if not calls:
            set_batch_orientation(store, 180, True)
        calls.append(options)
        return image

    monkeypatch.setattr(export_module, "enhance_slide", change_meanwhile)
    export_batch(store, tmp_path / "out")
    assert not _slide(store, 1, 1).exported


def _after_size(store, number, position):
    with Image.open(store.preview_path(number, position, "after")) as image:
        return image.size


def test_runner_re_enhances_every_slide_with_the_new_orientation(store, sweep600_dir):
    sizes = {
        (t.number, s.position): _after_size(store, t.number, s.position)
        for t in store.snapshot().trays
        for s in t.slides
    }
    runner = BatchRunner(store, FakeScanner(sweep600_dir))
    try:
        assert runner.set_batch_orientation(90, True) == ()
        assert runner.wait_idle(TIMEOUT)
    finally:
        runner.close()
    for (number, position), (width, height) in sizes.items():
        assert not _slide(store, number, position).preview_stale
        assert _after_size(store, number, position) == (height, width)


def test_runner_reports_skipped_trays_and_leaves_them_alone(store, sweep600_dir):
    store.update_tray(1, stage=TrayStage.EXPORTED, raw_removed=True)
    runner = BatchRunner(store, FakeScanner(sweep600_dir))
    try:
        assert runner.set_batch_orientation(180, False) == (1,)
        assert runner.wait_idle(TIMEOUT)
    finally:
        runner.close()
    assert not any(s.preview_stale for t in store.snapshot().trays for s in t.slides)


def test_re_applying_the_current_orientation_is_a_no_op_even_while_a_tray_is_busy(store):
    store.update_tray(2, stage=TrayStage.ENHANCING)
    before = store.snapshot()
    assert set_batch_orientation(store, 0, False) == ()
    assert store.snapshot() == before


def _exported(store, number):
    store.update_tray(number, stage=TrayStage.EXPORTED)
    _mark(store, number, status=SlideStatus.ACCEPTED, exported=True)


def test_orientation_changed_just_before_cleanup_commits_keeps_the_raw_scans(store, monkeypatch):
    _exported(store, 1)
    real = export_module._folder_size

    def change_meanwhile(path):
        if _tray(store, 1).stage is TrayStage.EXPORTED:
            set_batch_orientation(store, 90, False)
        return real(path)

    monkeypatch.setattr(export_module, "_folder_size", change_meanwhile)
    with pytest.raises(export_module.CleanupRefused):
        export_module.cleanup_raw(store, 1)
    tray = _tray(store, 1)
    assert not tray.raw_removed and tray.stage is TrayStage.REVIEW
    assert store.sweep_dir(1, tray.sweep_version).is_dir()


def test_orientation_changed_while_raw_scans_are_deleted_skips_that_tray(store, monkeypatch):
    _exported(store, 1)
    real = shutil.rmtree
    reported = []

    def change_meanwhile(path, *args, **kwargs):
        if not reported:
            reported.append(set_batch_orientation(store, 90, False))
        real(path, *args, **kwargs)

    monkeypatch.setattr(export_module.shutil, "rmtree", change_meanwhile)
    export_module.cleanup_raw(store, 1)
    assert reported == [(1,)]
    tray = _tray(store, 1)
    assert tray.raw_removed and tray.stage is TrayStage.EXPORTED
    assert not any(s.preview_stale for s in tray.slides)
    assert all(s.preview_stale for s in _tray(store, 2).slides)


def test_a_failed_deletion_keeps_the_tray_marked_removed_and_says_so(store, monkeypatch):
    _exported(store, 1)

    def broken(path, *args, **kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(export_module.shutil, "rmtree", broken)
    with pytest.raises(BatchError, match="can no longer be used"):
        export_module.cleanup_raw(store, 1)
    assert _tray(store, 1).raw_removed


def test_a_preview_rendered_for_an_old_orientation_is_never_published(store, monkeypatch):
    path = store.preview_path(1, 1, "after")
    _mark(store, 1, preview_stale=True)
    published = path.read_bytes()
    real = enhance_module.enhance_slide

    def change_meanwhile(slide, options, dpi):
        image = real(slide, dataclasses.replace(options, rotation=90), dpi)  # a different look
        set_batch_orientation(store, 180, False)
        return image

    monkeypatch.setattr(enhance_module, "enhance_slide", change_meanwhile)
    reenhance_slide(store, 1, 1)
    assert path.read_bytes() == published
    assert not list(path.parent.glob("*pending*"))


def test_a_current_render_is_published(store, monkeypatch):
    path = store.preview_path(1, 1, "after")
    published = path.read_bytes()
    apply_slide_settings(store, 1, 1, SlideSettings(rotation=90))
    reenhance_slide(store, 1, 1)
    assert path.read_bytes() != published and not _slide(store, 1, 1).preview_stale
    assert not list(path.parent.glob("*pending*"))


def test_a_cleanup_that_failed_after_committing_can_be_finished_later(store, monkeypatch):
    _exported(store, 1)
    folder = store.sweep_dir(1, _tray(store, 1).sweep_version)
    size = export_module._folder_size(folder)
    real = shutil.rmtree

    def broken(path, *args, **kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(export_module.shutil, "rmtree", broken)
    with pytest.raises(BatchError):
        export_module.cleanup_raw(store, 1)
    assert _tray(store, 1).raw_removed and folder.is_dir()
    assert [t.number for t in export_module.freeable_trays(store)] == [1]
    monkeypatch.setattr(export_module.shutil, "rmtree", real)
    assert export_module.free_space(store) == size
    assert not folder.exists() and export_module.freeable_trays(store) == ()


def test_pending_previews_are_removed_when_rendering_fails(store, monkeypatch):
    path = store.preview_path(1, 1, "after")

    def broken(*args, **kwargs):
        raise RuntimeError("flags failed")

    monkeypatch.setattr(enhance_module, "compute_flags", broken)
    with pytest.raises(RuntimeError):
        reenhance_slide(store, 1, 1)
    assert not list(path.parent.glob("*pending*"))
