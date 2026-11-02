import dataclasses

import pytest

from opticfilm_linux.batch import enhance, export, review
from opticfilm_linux.batch.enhance import RawRemoved
from opticfilm_linux.batch.model import SlideSettings, SlideStatus, TrayStage
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.scanner.fake import FakeScanner
from test_export_safety import _assert_raw_kept, _delivered
from test_export_safety import delivery as delivery


def _mark_exported(store):
    manifest = store.snapshot()
    tray = manifest.trays[0]
    slide = tray.slides[0]
    return export._mark_exported(
        store, 1, 1, tray.sweep_version, tray.mode, slide.settings, manifest.defaults
    )


def _rescan(store):
    runner = BatchRunner(store, FakeScanner(store.sweep_dir(1, 1)))
    try:
        runner.rescan_tray(1)
    finally:
        runner.close()


ACTIONS = {
    "status": lambda store: review.set_slide_status(store, 1, 1, SlideStatus.NEEDS_LOOK),
    "accepted_status": lambda store: review.set_slide_status(store, 1, 1, SlideStatus.ACCEPTED),
    "accept": lambda store: review.accept_tray(store, 1),
    "settings": lambda store: review.apply_slide_settings(store, 1, 1, SlideSettings(rotation=90)),
    "mirror": lambda store: review.apply_slide_settings(store, 1, 1, SlideSettings(mirror=True)),
    "mode": lambda store: review.set_tray_mode(store, 1, "negative"),
    "clear_export": lambda store: export.clear_exported(store, frozenset({(1, 1)})),
    "mark_export": _mark_exported,
    "rescan": _rescan,
}


@pytest.mark.parametrize("action", ["status", "accept", "clear_export", "mark_export"])
def test_owner_change_before_cleanup_commit_keeps_raw(delivery, monkeypatch, action):
    store, _ = _delivered(delivery)
    original = store.update
    changed = []

    def edit_before_commit(change):
        monkeypatch.setattr(store, "update", original)
        ACTIONS[action](store)
        changed.append(store.snapshot())
        return original(change)

    monkeypatch.setattr(store, "update", edit_before_commit)
    with pytest.raises(export.CleanupRefused, match="changed meanwhile"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)
    assert store.snapshot() == changed[0]


@pytest.mark.parametrize("action", ACTIONS)
def test_owner_change_after_cleanup_commit_is_refused(delivery, monkeypatch, action):
    store, _ = _delivered(delivery)
    original = export._delete
    observed = []

    def edit_before_delete(opened, tray):
        committed = opened.snapshot()
        assert committed.trays[0].raw_removed
        assert opened.sweep_dir(1, 1).is_dir()
        with pytest.raises((RawRemoved, export.BatchError), match="raw scans.*removed"):
            ACTIONS[action](opened)
        assert opened.snapshot() == committed
        observed.append(True)
        original(opened, tray)

    monkeypatch.setattr(export, "_delete", edit_before_delete)
    assert export.cleanup_raw(store, 1) > 0
    assert observed == [True]
    assert not store.sweep_dir(1, 1).exists()


def test_bulk_accept_refuses_removed_review_tray_atomically(delivery):
    store, _ = delivery
    tray = store.snapshot().trays[0]
    second = dataclasses.replace(tray, number=2)
    store.update(lambda m: dataclasses.replace(m, trays=(tray, second)))
    store.update_tray(2, raw_removed=True)
    before = store.snapshot()
    with pytest.raises(RawRemoved, match="raw scans.*removed"):
        review.accept_all(store)
    assert store.snapshot() == before


@pytest.mark.parametrize("commit_during_request", [False, True])
def test_rescan_refuses_removed_raw_before_atomic_mutation(
    delivery, monkeypatch, commit_during_request
):
    store, _ = delivery
    runner = BatchRunner(store, FakeScanner(store.sweep_dir(1, 1)))
    monkeypatch.setattr(runner, "_submit", lambda *_args, **_kwargs: None)
    original = store.update_tray_if

    def remove_before_update(number, condition, **changes):
        monkeypatch.setattr(store, "update_tray_if", original)
        store.update_tray(number, raw_removed=True)
        return original(number, condition, **changes)

    if commit_during_request:
        monkeypatch.setattr(store, "update_tray_if", remove_before_update)
    else:
        store.update_tray(1, raw_removed=True)
    try:
        with pytest.raises(RawRemoved, match="raw scans.*removed"):
            runner.rescan_tray(1)
        tray = store.snapshot().trays[0]
        assert tray.stage is TrayStage.REVIEW and tray.sweep_version == 1
        assert tray.slides and tray.raw_removed
    finally:
        runner.close()


def test_enhancement_start_after_cleanup_commit_cannot_revive_tray(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    original = store.update_tray_if

    def clean_before_start(number, condition, **changes):
        monkeypatch.setattr(store, "update_tray_if", original)
        export.cleanup_raw(store, number)
        return original(number, condition, **changes)

    monkeypatch.setattr(store, "update_tray_if", clean_before_start)
    with pytest.raises(RawRemoved, match="raw scans.*removed"):
        enhance.enhance_tray(store, 1)
    tray = store.snapshot().trays[0]
    assert tray.stage is TrayStage.EXPORTED and tray.raw_removed
    assert not store.sweep_dir(1, 1).exists()


@pytest.mark.parametrize("blank", [False, True])
def test_late_enhancement_cannot_publish_after_cleanup_commit(delivery, monkeypatch, blank):
    store, _ = _delivered(delivery)
    before = store.snapshot()
    tray = before.trays[0]
    original = export._delete
    published = []

    def merge_before_delete(opened, verified):
        committed = opened.snapshot()
        enhance._merge_slide(
            opened,
            1,
            tray,
            tray.slides[0],
            (),
            blank,
            before.defaults,
            lambda: published.append(True),
        )
        assert opened.snapshot() == committed
        assert published == []
        original(opened, verified)

    monkeypatch.setattr(export, "_delete", merge_before_delete)
    assert export.cleanup_raw(store, 1) > 0


def test_export_completion_cannot_change_removed_tray(delivery):
    store, _ = _delivered(delivery)
    store.update_tray(1, stage=TrayStage.REVIEW, raw_removed=True)
    before = store.snapshot()
    export._finish_tray_if_done(store, 1)
    assert store.snapshot() == before
