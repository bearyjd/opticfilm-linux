import dataclasses
import shutil
import threading

import pytest
from PIL import Image

from opticfilm_linux.batch.enhance import RawRemoved
from opticfilm_linux.batch.events import SlideUpdated
from opticfilm_linux.batch.model import (
    BatchDefaults,
    SlideSettings,
    SlideStatus,
    TrayStage,
)
from opticfilm_linux.batch.review import (
    NoSuchSlide,
    accept_all,
    apply_slide_settings,
    set_slide_status,
    set_tray_mode,
)
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.sweepstore import save_sweep
from synthetic import make_sweep

TIMEOUT = 120


@pytest.fixture(scope="module")
def sweep_dir(tmp_path_factory):
    directory = tmp_path_factory.mktemp("sweep600")
    save_sweep(make_sweep(dpi=600), directory)
    return directory


@pytest.fixture(scope="module")
def template(tmp_path_factory, sweep_dir):
    """A batch with two trays already scanned and enhanced (both in review)."""
    root = tmp_path_factory.mktemp("template") / "batch"
    store = BatchStore.create(root, name="T", defaults=BatchDefaults(dpi=600))
    runner = BatchRunner(store, FakeScanner(sweep_dir))
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
    return BatchStore.open(tmp_path / "b")


def _slide(store, number, position):
    tray = next(t for t in store.snapshot().trays if t.number == number)
    return next(s for s in tray.slides if s.position == position)


def _mark(store, number, position, **changes):
    def change(manifest):
        trays = []
        for tray in manifest.trays:
            if tray.number == number:
                slides = tuple(
                    dataclasses.replace(s, **changes) if s.position == position else s
                    for s in tray.slides
                )
                tray = dataclasses.replace(tray, slides=slides)
            trays.append(tray)
        return dataclasses.replace(manifest, trays=tuple(trays))

    store.update(change)


def test_set_slide_status_marks_the_owner_decision(store):
    set_slide_status(store, 1, 2, SlideStatus.NEEDS_LOOK)
    slide = _slide(store, 1, 2)
    assert slide.status is SlideStatus.NEEDS_LOOK and slide.owner_decided


@pytest.mark.parametrize("status", [SlideStatus.PENDING, SlideStatus.BLANK])
def test_set_status_rejects_pending_and_blank(store, status):
    with pytest.raises(BatchError):
        set_slide_status(store, 1, 1, status)


def test_a_blank_slide_cannot_be_accepted(store):
    _mark(store, 1, 2, status=SlideStatus.BLANK)
    with pytest.raises(BatchError, match="blank"):
        set_slide_status(store, 1, 2, SlideStatus.ACCEPTED)


def test_apply_settings_keeps_status_and_marks_decision(store):
    before = _slide(store, 1, 3).status
    apply_slide_settings(store, 1, 3, SlideSettings(rotation=180, brightness_ev=1.0))
    slide = _slide(store, 1, 3)
    assert slide.settings == SlideSettings(rotation=180, brightness_ev=1.0)
    assert slide.status is before and slide.owner_decided


def test_accept_all_skips_blank_and_non_review_trays(store):
    _mark(store, 1, 2, status=SlideStatus.BLANK)
    _mark(store, 1, 3, status=SlideStatus.NEEDS_LOOK)
    store.update_tray(2, stage=TrayStage.FAILED)
    _mark(store, 2, 1, status=SlideStatus.NEEDS_LOOK)
    accept_all(store)
    assert _slide(store, 1, 2).status is SlideStatus.BLANK
    assert _slide(store, 1, 3).status is SlideStatus.ACCEPTED and _slide(store, 1, 3).owner_decided
    assert _slide(store, 2, 1).status is SlideStatus.NEEDS_LOOK


def test_set_tray_mode_changes_the_mode(store):
    set_tray_mode(store, 2, "negative")
    assert store.snapshot().trays[1].mode == "negative"


def test_unknown_tray_or_slide_raises_nosuchslide(store):
    with pytest.raises(NoSuchSlide):
        set_slide_status(store, 9, 1, SlideStatus.ACCEPTED)
    with pytest.raises(NoSuchSlide):
        apply_slide_settings(store, 1, 9, SlideSettings())
    with pytest.raises(NoSuchSlide):
        set_tray_mode(store, 9, "negative")


def test_removed_raw_blocks_edits_but_not_accepting(store):
    store.update_tray(1, raw_removed=True)
    with pytest.raises(RawRemoved):
        apply_slide_settings(store, 1, 1, SlideSettings(rotation=90))
    with pytest.raises(RawRemoved):
        set_tray_mode(store, 1, "negative")
    set_slide_status(store, 1, 1, SlideStatus.ACCEPTED)
    accept_all(store)


def test_edit_slide_re_enhances_in_the_background(store, sweep_dir):
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    runner.edit_slide(1, 1, SlideSettings(rotation=90))
    assert runner.wait_idle(TIMEOUT)
    with Image.open(store.preview_path(1, 1, "before")) as before:
        with Image.open(store.preview_path(1, 1, "after")) as after:
            assert after.size == (before.size[1], before.size[0])
    assert SlideUpdated(1, 1) in list(runner.events.queue)
    runner.close()


