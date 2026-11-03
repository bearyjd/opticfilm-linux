"""Remaining raw always requires fresh evidence, including after process restart."""

import dataclasses

import numpy as np
import pytest

from opticfilm_linux.batch import export
from opticfilm_linux.batch.model import SlideStatus
from opticfilm_linux.batch.store import BatchStore
from test_export_round9_cleanup import _failed_first_release_reset, _two_delivered_trays
from test_export_safety import _confirmed_blank, _delivered
from test_export_safety import delivery as delivery


@pytest.mark.parametrize("status", [SlideStatus.BLANK, SlideStatus.PENDING, SlideStatus.NEEDS_LOOK])
def test_receipt_cannot_attach_after_status_changes(delivery, status):
    store, _ = _delivered(delivery)
    manifest = store.snapshot()
    (tray,) = manifest.trays
    (slide,) = tray.slides
    store.update_tray(1, slides=(dataclasses.replace(slide, status=status, exported=False),))
    changed = store.snapshot()
    result = export._mark_exported(
        store,
        1,
        1,
        tray.sweep_version,
        tray.mode,
        slide.settings,
        manifest.defaults,
        record=slide.exports[-1],
    )
    assert result == "stale_slide"
    assert store.snapshot() == changed


def test_failed_postcommit_rollback_cannot_be_bypassed_by_reopen(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    update, split = store.update, export.split_slides

    def changed_raw(sweep):
        (slide,) = split(sweep)
        return [dataclasses.replace(slide, rgb=np.zeros_like(slide.rgb))]

    def disk_full(change):
        if store.snapshot().trays[0].raw_removed:
            raise OSError("cleanup reset failed")
        result = update(change)
        if result.trays[0].raw_removed:
            monkeypatch.setattr(export, "split_slides", changed_raw)
        return result

    monkeypatch.setattr(store, "update", disk_full)
    with pytest.raises(OSError, match="cleanup reset failed"):
        export.cleanup_raw(store, 1)
    store.close()
    with BatchStore.open(store.directory) as reopened:
        with pytest.raises(export.CleanupRefused, match="current raw scan"):
            export.cleanup_raw(reopened, 1)
        assert reopened.sweep_dir(1, 1).exists()


def test_failed_release_rollback_cannot_authorize_later_cleanup_after_reopen(delivery, monkeypatch):
    store = _two_delivered_trays(delivery)
    _failed_first_release_reset(store, monkeypatch)
    first, second = store.snapshot().trays
    export.Path(first.slides[0].export_path).write_bytes(b"invalid delivery")
    store.close()
    with BatchStore.open(store.directory) as reopened:
        with pytest.raises(export.CleanupPolicyRefused, match="tray 1.*recover") as refused:
            export.cleanup_raw(reopened, second.number)
        assert isinstance(refused.value.__cause__, export.ExportVerificationFailed)
        assert reopened.sweep_dir(first.number, 1).exists()
        assert reopened.sweep_dir(second.number, 1).exists()


def test_blank_confirmation_requires_current_raw_fingerprint(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    (tray,) = store.snapshot().trays
    store.update_tray(1, slides=(_confirmed_blank(tray, tray.slides[0]),))
    (slide,) = export.split_slides(None)
    altered = dataclasses.replace(slide, rgb=np.zeros_like(slide.rgb))
    monkeypatch.setattr(export, "split_slides", lambda _: [altered])
    with pytest.raises(export.CleanupRefused, match="empty confirmation"):
        export.cleanup_raw(store, 1)
    assert store.sweep_dir(1, 1).exists()


def test_legacy_owner_decision_does_not_confirm_empty(delivery):
    store, _ = _delivered(delivery)
    (slide,) = store.snapshot().trays[0].slides
    store.update_tray(
        1, slides=(dataclasses.replace(slide, status=SlideStatus.BLANK, owner_decided=True),)
    )
    assert export.freeable_trays(store) == ()
    with pytest.raises(export.CleanupPolicyRefused, match="confirm they are empty"):
        export.cleanup_raw(store, 1)


def test_completed_removal_needs_no_delivery_or_raw_io(delivery, monkeypatch):
    store, path = _delivered(delivery)
    export.cleanup_raw(store, 1)
    path.unlink()
    monkeypatch.setattr(export, "_verify_export", lambda *a, **k: pytest.fail("delivery IO"))
    monkeypatch.setattr(export, "load_sweep", lambda *a: pytest.fail("raw IO"))
    assert export.cleanup_raw(store, 1) == 0
