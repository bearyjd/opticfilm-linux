"""Openings skipped on purpose: never scanned, never exported, their numbers kept free."""

import dataclasses
from types import SimpleNamespace

import pytest

from opticfilm_linux.app import auto, jobs, state
from opticfilm_linux.app.messages import slide_status_text
from opticfilm_linux.app.selection import filter_matches, selectable_keys
from opticfilm_linux.app.selection_commands import SelectionCommands
from opticfilm_linux.batch import export_contract, review
from opticfilm_linux.batch.empty import check_raw_coverage
from opticfilm_linux.batch.export import cleanup_raw, export_batch
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    EmptyConfirmation,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayLocation,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.sweepstore import save_sweep
from synthetic import make_sweep

TIMEOUT = 120
SKIPPED = SlideStatus.SKIPPED
FINGERPRINT = "a" * 64


@pytest.fixture(scope="module")
def sweeps(tmp_path_factory):
    three = tmp_path_factory.mktemp("three")
    sweep = dataclasses.replace(make_sweep(dpi=600, filled=(True,) * 3), positions=(1, 2, 3))
    save_sweep(sweep, three)
    four = tmp_path_factory.mktemp("four")
    save_sweep(make_sweep(dpi=600), four)
    return SimpleNamespace(three=three, four=four)


def _section_store(path):
    """A batch whose section already holds slides 1-20, so the next tray starts at 21."""
    store = BatchStore.create(path, name="T", defaults=BatchDefaults(dpi=600))
    earlier = tuple(
        TrayRecord(n, TrayStage.FAILED, location=TrayLocation("family", 1, "a", 4 * n - 3, 4))
        for n in range(1, 6)
    )
    store.update(lambda m: dataclasses.replace(m, collection="family", trays=earlier))
    return store


def _scan(store, sweep_dir, *, skipped=(), rescan=None):
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    try:
        if rescan is None:
            number = runner.scan_tray(skipped=skipped)
        else:
            number = runner.rescan_tray(rescan, skipped=skipped)
        assert runner.wait_idle(TIMEOUT)
    finally:
        runner.close()
    return number


def _export(store, out):
    review.accept_all(store)
    return sorted(p.name for p in export_batch(store, out, "jpeg").written)


def _tray(store, number):
    return next(t for t in store.snapshot().trays if t.number == number)


def test_a_skipped_opening_is_not_scanned_and_its_number_stays_free(tmp_path, sweeps):
    store = _section_store(tmp_path / "b")
    number = _scan(store, sweeps.three, skipped=(4,))
    tray = _tray(store, number)
    assert tray.location.start == 21 and tray.skipped == (4,)
    assert tray.stage is TrayStage.REVIEW
    assert [s.position for s in tray.slides] == [1, 2, 3, 4]
    assert [s.status is SKIPPED for s in tray.slides] == [False, False, False, True]
    assert _export(store, tmp_path / "out") == ["021.jpg", "022.jpg", "023.jpg"]
    section = tmp_path / "out" / "family" / "box-1" / "a"
    assert not (section / "024.jpg").exists()
    assert _tray(store, number).stage is TrayStage.EXPORTED
    assert cleanup_raw(store, number) > 0
    assert _tray(store, number).raw_removed
    assert not (section / "024.jpg").exists()


def _rescanned_names(tmp_path, sweeps, skipped):
    store = _section_store(tmp_path / f"b{len(skipped)}")
    out = tmp_path / f"out{len(skipped)}"
    number = _scan(store, sweeps.three if skipped else sweeps.four, skipped=skipped)
    _export(store, out)
    cleanup_raw(store, number)
    _scan(store, sweeps.four, rescan=number)
    tray = _tray(store, number)
    assert tray.skipped == () and not any(s.status is SKIPPED for s in tray.slides)
    names = _export(store, out)
    return {s.position: s.exports[-1].path for s in _tray(store, number).slides}, names


