"""A description or date edit never stalls raw cleanup, a rescan or automatic work."""

import dataclasses

import pytest

from opticfilm_linux.app import auto
from opticfilm_linux.batch import export, review
from opticfilm_linux.batch.metadata import MetaOverride
from opticfilm_linux.batch.model import BatchManifest, SlideRecord, TrayRecord, TrayStage
from opticfilm_linux.batch.runner import BatchRunner, _restart_rescan
from opticfilm_linux.scanner.fake import FakeScanner
from test_export_safety import _delivered
from test_export_safety import delivery as delivery


def _slide_edit(store):
    review.edit_meta_many(store, [(1, 1)], {"description": "Lake"})


def _tray_edit(store):
    review.edit_tray_meta(store, 1, {"date": "1980"})


def _batch_edit(store):  # "from now on" pins every existing tray
    review.edit_batch_meta(store, {"description": "Next event"}, scope="from_now_on")


EDITS = {"slide": _slide_edit, "tray": _tray_edit, "batch": _batch_edit}


def _metas(store):
    tray = store.snapshot().trays[0]
    return tray.meta, tray.slides[0].meta, store.snapshot().description


def _hook(monkeypatch, store, window, action):
    """Run ``action`` once inside ``window`` of the cleanup: the slow sweep check, or between
    the evidence snapshot and the commit."""
    if window == "sweep":
        original = export._checked_sweep_folders

        def checked(*args):
            action()
            return original(*args)

        monkeypatch.setattr(export, "_checked_sweep_folders", checked)
        return
    original = store.update

    def edit_before_commit(change):
        monkeypatch.setattr(store, "update", original)
        action()
        return original(change)

    monkeypatch.setattr(store, "update", edit_before_commit)


@pytest.mark.parametrize("window", ["sweep", "commit"])
@pytest.mark.parametrize("edit", sorted(EDITS))
def test_meta_edit_during_cleanup_still_frees_raw_and_survives(delivery, monkeypatch, edit, window):
    store, _ = _delivered(delivery)
    expected = []

    def action():
        EDITS[edit](store)
        expected.append(_metas(store))

    _hook(monkeypatch, store, window, action)
    assert export.cleanup_raw(store, 1) > 0
    assert store.snapshot().trays[0].raw_removed and not store.sweep_dir(1, 1).exists()
    assert _metas(store) == expected[0]


@pytest.mark.parametrize("window", ["sweep", "commit"])
def test_meta_undo_during_cleanup_still_frees_raw(delivery, monkeypatch, window):
    store, _ = _delivered(delivery)
    result = review.edit_batch_meta(store, {"date": "1975"})
    _hook(monkeypatch, store, window, lambda: review.restore_meta_many(store, result.before))
    assert export.cleanup_raw(store, 1) > 0
    assert store.snapshot().trays[0].raw_removed
    assert store.snapshot().date is None and store.snapshot().trays[0].meta == MetaOverride()


def test_meta_edit_between_rescan_check_and_restart_does_not_refuse(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    store.update_tray(1, stage=TrayStage.REVIEW)
    runner = BatchRunner(store, FakeScanner(store.sweep_dir(1, 1)))
    original = runner._require_ready
    monkeypatch.setattr(runner, "_require_ready", lambda: (_tray_edit(store), original())[1])
    try:
        runner.rescan_tray(1)
    finally:
        runner.close()
    assert store.snapshot().trays[0].meta == MetaOverride(date="1980")
    assert store.snapshot().trays[0].sweep_version == 2


def test_rescan_keeps_slide_meta_also_of_a_skipped_opening():
    slides = tuple(SlideRecord(p, meta=MetaOverride(f"slide {p}", "")) for p in (1, 2))
    tray = TrayRecord(1, TrayStage.REVIEW, slides=slides, meta=MetaOverride(date="1980"))
    manifest = _restart_rescan(BatchManifest(name="m", trays=(tray,)), tray, skipped=(2,))
    (restarted,) = manifest.trays
    assert [s.meta for s in restarted.slides] == [s.meta for s in slides]
    assert restarted.slides[1].status == "skipped" and restarted.meta == tray.meta


def test_resume_marks_freed_even_after_a_meta_edit(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    store.update_tray(1, raw_removed=True, auto="verified")

    def pending(opened, tray):
        _tray_edit(opened)
        return False

    monkeypatch.setattr(auto, "cleanup_pending", pending)
    auto.AutoPipeline(store).resume()
    tray = store.snapshot().trays[0]
    assert tray.auto == "freed" and tray.meta == MetaOverride(date="1980")


def test_stripping_meta_compares_everything_else():
    from opticfilm_linux.batch.metadata import without_meta

    tray = TrayRecord(1, TrayStage.REVIEW, slides=(SlideRecord(1),))
    edited = dataclasses.replace(
        tray,
        meta=MetaOverride("t"),
        slides=(dataclasses.replace(tray.slides[0], meta=MetaOverride(date="")),),
    )
    assert without_meta(edited) == without_meta(tray) and edited != tray
    assert without_meta(dataclasses.replace(edited, auto="held")) != without_meta(tray)
