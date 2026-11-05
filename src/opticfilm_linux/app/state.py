"""Immutable snapshots of everything the window shows, built from the manifest."""

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import Literal

from opticfilm_linux.app.messages import flag_text, holder_latch_text, scanner_text, stage_text
from opticfilm_linux.app.selection import Selection, prune, selectable_keys
from opticfilm_linux.batch.cleanup_recovery import interrupted_cleanup_guidance
from opticfilm_linux.batch.empty import (
    empty_frame_fingerprints,
    is_empty_confirmed,
    is_known_empty,
)
from opticfilm_linux.batch.metadata import resolve
from opticfilm_linux.batch.model import (
    NO_DELIVERY,
    BatchManifest,
    SlideKey,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.output import Format, JpegSize
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.holder_latch import HolderLatch
from opticfilm_linux.scanner.select import next_strip_enabled


@dataclass(frozen=True)
class SlideCard:
    position: int
    status: SlideStatus
    flags: tuple[str, ...]  # plain sentences
    settings: SlideSettings
    owner_decided: bool
    exported: bool
    updating: bool
    preview_token: int  # cached version of the "after" preview; 0 when there is none
    before_preview_token: int = 0


@dataclass(frozen=True)
class TrayRow:
    number: int
    stage: TrayStage
    stage_text: str
    progress: float | None
    elapsed_s: float | None
    error: str | None
    needs_look: int
    raw_removed: bool
    auto: str = ""
    auto_failed: tuple[int, ...] = ()
    auto_flagged: tuple[int, ...] = ()
    auto_error: str = ""
    can_recover_raw: bool = False
    slides: tuple[SlideCard, ...] = ()


@dataclass(frozen=True)
class Banner:
    message: str
    detail: str = ""


@dataclass(frozen=True)
class ExportView:
    running: bool
    done: int
    total: int
    finished: bool = False
    written: int = 0
    cancelled: bool = False
    already_exported: int = 0
    not_accepted: int = 0
    raw_freed: int = 0
    message: str = ""


@dataclass(frozen=True)
class FreeSpaceView:
    trays: tuple[tuple[int, int], ...]  # (tray number, bytes)
    total_bytes: int
    running: bool = False
    done: int = 0
    total: int = 0
    freed: int | None = None
    message: str = ""


LoadPhase = Literal["idle", "jogging", "await_reinsert", "loading", "done", "failed"]


@dataclass(frozen=True)
class SelectionSummary:
    count: int = 0
    accepted: int = 0
    settings: tuple[tuple[str, object], ...] = ()  # None means mixed or automatic
    mixed: tuple[str, ...] = ()
    trays: tuple[int, ...] = ()
    # Resolved description and date ("1978-07"; "" none) of the selection, or of the batch when
    # nothing is selected; None means mixed. Levels: "batch", "tray", "slide", "" or None.
    meta: tuple[tuple[str, str | None], ...] = ()
    meta_levels: tuple[tuple[str, str | None], ...] = ()
    meta_note: str = ""  # a common note on an unusable stored value, else ""
    batch_meta: tuple[tuple[str, str], ...] = ()  # the batch default, for "Next tray"
    meta_scope: str = ""  # what a strip edit changes: "slides", "tray" (selected) or "batch"


def _common(values):
    return values[0] if all(value == values[0] for value in values) else None


def _meta_fields(manifest, pairs, selection: Selection) -> dict:
    """Common-or-mixed resolved metadata of ``(tray, slide)`` pairs, else of the batch."""
    if manifest is None:
        return {}
    batch = resolve(manifest, TrayRecord(0, TrayStage.REVIEW))
    found = [resolve(manifest, tray, slide) for tray, slide in pairs] or [batch]

    def text(meta):
        return meta.date.text() if meta.date else ""

    notes = [" ".join(filter(None, (m.legacy_note, m.description_note))) for m in found]
    return {
        "meta": (
            ("description", _common([m.description for m in found])),
            ("date", _common([text(m) for m in found])),
        ),
        "meta_levels": (
            ("description", _common([m.description_level for m in found])),
            ("date", _common([m.date_level for m in found])),
        ),
        "meta_note": _common(notes) or "",
        "batch_meta": (("description", batch.description), ("date", text(batch))),
        "meta_scope": "slides" if pairs else "tray" if selection.trays else "batch",
    }


def selection_fields(manifest, selection: Selection) -> dict:
    selection = prune(selection, selectable_keys(manifest) if manifest else ())
    pairs = (
        tuple(
            (tray, slide)
            for tray in manifest.trays
            for slide in tray.slides
            if (tray.number, slide.position) in selection.keys
        )
        if manifest
        else ()
    )
    slides = tuple(slide for _, slide in pairs)
    common = (
        tuple(
            (
                field.name,
                getattr(slides[0].settings, field.name)
                if all(
                    getattr(slide.settings, field.name) == getattr(slides[0].settings, field.name)
                    for slide in slides
                )
                else None,
            )
            for field in fields(SlideSettings)
        )
        if slides
        else ()
    )
    return {
        "selection": selection.keys,
        "focus": selection.focus,
        "selection_summary": SelectionSummary(
            len(slides),
            sum(slide.status is SlideStatus.ACCEPTED for slide in slides),
            common,
            tuple(
                name
                for name, value in common
                if value is None
                and any(
                    getattr(slide.settings, name) != getattr(slides[0].settings, name)
                    for slide in slides
                )
            ),
            tuple(sorted({number for number, _ in selection.keys} | selection.trays, reverse=True)),
            **_meta_fields(manifest, pairs, selection),
        ),
    }


@dataclass(frozen=True)
class AppState:
    batch_name: str | None
    readonly: bool
    scanner: ScannerStatus
    scanner_text: str
    can_scan: bool
    scan_pending: bool
    trays: tuple[TrayRow, ...]
    selected: int | None
    slides: tuple[SlideCard, ...]
    banner: Banner | None
    export: ExportView | None
    free_space: FreeSpaceView | None
    default_rotation: int = 0  # the batch's orientation, applied to every slide
    default_mirror: bool = False
    load_phase: LoadPhase = "idle"
    load_exit_code: int | None = None
    can_first_load: bool = False
    can_eject: bool = False
    can_load_next: bool = False
    next_strip_enabled: bool = True  # non-interactive load is enabled unless switched off
    holder_busy: bool = False  # an eject or load is queued or running
    can_confirm_scanner: bool = False  # a command was cut off; the owner may confirm a reset
    export_dir: str = ""
    export_format: Format = "jpeg"
    jpeg_size: JpegSize = "full"
    batch_session: int = 0  # changes on every attach, including a reopen of the same batch
    collection: str = ""
    box: int = 1
    section: str = "a"
    section_size: int = 140
    can_finish_box: bool = False
    holder_latch_text: str = holder_latch_text(HolderLatch.UNKNOWN)
    can_confirm_holder_latched: bool = False
    holder_latch_warning: bool = False
    auto_export: bool = False
    auto_status: str = ""
    auto_flagged: int = 0
    auto_failed: int = 0
    empty_confirmation_tray: int | None = None
    empty_confirmation_count: int = 0
    empty_confirmation_sweep_version: int = 0
    empty_confirmation_previews: tuple[tuple[int, str, int], ...] = ()
    held_tray: int | None = None
    selection: frozenset[SlideKey] = frozenset()
    focus: SlideKey | None = None
    selection_summary: SelectionSummary = SelectionSummary()
    undo_label: str = ""


EMPTY_STATE = AppState(
    batch_name=None,
    readonly=False,
    scanner=ScannerStatus.UNKNOWN,
    scanner_text=scanner_text(ScannerStatus.UNKNOWN),
    can_scan=False,
    scan_pending=False,
    trays=(),
    selected=None,
    slides=(),
    banner=None,
    export=None,
    free_space=None,
)


def _tray_error(tray, raw_pending, raw_recoverable):
    if not tray.raw_removed or tray.number not in raw_pending:
        return tray.error
    guidance = interrupted_cleanup_guidance(tray.number, recoverable=tray.number in raw_recoverable)
    return f"{tray.error} {guidance}" if tray.error else guidance


def _tray_row(
    tray: TrayRecord,
    progress: Mapping[int, float],
    scan_started: Mapping[int, float],
    now: float,
    raw_pending: frozenset[int] = frozenset(),
    raw_recoverable: frozenset[int] = frozenset(),
) -> TrayRow:
    started = scan_started.get(tray.number)
    elapsed = now - started if tray.stage is TrayStage.SCANNING and started is not None else None
    return TrayRow(
        number=tray.number,
        stage=tray.stage,
        stage_text=(
            "Raw removal pending"
            if tray.raw_removed and tray.number in raw_pending
            else stage_text(tray.stage)
        ),
        progress=progress.get(tray.number),
        elapsed_s=elapsed,
        error=_tray_error(tray, raw_pending, raw_recoverable),
        needs_look=sum(1 for s in tray.slides if s.status is SlideStatus.NEEDS_LOOK),
        raw_removed=tray.raw_removed,
        can_recover_raw=tray.raw_removed and tray.number in raw_recoverable,
        auto=tray.auto,
        auto_failed=tray.auto_failed,
        auto_flagged=tray.auto_flagged,
        auto_error=tray.auto_error,
    )


def _slide_card(
    tray: TrayRecord,
    slide: SlideRecord,
    tokens: Mapping[tuple[int, int], int],
    updating: frozenset[tuple[int, int]],
) -> SlideCard:
    key = (tray.number, slide.position)
    return SlideCard(
        position=slide.position,
        status=slide.status,
        flags=tuple(flag_text(f) for f in slide.flags),
        settings=slide.settings,
        owner_decided=slide.owner_decided,
        exported=slide.exported,
        updating=key in updating,
        preview_token=tokens.get(key, 0),
        before_preview_token=tokens.get((*key, "before"), 0),
    )


def _batch_fields(
    manifest: BatchManifest | None,
    selected: int | None,
    preview_tokens: Mapping[tuple[int, int], int],
    updating: frozenset[tuple[int, int]],
) -> dict:
    trays = manifest.trays if manifest is not None else ()
    current = next((t for t in trays if t.number == selected), None)
    return {
        "batch_name": manifest.name if manifest is not None else None,
        "selected": current.number if current is not None else None,
        "slides": tuple(_slide_card(current, s, preview_tokens, updating) for s in current.slides)
        if current is not None
        else (),
        "default_rotation": manifest.defaults.rotation if manifest is not None else 0,
        "default_mirror": manifest.defaults.mirror if manifest is not None else False,
        "export_dir": manifest.defaults.export_dir if manifest is not None else "",
        "export_format": manifest.defaults.export_format if manifest is not None else "jpeg",
        "jpeg_size": manifest.defaults.jpeg_size if manifest is not None else "full",
        "collection": manifest.collection if manifest is not None else "",
        "box": manifest.box if manifest is not None else 1,
        "section": manifest.section if manifest is not None else "a",
        "section_size": manifest.section_size if manifest is not None else 140,
    }


def _scanner_fields(
    manifest: BatchManifest | None,
    scanner: ScannerStatus,
    scan_pending: bool,
    readonly: bool,
    holder_control: bool,
    holder_busy: bool,
    next_strip: bool | None,
    jobs_running: bool,
    holder_latch: HolderLatch,
) -> dict:
    busy = holder_busy or scan_pending or readonly or jobs_running
    idle = manifest is not None and holder_control and not (holder_busy or scan_pending or readonly)
    return {
        "scanner": scanner,
        "scanner_text": scanner_text(scanner),
        "can_scan": manifest is not None
        and scanner is ScannerStatus.READY
        and holder_latch is not HolderLatch.RELEASED
        and not scan_pending
        and not holder_busy
        and not readonly,
        "scan_pending": scan_pending,
        "can_eject": idle and scanner in (ScannerStatus.READY, ScannerStatus.NOT_READY),
        "can_load_next": idle
        and (next_strip_enabled() if next_strip is None else next_strip)
        and scanner is ScannerStatus.NOT_READY,
        "next_strip_enabled": next_strip_enabled() if next_strip is None else next_strip,
        "can_confirm_scanner": scanner is ScannerStatus.INTERRUPTED and not busy,
        "holder_busy": holder_busy,
        "holder_latch_text": holder_latch_text(holder_latch),
        "can_confirm_holder_latched": idle
        and not jobs_running
        and holder_latch is HolderLatch.RELEASED,
        "holder_latch_warning": manifest is not None and holder_latch is HolderLatch.UNKNOWN,
    }


def _can_finish_box(
    manifest: BatchManifest | None,
    busy: bool,
    runner_busy: bool,
    updating: frozenset[tuple[int, int]],
) -> bool:
    return (
        bool(manifest and manifest.collection)
        and not runner_busy
        and not busy
        and not updating
        and not any(
            t.stage in (TrayStage.SCANNING, TrayStage.SCANNED, TrayStage.ENHANCING)
            for t in (manifest.trays if manifest else ())
        )
    )


def _tray_rows(manifest, progress, scan_started, now, raw_pending, raw_recoverable):
    return tuple(
        _tray_row(t, progress, scan_started, now, raw_pending, raw_recoverable)
        for t in (manifest.trays if manifest else ())
    )


def _sheet_trays(manifest, rows, tokens, updating):
    cards = {
        tray.number: tuple(_slide_card(tray, slide, tokens, updating) for slide in tray.slides)
        for tray in (manifest.trays if manifest else ())
    }
    return tuple(replace(row, slides=cards[row.number]) for row in rows)


def _selected_batch_fields(manifest, selected, preview_tokens, updating, selection):
    return {
        **_batch_fields(manifest, selected, preview_tokens, updating),
        **selection_fields(manifest, selection or Selection()),
    }


def build_state(
    manifest: BatchManifest | None,
    *,
    preview_tokens: Mapping[tuple[int, int], int],
    scanner: ScannerStatus,
    scan_pending: bool,
    readonly: bool,
    selected: int | None,
    updating: frozenset[tuple[int, int]],
    progress: Mapping[int, float],
    scan_started: Mapping[int, float],
    now: float,
    banner: Banner | None,
    export: ExportView | None,
    free_space: FreeSpaceView | None,
    holder_control: bool = False,
    holder_busy: bool = False,
    next_strip: bool | None = None,
    jobs_running: bool = False,
    batch_session: int = 0,
    runner_busy: bool = False,
    holder_latch: HolderLatch = HolderLatch.UNKNOWN,
    raw_pending: frozenset[int] = frozenset(),
    raw_recoverable: frozenset[int] = frozenset(),
    selection: Selection | None = None,
) -> AppState:
    busy = holder_busy or scan_pending or readonly or jobs_running
    rows = _tray_rows(manifest, progress, scan_started, now, raw_pending, raw_recoverable)
    scanner_state = manifest, scanner, scan_pending, readonly
    holder_state = holder_control, holder_busy, next_strip, jobs_running, holder_latch
    return AppState(
        **_selected_batch_fields(manifest, selected, preview_tokens, updating, selection),
        **_auto_fields(manifest, readonly, preview_tokens, raw_pending),
        **_scanner_fields(*scanner_state, *holder_state),
        batch_session=batch_session,
        readonly=readonly,
        trays=_sheet_trays(manifest, rows, preview_tokens, updating),
        banner=banner,
        export=export,
        free_space=free_space,
        can_finish_box=_can_finish_box(manifest, busy, runner_busy, updating),
    )


def _release_needed(manifest: BatchManifest) -> bool:
    defaults = replace(manifest.defaults, auto_export=False, keep_raw=False)
    return not manifest.first_tray_released or manifest.released_render != defaults


def _release_flags(tray: TrayRecord) -> bool:
    return any(
        not is_empty_confirmed(tray, s)
        if s.status is SlideStatus.BLANK
        else (s.flags or s.position in tray.auto_flagged) and not s.owner_decided
        for s in tray.slides
    )


def _approval_candidates(trays):
    candidates = [t for t in trays if not t.raw_removed]
    original = min(trays, key=lambda t: t.number) if trays else None
    first = original
    confirmed_empty = (
        original is not None
        and bool(original.slides)
        and all(is_known_empty(original, s) for s in original.slides)
    )
    if original is not None and (original.raw_removed or confirmed_empty):
        first = next(
            (t for t in sorted(candidates, key=lambda t: t.number) if _approval_ready(t)),
            None,
        )
    empty = next(
        (
            t
            for t in candidates
            if t.stage in (TrayStage.REVIEW, TrayStage.EXPORTED)
            and any(
                s.status is SlideStatus.BLANK and not is_empty_confirmed(t, s) for s in t.slides
            )
        ),
        None,
    )
    return first, empty


def _approval_ready(tray: TrayRecord) -> bool:
    return (
        not tray.raw_removed
        and tray.stage is TrayStage.EXPORTED
        and tray.auto == "held"
        and not _release_flags(tray)
        and not any(s.preview_stale for s in tray.slides)
        and any(s.status is SlideStatus.ACCEPTED and s.exported for s in tray.slides)
        and all(
            s.status in NO_DELIVERY or (s.status is SlideStatus.ACCEPTED and s.exported)
            for s in tray.slides
        )
    )


def _auto_waiting(tray: TrayRecord, raw_pending: frozenset[int]) -> bool:
    return tray.auto in ("held", "blocked") or (
        tray.auto == "verified"
        and (bool(tray.auto_error) or (tray.raw_removed and tray.number in raw_pending))
    )


def _auto_fields(
    manifest: BatchManifest | None, readonly: bool, preview_tokens=None, raw_pending=frozenset()
) -> dict:
    if manifest is None:
        return {}
    trays = manifest.trays
    flagged = sum(len(t.auto_flagged) for t in trays)
    failed = sum(len(t.auto_failed) or int(t.auto == "failed") for t in trays)
    finished = sum(t.auto == "freed" for t in trays)
    held_count = sum(_auto_waiting(t, raw_pending) for t in trays)
    active = sum(
        t.auto in ("pending", "exported", "verified") and not _auto_waiting(t, raw_pending)
        for t in trays
    )
    first, empty = _approval_candidates(trays)
    held = (
        first.number
        if first is not None
        and first.stage is TrayStage.EXPORTED
        and first.auto == "held"
        and not first.raw_removed
        and not any(s.preview_stale for s in first.slides)
        and any(s.exported and s.status is SlideStatus.ACCEPTED for s in first.slides)
        and all(
            s.status in NO_DELIVERY or (s.status is SlideStatus.ACCEPTED and s.exported)
            for s in first.slides
        )
        and not _release_flags(first)
        and _release_needed(manifest)
        and manifest.defaults.auto_export
        and not manifest.defaults.keep_raw
        and not readonly
        else None
    )
    return {
        "auto_export": manifest.defaults.auto_export,
        "auto_flagged": flagged,
        "auto_failed": failed,
        **_empty_confirmation_fields(empty, readonly, preview_tokens or {}),
        "held_tray": held,
        "auto_status": (
            f"Automatic export: {finished} finished, {active} in progress, "
            f"{held_count} waiting, {flagged} flagged, {failed} failed"
        )
        if any(t.auto and t.auto != "off" for t in trays)
        else "",
    }


def _empty_confirmation_fields(tray, readonly, tokens):
    blanks = (
        tuple(
            s
            for s in tray.slides
            if s.status is SlideStatus.BLANK and not is_empty_confirmed(tray, s)
        )
        if tray
        else ()
    )
    fingerprints = empty_frame_fingerprints(tray) if tray else {}
    return {
        "empty_confirmation_tray": tray.number if tray and not readonly else None,
        "empty_confirmation_count": len(blanks),
        "empty_confirmation_sweep_version": tray.sweep_version if tray else 0,
        "empty_confirmation_previews": tuple(
            (
                s.position,
                fingerprints.get(s.position, ""),
                tokens.get((tray.number, s.position, "before"), 0),
            )
            for s in blanks
        ),
    }
