"""Openings skipped on purpose: never scanned, never exported, their numbers kept free."""

import dataclasses
from types import SimpleNamespace

import pytest

from opticfilm_linux.app import auto, jobs, state
from opticfilm_linux.app.messages import slide_status_text
from opticfilm_linux.app.selection import filter_matches, selectable_keys
from opticfilm_linux.app.selection_commands import SelectionCommands
from opticfilm_linux.app.state import SlideCard, TrayRow
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
    gap = tmp_path_factory.mktemp("gap")
    sweep = dataclasses.replace(make_sweep(dpi=600, filled=(True,) * 3), positions=(1, 3, 4))
    save_sweep(sweep, gap)
    return SimpleNamespace(three=three, four=four, gap=gap)


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


def test_a_rescan_that_skips_an_opening_drops_its_old_previews_and_master(tmp_path, sweeps):
    from opticfilm_linux.app.preview_tokens import PreviewTokens

    store = _section_store(tmp_path / "b")
    number = _scan(store, sweeps.four)
    assert store.preview_path(number, 4, "after").exists()
    _scan(store, sweeps.three, skipped=(4,), rescan=number)
    for kind in ("before", "after"):
        assert not store.preview_path(number, 4, kind).exists()
    assert not store.master_path(number, 4).exists()
    assert store.preview_path(number, 3, "after").exists()
    tokens = PreviewTokens().read(store, store.snapshot())
    assert (number, 4) not in tokens and (number, 4, "before") not in tokens
    assert (number, 3) in tokens


def _card(position, status, token=7):
    return SlideCard(position, status, (), SlideSettings(), False, False, False, token, token)


def test_a_skipped_tile_has_no_preview_and_is_not_selectable():
    from opticfilm_linux.app.qt.sheet import filter_count, sheet_rows

    cards = (_card(1, SlideStatus.ACCEPTED), _card(2, SKIPPED))
    rows = sheet_rows(SimpleNamespace(trays=(_row_with(cards),), selection=frozenset(), focus=None))
    accepted, skipped = rows[0]["slides"]
    assert accepted["selectable"] and accepted["afterUrl"] and accepted["beforeUrl"]
    assert not skipped["selectable"]
    assert skipped["afterUrl"] == "" and skipped["beforeUrl"] == ""
    assert skipped["statusText"] == "Empty (not scanned)"
    failed = SimpleNamespace(trays=(_row_with((_card(4, SKIPPED),), TrayStage.FAILED),))
    assert filter_count(failed, "failed") == 1  # a failed tray with no scanned slide


def _row_with(cards, stage=TrayStage.REVIEW):
    row = TrayRow(1, stage, "", None, None, None, 0, False)
    return dataclasses.replace(row, slides=cards)


def test_a_skip_in_the_middle_keeps_022_free_and_a_rescan_fills_only_it(tmp_path, sweeps):
    store = _section_store(tmp_path / "b")
    out = tmp_path / "out"
    number = _scan(store, sweeps.gap, skipped=(2,))
    assert _export(store, out) == ["021.jpg", "023.jpg", "024.jpg"]
    section = out / "family" / "box-1" / "a"
    before = {p.name: p.read_bytes() for p in section.iterdir()}
    cleanup_raw(store, number)
    _scan(store, sweeps.four, rescan=number)
    review.accept_all(store)
    written = export_batch(store, out, "jpeg", keys=frozenset({(number, 2)})).written
    assert [p.name for p in written] == ["022.jpg"]
    assert {name: (section / name).read_bytes() for name in before} == before
    assert sorted(p.name for p in section.iterdir()) == sorted([*before, "022.jpg"])


def test_the_flat_layout_reserves_no_number_for_a_skipped_opening(tmp_path, sweeps):
    """Like a blank window, a skipped one only keeps its number in the box/section layout."""
    store = BatchStore.create(tmp_path / "b", name="T", defaults=BatchDefaults(dpi=600))
    _scan(store, sweeps.gap, skipped=(2,))
    names = _export(store, tmp_path / "out")
    numbers = [int("".join(c for c in name if c.isdigit())) for name in names]
    assert len(names) == 3 and numbers == list(range(numbers[0], numbers[0] + 3))


class _WrongPositions:
    """A scanner that ignores the requested openings (what a misbehaving driver could do)."""

    def __init__(self, inner):
        self._inner = inner

    def status(self):
        return self._inner.status()

    def scan_holder(self, settings):
        return self._inner.scan_holder(dataclasses.replace(settings, positions=(1, 2, 3, 4)))


def test_a_sweep_with_other_openings_than_requested_fails_the_tray(tmp_path, sweeps):
    store = _section_store(tmp_path / "b")
    runner = BatchRunner(store, _WrongPositions(FakeScanner(sweeps.four)))
    try:
        number = runner.scan_tray(skipped=(4,))
        assert runner.wait_idle(TIMEOUT)
    finally:
        runner.close()
    tray = _tray(store, number)
    assert tray.stage is TrayStage.FAILED
    assert "openings (1, 2, 3, 4)" in tray.error and "(1, 2, 3)" in tray.error
    assert not store.sweep_dir(number, tray.sweep_version).exists()


def test_skipped_records_exist_from_the_moment_the_scan_starts(tmp_path, sweeps):
    import threading

    gate = threading.Event()
    store = _section_store(tmp_path / "b")
    runner = BatchRunner(store, FakeScanner(sweeps.three, before_scan=lambda: gate.wait(TIMEOUT)))
    try:
        number = runner.scan_tray(skipped=(4,))
        tray = _tray(store, number)
        assert tray.stage is TrayStage.SCANNING
        assert tray.slides == (SlideRecord(4, status=SKIPPED),)
    finally:
        gate.set()
        runner.close()


def test_a_quick_turn_never_touches_a_skipped_opening():
    from opticfilm_linux.batch import reorient

    def manifest(stale, rotation):
        slide = SlideRecord(
            4, status=SKIPPED, preview_stale=stale, settings=SlideSettings(rotation=rotation)
        )
        tray = TrayRecord(1, TrayStage.REVIEW, slides=(SlideRecord(1), slide), skipped=(4,))
        return BatchManifest("M", trays=(tray,))

    assert not reorient._eligible(manifest(False, 0), manifest(True, 90), (1, 4))
