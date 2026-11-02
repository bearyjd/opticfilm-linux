import ast
import itertools
from dataclasses import replace
from pathlib import Path

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


def test_cleanup_result_text_shows_failure_and_actual_freed_bytes():
    from opticfilm_linux.app import state as state_module
    from opticfilm_linux.app.messages import bytes_text
    from opticfilm_linux.app.state import AppState, FreeSpaceView

    # Exercise the pure formatter without requiring a running Qt application.
    source = Path(state_module.__file__).parent / "qt" / "bridge.py"
    tree = ast.parse(source.read_text())
    formatter = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_free_text"
    )
    namespace = {"AppState": AppState, "bytes_text": bytes_text}
    exec(compile(ast.Module(body=[formatter], type_ignores=[]), str(source), "exec"), namespace)
    view = FreeSpaceView(
        ((1, 12),), 12, freed=5, message="Cleanup failed; raw data partly removed."
    )
    text = namespace["_free_text"](replace(EMPTY_STATE, free_space=view))
    assert "Cleanup failed; raw data partly removed." in text
    assert f"Freed {bytes_text(5)}." in text


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
    assert state.holder_busy is busy


@pytest.mark.parametrize("busy", [False, True])
def test_scan_is_disabled_while_the_holder_moves(busy):
    state = _build(holder_control=True, holder_busy=busy)
    assert state.can_scan is not busy


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


@pytest.mark.parametrize(
    "latch,allowed", [("UNKNOWN", True), ("LATCHED", True), ("RELEASED", False)]
)
def test_can_scan_respects_holder_latch(latch, allowed):
    from opticfilm_linux.scanner.holder_latch import HolderLatch

    state = _build(holder_latch=HolderLatch[latch], holder_control=True)
    assert state.can_scan is allowed
    assert state.can_confirm_holder_latched is (latch == "RELEASED")


def test_auto_status_counts_flagged_and_failed_after_acceptance():
    from dataclasses import replace

    manifest = _manifest()
    first = replace(
        manifest.trays[0],
        auto="held",
        stage=TrayStage.EXPORTED,
        auto_flagged=(2, 4),
        slides=tuple(
            replace(s, status=SlideStatus.ACCEPTED, exported=True, owner_decided=True)
            for s in manifest.trays[0].slides
        ),
    )
    second = replace(manifest.trays[1], stage=TrayStage.REVIEW, auto="failed", auto_failed=(1,))
    manifest = replace(
        manifest, trays=(first, second), defaults=replace(manifest.defaults, auto_export=True)
    )
    state = _build(manifest)
    assert state.auto_export
    assert state.auto_flagged == 2
    assert state.auto_failed == 1
    assert "1 finished" in state.auto_status
    assert "2 flagged" in state.auto_status
    assert "1 failed" in state.auto_status
    assert state.held_tray == 1
    assert state.trays[0].auto == "held"
    assert state.trays[1].auto_failed == (1,)


def test_keep_raw_and_readonly_hide_first_tray_release():
    from dataclasses import replace

    manifest = _manifest()
    manifest = replace(
        manifest,
        trays=(replace(manifest.trays[0], auto="held"),),
        defaults=replace(manifest.defaults, auto_export=True, keep_raw=True),
    )
    assert _build(manifest).held_tray is None
    manifest = replace(manifest, defaults=replace(manifest.defaults, keep_raw=False))
    assert _build(manifest, readonly=True).held_tray is None


@pytest.mark.parametrize("auto", ["off", "failed", "freed", "blocked"])
def test_first_settings_release_hidden_without_verified_held_first_tray(auto):
    manifest = _manifest()
    first = replace(manifest.trays[0], auto=auto, raw_removed=auto == "freed")
    manifest = replace(
        manifest, trays=(first,), defaults=replace(manifest.defaults, auto_export=True)
    )
    assert _build(manifest).held_tray is None
    assert (
        _build(
            replace(
                manifest,
                first_tray_released=True,
                released_render=replace(manifest.defaults, auto_export=False, keep_raw=False),
            )
        ).held_tray
        is None
    )


def test_changed_render_settings_require_verified_first_tray_evidence():
    manifest = _manifest()
    approved = replace(manifest.defaults, auto_export=False, keep_raw=False)
    manifest = replace(
        manifest,
        defaults=replace(manifest.defaults, auto_export=True, jpeg_size="3600"),
        first_tray_released=True,
        released_render=approved,
    )
    assert _build(manifest).held_tray is None