def test_a_whole_tray_rescan_puts_the_skipped_slide_in_its_reserved_place(tmp_path, sweeps):
    skipped, names = _rescanned_names(tmp_path, sweeps, (4,))
    control, _ = _rescanned_names(tmp_path, sweeps, ())
    assert skipped[4] == "family/box-1/a/024.jpg"
    assert "024.jpg" in names
    # Openings 1-3 follow the same re-export rule as a tray that was never skipped.
    assert {p: skipped[p] for p in (1, 2, 3)} == {p: control[p] for p in (1, 2, 3)}


def test_a_rescan_into_a_fresh_folder_writes_021_to_024(tmp_path, sweeps):
    store = _section_store(tmp_path / "b")
    number = _scan(store, sweeps.three, skipped=(4,))
    _export(store, tmp_path / "first")
    cleanup_raw(store, number)
    _scan(store, sweeps.four, rescan=number)
    assert _export(store, tmp_path / "fresh") == ["021.jpg", "022.jpg", "023.jpg", "024.jpg"]


def test_a_rescan_can_skip_an_opening_that_was_scanned_before(tmp_path, sweeps):
    store = _section_store(tmp_path / "b")
    number = _scan(store, sweeps.four)
    _scan(store, sweeps.three, skipped=(4,), rescan=number)
    tray = _tray(store, number)
    assert tray.skipped == (4,) and tray.stage is TrayStage.REVIEW
    assert tray.slides[3].status is SKIPPED and not tray.slides[3].preview_stale


@pytest.mark.parametrize("skipped", [(1, 2, 3, 4), (0,), (5,), (3, 1), (2, 2), (True,)])
def test_bad_skipped_openings_are_refused_before_a_tray_is_added(tmp_path, sweeps, skipped):
    store = _section_store(tmp_path / "b")
    runner = BatchRunner(store, FakeScanner(sweeps.three))
    try:
        with pytest.raises(BatchError, match="opening"):
            runner.scan_tray(skipped=skipped)
    finally:
        runner.close()
    assert len(store.snapshot().trays) == 5


def test_every_opening_skipped_tells_the_owner_to_eject(tmp_path, sweeps):
    store = _section_store(tmp_path / "b")
    runner = BatchRunner(store, FakeScanner(sweeps.three))
    try:
        with pytest.raises(BatchError, match="Eject"):
            runner.scan_tray(skipped=(1, 2, 3, 4))
    finally:
        runner.close()


@pytest.mark.parametrize(
    "tray",
    [
        dict(skipped=(1, 2, 3, 4)),
        dict(skipped=(4,), slides=(SlideRecord(3, status=SKIPPED),)),
    ],
)
def test_the_tray_record_refuses_inconsistent_skipped_openings(tray):
    with pytest.raises(ValueError, match="skipped"):
        TrayRecord(1, TrayStage.REVIEW, **tray)


def _review_store(tmp_path):
    store = BatchStore.create(tmp_path / "r", name="R")
    slides = (*(SlideRecord(p) for p in (1, 2, 3)), SlideRecord(4, status=SKIPPED))
    tray = TrayRecord(1, TrayStage.REVIEW, slides=slides, skipped=(4,))
    store.update(lambda m: dataclasses.replace(m, trays=(tray,)))
    return store


def _fourth(store):
    return store.snapshot().trays[0].slides[3]


@pytest.mark.parametrize(
    "edit",
    [
        review.accept_all,
        lambda s: review.accept_tray(s, 1),
        lambda s: review.accept_tray(s, 1, automatic=True),
        lambda s: review.set_batch_orientation(s, 90, False),
        lambda s: review.set_tray_mode(s, 1, "negative"),
    ],
)
def test_tray_wide_edits_leave_a_skipped_opening_alone(tmp_path, edit):
    store = _review_store(tmp_path)
    edit(store)
    assert _fourth(store) == SlideRecord(4, status=SKIPPED)
    assert all(s.status is not SKIPPED for s in store.snapshot().trays[0].slides[:3])


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: review.set_slide_status(s, 1, 4, SlideStatus.ACCEPTED),
        lambda s: review.set_slide_status(s, 1, 4, SlideStatus.BLANK),
        lambda s: review.apply_slide_settings(s, 1, 4, SlideSettings(rotation=90)),
    ],
)
def test_single_slide_edits_refuse_a_skipped_opening(tmp_path, edit):
    store = _review_store(tmp_path)
    with pytest.raises(BatchError, match="not scanned"):
        edit(store)
    assert _fourth(store) == SlideRecord(4, status=SKIPPED)


