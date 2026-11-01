"""Immutable snapshots of everything the window shows, built from the manifest."""

from collections.abc import Mapping
from dataclasses import dataclass

from opticfilm_linux.app.messages import flag_text, holder_latch_text, scanner_text, stage_text
from opticfilm_linux.batch.model import (
    BatchManifest,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.holder_latch import HolderLatch


@dataclass(frozen=True)
class SlideCard:
    position: int
    status: SlideStatus
    flags: tuple[str, ...]  # plain sentences
    settings: SlideSettings
    owner_decided: bool
    exported: bool
    updating: bool
    preview_token: int  # modified time of the "after" preview; 0 when there is none


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
    can_eject: bool = False
    can_load_next: bool = False
    next_strip_enabled: bool = False  # the experimental non-interactive load is switched on
    holder_busy: bool = False  # an eject or load is queued or running
    can_confirm_scanner: bool = False  # a command was cut off; the owner may confirm a reset
    holder_latch_text: str = holder_latch_text(HolderLatch.UNKNOWN)
    can_confirm_holder_latched: bool = False
    holder_latch_warning: bool = False


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


def _tray_row(
    tray: TrayRecord,
    progress: Mapping[int, float],
    scan_started: Mapping[int, float],
    now: float,
) -> TrayRow:
    started = scan_started.get(tray.number)
    elapsed = now - started if tray.stage is TrayStage.SCANNING and started is not None else None
    return TrayRow(
        number=tray.number,
        stage=tray.stage,
        stage_text=stage_text(tray.stage),
        progress=progress.get(tray.number),
        elapsed_s=elapsed,
        error=tray.error,
        needs_look=sum(1 for s in tray.slides if s.status is SlideStatus.NEEDS_LOOK),
        raw_removed=tray.raw_removed,
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
    )


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
    next_strip: bool = False,
    jobs_running: bool = False,
    holder_latch: HolderLatch = HolderLatch.UNKNOWN,
) -> AppState:
    trays = manifest.trays if manifest is not None else ()
    current = next((t for t in trays if t.number == selected), None)
    idle = (
        manifest is not None
        and holder_control
        and not (holder_busy or scan_pending or readonly or jobs_running)
    )
    return AppState(
        batch_name=manifest.name if manifest is not None else None,
        readonly=readonly,
        scanner=scanner,
        scanner_text=scanner_text(scanner),
        can_scan=manifest is not None
        and scanner is ScannerStatus.READY
        and holder_latch is not HolderLatch.RELEASED
        and not scan_pending
        and not holder_busy
        and not readonly,
        scan_pending=scan_pending,
        trays=tuple(_tray_row(t, progress, scan_started, now) for t in trays),
        selected=current.number if current is not None else None,
        slides=tuple(_slide_card(current, s, preview_tokens, updating) for s in current.slides)
        if current is not None
        else (),
        banner=banner,
        export=export,
        free_space=free_space,
        default_rotation=manifest.defaults.rotation if manifest is not None else 0,
        default_mirror=manifest.defaults.mirror if manifest is not None else False,
        can_eject=idle and scanner in (ScannerStatus.READY, ScannerStatus.NOT_READY),
        can_load_next=idle and next_strip and scanner is ScannerStatus.NOT_READY,
        next_strip_enabled=next_strip,
        can_confirm_scanner=scanner is ScannerStatus.INTERRUPTED
        and not (holder_busy or scan_pending or readonly or jobs_running),
        holder_busy=holder_busy,
        holder_latch_text=holder_latch_text(holder_latch),
        can_confirm_holder_latched=idle and holder_latch is HolderLatch.RELEASED,
        holder_latch_warning=manifest is not None and holder_latch is HolderLatch.UNKNOWN,
    )
