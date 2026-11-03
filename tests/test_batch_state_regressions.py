"""Capture and owner edits invalidate previous automatic delivery state."""

import dataclasses
import shutil

import pytest

from opticfilm_linux.batch import export, review
from opticfilm_linux.batch.export_contract import _release_candidate
from opticfilm_linux.batch.model import SlideStatus, TrayStage
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.scanner.base import ScannerStatus
from test_app_auto import pipeline

pytest_plugins = ("test_app_auto",)


def test_failed_export_rescan_automatically_exports_new_capture(store, monkeypatch):
    auto = pipeline(store)
    write = export.write_slide

    def fail(*args, **kwargs):
        raise OSError("destination unavailable")

    monkeypatch.setattr(export, "write_slide", fail)
    auto.arm(1)
    auto.run(1)
    failed = store.snapshot().trays[0]
    assert failed.auto == "failed" and failed.auto_failed
    store.update_tray(1, auto_flagged=(1,))
    scanner = type("ReadyScanner", (), {"status": lambda _: ScannerStatus.READY})()
    runner = BatchRunner(store, scanner)

    def capture(number):
        tray = store.snapshot().trays[0]
        shutil.copytree(store.sweep_dir(number, 1), store.sweep_dir(number, tray.sweep_version))
        store.update_tray(number, stage=TrayStage.REVIEW)

    monkeypatch.setattr(runner, "_scan_job", capture)
    monkeypatch.setattr(export, "write_slide", write)
    try:
        runner.rescan_tray(1)
        assert runner.wait_idle(10)
        fresh = store.snapshot().trays[0]
        assert fresh.auto == "pending"
        assert not fresh.auto_error and not fresh.auto_failed and not fresh.auto_flagged
        auto.run(1)
        delivered = store.snapshot().trays[0]
        assert delivered.auto == "held" and delivered.stage is TrayStage.EXPORTED
        assert all(slide.exported for slide in delivered.slides)
        assert delivered.sweep_version == 2
    finally:
        runner.close()


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("bulk", [False, True])
def test_exported_status_change_returns_to_review(store, automatic, bulk):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    if not automatic:
        store.update_tray(1, auto="off")
    if bulk:
        review.set_status_many(store, ((1, 1),), SlideStatus.NEEDS_LOOK)
    else:
        review.set_slide_status(store, 1, 1, SlideStatus.NEEDS_LOOK)
    tray = store.snapshot().trays[0]
    assert tray.stage is TrayStage.REVIEW
    assert tray.auto == ("pending" if automatic else "off")
    assert not tray.raw_removed


def test_release_uses_lowest_ready_surviving_tray(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    first = store.snapshot().trays[0]
    second = dataclasses.replace(first, number=2)
    third = dataclasses.replace(first, number=3)
    store.update(
        lambda m: dataclasses.replace(
            m, trays=(dataclasses.replace(first, raw_removed=True), second, third)
        )
    )
    assert _release_candidate(store.snapshot(), 2)
    assert not _release_candidate(store.snapshot(), 3)
    with pytest.raises(export.CleanupRefused, match="only the first tray"):
        export.release_first_tray(store, 3)
    store.update_tray(
        2, slides=tuple(dataclasses.replace(slide, preview_stale=True) for slide in second.slides)
    )
    assert _release_candidate(store.snapshot(), 3)


@pytest.mark.parametrize("manual_state", ["", "off"])
def test_release_candidate_tracks_automatic_mode_and_window_offer(store, manual_state):
    from opticfilm_linux.app.state import _auto_fields

    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    first = store.snapshot().trays[0]
    store.update(
        lambda m: dataclasses.replace(
            m,
            trays=(
                dataclasses.replace(first, raw_removed=True),
                dataclasses.replace(first, number=2, auto=manual_state),
                dataclasses.replace(first, number=3),
            ),
        )
    )
    automatic = store.snapshot()
    assert not _release_candidate(automatic, 2)
    assert _release_candidate(automatic, 3)
    assert auto._release_candidate(automatic) == 3
    assert _auto_fields(automatic, False)["held_tray"] == 3
    store.update(
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, auto_export=False)
        )
    )
    manual = store.snapshot()
    assert _release_candidate(manual, 2)
    assert not _release_candidate(manual, 3)
    assert _auto_fields(manual, False)["held_tray"] is None