def test_bulk_edits_skip_a_skipped_opening_and_say_why(tmp_path):
    store = _review_store(tmp_path)
    keys = {(1, p) for p in (1, 2, 3, 4)}
    result = review.set_status_many(store, keys, SlideStatus.ACCEPTED)
    assert dict(result.skipped) == {(1, 4): "skipped"}
    result = review.apply_settings_many(store, keys, {"rotation": 90})
    assert dict(result.skipped) == {(1, 4): "skipped"}
    assert _fourth(store) == SlideRecord(4, status=SKIPPED)
    shown = {}
    SelectionCommands._bulk_result(SimpleNamespace(_set=lambda **kw: shown.update(kw)), result)
    assert "were not scanned" in shown["_banner"].message


def test_a_skipped_opening_is_not_selectable_and_has_a_status_text(tmp_path):
    store = _review_store(tmp_path)
    assert selectable_keys(store.snapshot()) == ((1, 1), (1, 2), (1, 3))
    assert slide_status_text(SKIPPED) == "Empty (not scanned)"
    failed = dataclasses.replace(store.snapshot().trays[0], stage=TrayStage.FAILED)
    assert [filter_matches(failed, s, "failed") for s in failed.slides] == [True] * 3 + [False]


def test_export_counts_do_not_call_a_skipped_opening_unaccepted(tmp_path):
    store = _review_store(tmp_path)
    review.accept_all(store)
    assert jobs._export_counts(store.snapshot(), None).not_accepted == 0


def _first_tray(*statuses):
    slides = []
    for position, status in enumerate(statuses, start=1):
        if status is SlideStatus.BLANK:
            slides.append(
                SlideRecord(
                    position,
                    status=status,
                    blank_fingerprint=FINGERPRINT,
                    empty_confirmed=EmptyConfirmation(1, FINGERPRINT),
                )
            )
        else:
            slides.append(SlideRecord(position, status=status))
    skipped = tuple(s.position for s in slides if s.status is SKIPPED)
    tray = TrayRecord(1, TrayStage.REVIEW, slides=tuple(slides), skipped=skipped)
    return BatchManifest("M", trays=(tray, TrayRecord(2, TrayStage.REVIEW)))


@pytest.mark.parametrize(
    "statuses,empty",
    [
        ((SlideStatus.BLANK, SlideStatus.BLANK, SKIPPED, SKIPPED), True),
        ((SlideStatus.BLANK,) * 4, True),
        ((SlideStatus.BLANK, SlideStatus.BLANK, SlideStatus.PENDING, SKIPPED), False),
        ((SlideStatus.ACCEPTED,) * 4, False),
    ],
)
def test_a_first_tray_of_skipped_and_confirmed_empty_openings_counts_as_empty(statuses, empty):
    manifest = _first_tray(*statuses)
    assert export_contract._release_candidate(manifest, 1) is not empty
    pipeline = auto.AutoPipeline.__new__(auto.AutoPipeline)
    assert pipeline._release_candidate(manifest) == (None if empty else 1)
    first, _ = state._approval_candidates(manifest.trays)
    assert (first is None) is empty


def test_raw_coverage_ignores_skipped_openings_but_refuses_them_in_the_raw():
    slides = (SlideRecord(1), SlideRecord(2), SlideRecord(4, status=SKIPPED))
    tray = TrayRecord(1, TrayStage.EXPORTED, slides=slides, skipped=(4,))
    check_raw_coverage(tray, {1: None, 2: None})
    for raw in ({1: None, 2: None, 4: None}, {1: None}, {1: None, 2: None, 3: None}):
        with pytest.raises(ValueError, match="positions"):
            check_raw_coverage(tray, raw)
