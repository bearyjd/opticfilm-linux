"""Preview cache versions and automatic progress follow current render evidence."""

import dataclasses
import os
from types import SimpleNamespace

from opticfilm_linux.app.controller import Controller
from opticfilm_linux.batch.model import SlideSettings, SlideStatus, TrayStage
from opticfilm_linux.batch.review import apply_slide_settings
from test_app_auto import pipeline

pytest_plugins = ("test_app_auto",)


def test_before_preview_uses_own_file_version(store):
    for kind, timestamp in (("before", 4_000_000), ("after", 8_000_000)):
        path = store.preview_path(1, 1, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"preview")
        os.utime(path, ns=(timestamp, timestamp))
    tokens = Controller._preview_tokens(SimpleNamespace(store=store), store.snapshot())
    assert tokens[(1, 1)] == 8_000_000
    assert tokens[(1, 1, "before")] == 4_000_000
    store.preview_path(1, 1, "after").unlink()
    tokens = Controller._preview_tokens(SimpleNamespace(store=store), store.snapshot())
    assert tokens[(1, 1, "before")] == 4_000_000
    assert (1, 1) not in tokens


def test_editing_exported_automatic_tray_waits_for_preview_then_reexports(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    before = auto._tray(1)
    assert before.auto == "held" and before.stage is TrayStage.EXPORTED
    old_export = before.slides[0].export_path
    apply_slide_settings(store, 1, 1, SlideSettings(rotation=90))
    edited = auto._tray(1)
    assert edited.auto == "pending" and edited.stage is TrayStage.REVIEW
    assert edited.slides[0].preview_stale and not edited.slides[0].exported
    auto.run(1)
    assert auto._tray(1).auto == "pending"
    assert not auto._tray(1).slides[0].exported
    store.update_tray(
        1, slides=tuple(dataclasses.replace(s, preview_stale=False) for s in edited.slides)
    )
    auto.run(1)
    renewed = auto._tray(1)
    assert renewed.auto == "held" and not renewed.raw_removed
    assert renewed.slides[0].owner_decided and renewed.slides[0].exported
    assert renewed.slides[0].export_path != old_export


def test_successful_manual_export_reconciles_failed_automatic_progress(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    store.update_tray(1, auto="failed", auto_error="delivery failed", auto_failed=(1, 2))
    assert auto.retry_completed_exports() == (1,)
    tray = auto._tray(1)
    assert tray.auto == "exported" and not tray.auto_error and not tray.auto_failed
    auto.run(1)
    assert auto._tray(1).auto == "held"
    assert not auto._tray(1).raw_removed


def test_manual_reconciliation_preserves_owner_exclusions(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    tray = auto._tray(1)
    excluded = dataclasses.replace(
        tray.slides[0], status=SlideStatus.NEEDS_LOOK, owner_decided=True
    )
    store.update_tray(
        1, auto="failed", auto_error="delivery failed", slides=(excluded, *tray.slides[1:])
    )
    assert auto.retry_completed_exports() == ()
    assert auto._tray(1).auto == "failed"


def test_persistent_cleanup_retry_counts_as_waiting(store):
    from test_app_state import _build

    store.update_tray(1, auto="verified", auto_error="tray changed meanwhile; retry cleanup")
    state = _build(store.snapshot())
    assert "0 finished, 0 in progress, 1 waiting" in state.auto_status
    assert "0 failed" in state.auto_status


def test_mode_change_of_exported_automatic_tray_rearms_current_export(store):
    from opticfilm_linux.batch.review import set_tray_mode

    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    before = auto._tray(1)
    assert before.auto == "held"
    set_tray_mode(store, 1, "negative")
    renewed = auto._tray(1)
    assert renewed.mode == "negative" and renewed.auto == "pending"
    assert renewed.stage is TrayStage.REVIEW
    assert not renewed.auto_error and not renewed.auto_failed
    assert all(not s.exported for s in renewed.slides)
    assert [s.owner_decided for s in renewed.slides] == [s.owner_decided for s in before.slides]


def test_flagged_hold_explains_owner_review_before_first_tray_approval(store):
    from opticfilm_linux.batch.model import Flag

    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    tray = auto._tray(1)
    flagged = dataclasses.replace(tray.slides[0], flags=(Flag.DUST,), owner_decided=False)
    store.update_tray(1, slides=(flagged, *tray.slides[1:]))
    auto._verify_and_finish(1)
    assert auto._tray(1).auto == "held"
    assert auto._tray(1).auto_error == "Waiting for owner review of flagged slides"
    assert not auto._tray(1).raw_removed