def test_runner_status_change_and_accept_all_emit_events(store, sweep_dir):
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    runner.set_slide_status(1, 2, SlideStatus.NEEDS_LOOK)
    runner.accept_all()
    events = list(runner.events.queue)
    assert SlideUpdated(1, 2) in events
    assert _slide(store, 1, 2).status is SlideStatus.ACCEPTED
    runner.close()


def test_tray_mode_change_re_enhances_but_keeps_owner_choices(store, sweep_dir):
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    runner.set_slide_status(1, 2, SlideStatus.NEEDS_LOOK)
    runner.set_tray_mode(1, "negative")
    assert runner.wait_idle(TIMEOUT)
    tray = store.snapshot().trays[0]
    assert tray.mode == "negative" and tray.stage is TrayStage.REVIEW
    kept = _slide(store, 1, 2)
    assert kept.status is SlideStatus.NEEDS_LOOK and kept.owner_decided
    runner.close()


def test_concurrent_edits_and_enhancement_never_corrupt_the_manifest(store, sweep_dir):
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    runner.scan_tray()  # a third tray enhances while the edits arrive

    def edit(number, position):
        runner.edit_slide(number, position, SlideSettings(brightness_ev=position / 4))

    threads = [
        threading.Thread(target=edit, args=(number, position))
        for number in (1, 2)
        for position in (1, 2, 3, 4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert runner.wait_idle(TIMEOUT)
    runner.close()
    reopened = BatchStore.open(store.directory, readonly=True)
    assert reopened.snapshot() == store.snapshot()
    for number in (1, 2):
        for position in (1, 2, 3, 4):
            slide = _slide(reopened, number, position)
            assert slide.owner_decided and slide.settings.brightness_ev == position / 4
    assert reopened.snapshot().trays[2].stage is TrayStage.REVIEW


@pytest.mark.parametrize(
    "stage", [TrayStage.SCANNING, TrayStage.SCANNED, TrayStage.FAILED, TrayStage.EXPORTED]
)
def test_edits_and_mode_changes_need_a_reviewable_tray(store, stage):
    store.update_tray(1, stage=stage)
    with pytest.raises(BatchError, match="review"):
        apply_slide_settings(store, 1, 1, SlideSettings(rotation=90))
    with pytest.raises(BatchError, match="review"):
        set_tray_mode(store, 1, "negative")


def test_edits_are_allowed_while_a_tray_is_being_re_enhanced(store):
    store.update_tray(1, stage=TrayStage.ENHANCING)
    apply_slide_settings(store, 1, 1, SlideSettings(rotation=90))
    set_tray_mode(store, 1, "negative")


def test_editing_marks_the_preview_stale_and_reenhancing_clears_it(tmp_path, sweep600_dir):
    from opticfilm_linux.batch.enhance import reenhance_slide
    from opticfilm_linux.batch.runner import BatchRunner
    from opticfilm_linux.batch.store import BatchStore
    from opticfilm_linux.scanner.fake import FakeScanner

    with BatchStore.create(tmp_path / "b", name="T", defaults=BatchDefaults(dpi=600)) as store:
        runner = BatchRunner(store, FakeScanner(sweep600_dir))
        runner.scan_tray()
        assert runner.wait_idle(120)
        runner.close()
        assert not any(s.preview_stale for s in store.snapshot().trays[0].slides)
        apply_slide_settings(store, 1, 1, SlideSettings(brightness_ev=1.0))
        assert store.snapshot().trays[0].slides[0].preview_stale
        reenhance_slide(store, 1, 1)
        assert not store.snapshot().trays[0].slides[0].preview_stale


def test_resume_requeues_slides_whose_preview_is_stale(tmp_path, sweep600_dir):
    from opticfilm_linux.batch.runner import BatchRunner
    from opticfilm_linux.batch.store import BatchStore
    from opticfilm_linux.scanner.fake import FakeScanner

    with BatchStore.create(tmp_path / "b", name="T", defaults=BatchDefaults(dpi=600)) as store:
        runner = BatchRunner(store, FakeScanner(sweep600_dir))
        runner.scan_tray()
        assert runner.wait_idle(120)
        runner.close()
        apply_slide_settings(store, 1, 2, SlideSettings(rotation=90))  # app closed before it ran
        again = BatchRunner(store, FakeScanner(sweep600_dir))
        again.resume()
        assert again.wait_idle(120)
        again.close()
        assert not any(s.preview_stale for s in store.snapshot().trays[0].slides)


def test_a_manifest_without_the_stale_field_still_loads():
    import json

    from opticfilm_linux.batch.manifest import from_json, to_json
    from opticfilm_linux.batch.model import BatchManifest, SlideRecord, TrayRecord, TrayStage

    manifest = BatchManifest(
        name="T",
        trays=(TrayRecord(number=1, stage=TrayStage.REVIEW, slides=(SlideRecord(position=1),)),),
    )
    data = json.loads(to_json(manifest))
    del data["trays"][0]["slides"][0]["preview_stale"]
    assert from_json(json.dumps(data)) == manifest