def test_committed_cleanup_waiting_for_completion_is_visible():
    manifest = _manifest()
    tray = replace(manifest.trays[0], raw_removed=True, auto="verified", cleanup_sweeps=(1,))
    assert (
        _build(replace(manifest, trays=(tray,)), raw_pending=frozenset({1})).trays[0].stage_text
        == "Raw removal pending"
    )


def test_load_next_is_available_by_default_with_env_unset(monkeypatch):
    monkeypatch.delenv("OPTICFILM_NEXT_STRIP", raising=False)
    state = _build(scanner=ScannerStatus.NOT_READY, holder_control=True)
    assert state.can_load_next and state.next_strip_enabled


@pytest.mark.parametrize("auto", ["", "held", "freed", "failed"])
def test_manual_interrupted_cleanup_shows_raw_removal_pending(auto):
    tray = replace(_manifest().trays[0], raw_removed=True, auto=auto)
    manifest = replace(_manifest(), trays=(tray,))
    state = _build(manifest, raw_pending=frozenset({tray.number}))
    assert state.trays[0].stage_text == "Raw removal pending"
    assert _build(manifest).trays[0].stage_text != "Raw removal pending"


def test_confirmed_blank_flags_do_not_hide_first_tray_release():
    manifest = _manifest()
    accepted = SlideRecord(1, status=SlideStatus.ACCEPTED, exported=True)
    from opticfilm_linux.batch.model import EmptyConfirmation

    blank = SlideRecord(
        2,
        status=SlideStatus.BLANK,
        flags=(Flag.DUST,),
        owner_decided=True,
        blank_fingerprint="b" * 64,
        empty_confirmed=EmptyConfirmation(manifest.trays[0].sweep_version, "b" * 64),
    )
    first = replace(
        manifest.trays[0],
        stage=TrayStage.EXPORTED,
        auto="held",
        auto_flagged=(2,),
        slides=(accepted, blank),
    )
    manifest = replace(
        manifest, trays=(first,), defaults=replace(manifest.defaults, auto_export=True)
    )
    assert _build(manifest).held_tray == 1


def test_undecided_empty_frames_expose_confirmation_before_release():
    manifest = _manifest()
    first = replace(
        manifest.trays[0],
        stage=TrayStage.EXPORTED,
        auto="held",
        slides=(
            SlideRecord(1, status=SlideStatus.ACCEPTED, exported=True),
            SlideRecord(2, status=SlideStatus.BLANK),
        ),
    )
    manifest = replace(
        manifest, trays=(first,), defaults=replace(manifest.defaults, auto_export=True)
    )
    state = _build(manifest)
    assert state.held_tray is None
    assert state.empty_confirmation_tray == 1
    assert state.empty_confirmation_count == 1


def test_freed_first_tray_allows_surviving_held_settings_approval():
    manifest = _manifest()
    first = replace(manifest.trays[0], stage=TrayStage.EXPORTED, raw_removed=True, auto="freed")
    second = replace(
        first,
        number=2,
        raw_removed=False,
        auto="held",
        slides=(SlideRecord(1, status=SlideStatus.ACCEPTED, exported=True),),
    )
    manifest = replace(
        manifest, trays=(first, second), defaults=replace(manifest.defaults, auto_export=True)
    )
    assert _build(manifest).held_tray == 2


def test_empty_confirmation_includes_every_blank_with_rendered_fingerprints():
    from opticfilm_linux.batch.model import EmptyConfirmation

    manifest = _manifest()
    first = replace(
        manifest.trays[0],
        stage=TrayStage.REVIEW,
        sweep_version=3,
        slides=(
            SlideRecord(
                1,
                status=SlideStatus.BLANK,
                blank_fingerprint="a" * 64,
                empty_confirmed=EmptyConfirmation(3, "a" * 64),
            ),
            SlideRecord(2, status=SlideStatus.BLANK, blank_fingerprint="b" * 64),
        ),
    )
    state = _build(replace(manifest, trays=(first,)))
    assert state.empty_confirmation_sweep_version == 3
    assert state.empty_confirmation_count == 2
    assert [(p, digest) for p, digest, _ in state.empty_confirmation_previews] == [
        (1, "a" * 64),
        (2, "b" * 64),
    ]


def test_legacy_owner_blank_still_needs_explicit_confirmation():
    manifest = _manifest()
    first = replace(
        manifest.trays[0],
        stage=TrayStage.EXPORTED,
        auto="held",
        slides=(SlideRecord(1, status=SlideStatus.BLANK, owner_decided=True),),
    )
    state = _build(replace(manifest, trays=(first,)))
    assert state.empty_confirmation_tray == 1
    assert state.held_tray is None
