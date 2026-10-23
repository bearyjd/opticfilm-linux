import itertools

import pytest

from opticfilm_linux.app.state import EMPTY_STATE, SlideCard, TrayRow, build_state
from opticfilm_linux.batch.model import (
    BatchManifest,
    Flag,
    SlideRecord,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.scanner.base import ScannerStatus


def _manifest() -> BatchManifest:
    slides = (
        SlideRecord(position=1, status=SlideStatus.ACCEPTED),
        SlideRecord(position=2, status=SlideStatus.NEEDS_LOOK, flags=(Flag.TOO_DARK, Flag.DUST)),
        SlideRecord(position=3, status=SlideStatus.NEEDS_LOOK),
        SlideRecord(position=4, status=SlideStatus.BLANK),
    )
    return BatchManifest(
        name="Trips",
        trays=(
            TrayRecord(number=1, stage=TrayStage.REVIEW, slides=slides),
            TrayRecord(number=2, stage=TrayStage.SCANNING),
            TrayRecord(number=3, stage=TrayStage.ENHANCING, error=None),
        ),
    )


def _build(manifest=None, **overrides):
    args = {
        "preview_tokens": {},
        "scanner": ScannerStatus.READY,
        "scan_pending": False,
        "readonly": False,
        "selected": None,
        "updating": frozenset(),
        "progress": {},
        "scan_started": {},
        "now": 100.0,
        "banner": None,
        "export": None,
        "free_space": None,
    }
    args.update(overrides)
    return build_state(manifest if manifest is not None else _manifest(), **args)


@pytest.mark.parametrize(
    ("scanner", "pending", "readonly", "has_batch"),
    list(itertools.product(ScannerStatus, (False, True), (False, True), (False, True))),
)
def test_can_scan_only_when_ready_idle_writable_and_open(scanner, pending, readonly, has_batch):
    manifest = _manifest() if has_batch else None
    state = build_state(
        manifest,
        preview_tokens={},
        scanner=scanner,
        scan_pending=pending,
        readonly=readonly,
        selected=None,
        updating=frozenset(),
        progress={},
        scan_started={},
        now=0.0,
        banner=None,
        export=None,
        free_space=None,
    )
    expected = scanner is ScannerStatus.READY and not pending and not readonly and has_batch
    assert state.can_scan is expected


def test_no_batch_gives_empty_name_and_no_trays():
    state = build_state(
        None,
        preview_tokens={},
        scanner=ScannerStatus.READY,
        scan_pending=False,
        readonly=False,
        selected=3,
        updating=frozenset(),
        progress={},
        scan_started={},
        now=0.0,
        banner=None,
        export=None,
        free_space=None,
    )
    assert state.batch_name is None and state.trays == () and state.selected is None
    assert EMPTY_STATE.batch_name is None and not EMPTY_STATE.can_scan


def test_tray_rows_count_needs_look_and_use_words():
    rows = _build().trays
    assert isinstance(rows[0], TrayRow)
    assert (rows[0].needs_look, rows[0].stage_text) == (2, "Ready to review")


def test_elapsed_only_for_scanning_trays():
    rows = _build(scan_started={1: 10.0, 2: 40.0}).trays
    assert rows[0].elapsed_s is None and rows[1].elapsed_s == 60.0 and rows[2].elapsed_s is None


def test_progress_is_passed_through_by_tray():
    rows = _build(progress={3: 0.5}).trays
    assert [r.progress for r in rows] == [None, None, 0.5]


def test_selected_tray_fills_slide_cards_with_plain_flags_and_tokens():
    state = _build(selected=1, updating=frozenset({(1, 2)}), preview_tokens={(1, 2): 77})
    assert state.selected == 1 and len(state.slides) == 4
    card = state.slides[1]
    assert isinstance(card, SlideCard)
    assert card.flags == ("Very dark", "Dust or scratches detected")
    assert card.updating and card.preview_token == 77
    assert not state.slides[0].updating and state.slides[0].preview_token == 0


def test_selected_tray_that_does_not_exist_falls_back_to_none():
    state = _build(selected=9)
    assert state.selected is None and state.slides == ()


_FLAGS = list(itertools.product(ScannerStatus, *[(False, True)] * 5))


@pytest.mark.parametrize(("scanner", "pending", "readonly", "control", "busy", "strip"), _FLAGS)
def test_holder_buttons_follow_the_truth_table(scanner, pending, readonly, control, busy, strip):
    state = _build(
        scanner=scanner,
        scan_pending=pending,
        readonly=readonly,
        holder_control=control,
        holder_busy=busy,
        next_strip=strip,
    )
    idle = not pending and not readonly and control and not busy
    assert state.can_eject is (idle and scanner in (ScannerStatus.READY, ScannerStatus.NOT_READY))
    assert state.can_load_next is (idle and strip and scanner is ScannerStatus.NOT_READY)
    assert state.next_strip_enabled is strip


def test_holder_buttons_need_an_open_batch():
    state = build_state(
        None,
        preview_tokens={},
        scanner=ScannerStatus.NOT_READY,
        scan_pending=False,
        readonly=False,
        selected=None,
        updating=frozenset(),
        progress={},
        scan_started={},
        now=0.0,
        banner=None,
        export=None,
        free_space=None,
        holder_control=True,
        next_strip=True,
    )
    assert not state.can_eject and not state.can_load_next
    assert not EMPTY_STATE.can_eject and not EMPTY_STATE.can_load_next
