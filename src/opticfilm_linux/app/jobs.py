"""Long-running jobs (export, free disk space) that report progress as immutable views."""

import threading
from collections.abc import Callable, Collection
from dataclasses import replace
from pathlib import Path

from opticfilm_linux.app.raw import bytes_removed, raw_failure, raw_inventory
from opticfilm_linux.app.state import ExportView, FreeSpaceView
from opticfilm_linux.batch.empty import is_empty_confirmed
from opticfilm_linux.batch.export import (
    ExportResult,
    FreeableTray,
    _release_defaults,
    export_batch,
    free_space,
    freeable_trays,
)
from opticfilm_linux.batch.model import (
    BatchManifest,
    SlideKey,
    SlideStatus,
    TrayStage,
    advisory_only,
)
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.output import Format, JpegSize


def run_export(
    store: BatchStore,
    directory: Path,
    fmt: Format,
    cancel: threading.Event,
    publish: Callable[[ExportView], None],
    keys: frozenset[SlideKey] | None = None,
    *,
    jpeg_size: JpegSize = "full",
) -> None:
    """Export accepted slides; always ends by publishing a finished view, then re-raises errors."""
    last = ExportView(True, 0, 0)
    reexport_blocked = False
    publish(last)

    def selection(manifest: BatchManifest) -> None:
        nonlocal last, reexport_blocked
        last = _export_counts(manifest, keys)
        reexport_blocked = _reexport_blocked(manifest)

    def progress(update) -> None:
        nonlocal last
        last = replace(last, done=update.done, total=update.total)
        publish(last)

    try:
        result = export_batch(
            store,
            directory,
            fmt,
            on_progress=progress,
            cancel=cancel,
            keys=keys,
            on_selection=selection,
            jpeg_size=jpeg_size,
        )
    except BaseException:
        publish(
            replace(
                last,
                running=False,
                finished=True,
                written=last.done,
                message="Export failed. See the error message for details.",
            )
        )
        raise
    publish(_export_finished(last, result, reexport_blocked))


def _reexport_blocked(manifest: BatchManifest) -> bool:
    return any(
        tray.raw_removed and any(s.status is SlideStatus.ACCEPTED for s in tray.slides)
        for tray in manifest.trays
        if tray.stage in (TrayStage.REVIEW, TrayStage.EXPORTED)
    )


def _export_finished(last: ExportView, result: ExportResult, reexport_blocked: bool) -> ExportView:
    return replace(
        last,
        running=False,
        finished=True,
        written=len(result.written),
        cancelled=result.cancelled,
        message=_nothing_to_write(last, reexport_blocked)
        if not result.written and not result.cancelled
        else "",
    )


def _export_counts(manifest: BatchManifest, keys: frozenset[SlideKey] | None) -> ExportView:
    already_exported = not_accepted = raw_freed = 0
    for tray in manifest.trays:
        for slide in tray.slides:
            if slide.status is SlideStatus.SKIPPED or (
                keys is not None and (tray.number, slide.position) not in keys
            ):
                continue
            if tray.raw_removed:
                raw_freed += 1
            elif slide.status is not SlideStatus.ACCEPTED:
                not_accepted += 1
            elif slide.exported:
                already_exported += 1
    return ExportView(
        True,
        0,
        0,
        already_exported=already_exported,
        not_accepted=not_accepted,
        raw_freed=raw_freed,
    )


def _nothing_to_write(view: ExportView, reexport_blocked: bool) -> str:
    reasons = []
    if view.already_exported:
        reasons.append(f"{view.already_exported} accepted slides were already exported")
    if view.not_accepted:
        reasons.append(f"{view.not_accepted} slides are not accepted")
    if view.raw_freed:
        reasons.append(f"{view.raw_freed} slides have had their raw scans removed")
    reason = "; ".join(reasons) if reasons else "no accepted slides are ready to export"
    message = f"Nothing to write: {reason}."
    if reexport_blocked:
        message += " Cannot re-export all accepted: some accepted slides have removed raw scans."
    elif view.already_exported:
        message += " Use Re-export all accepted to write new numbered files."
    return message


def free_space_candidates(store: BatchStore) -> FreeSpaceView:
    found = freeable_trays(store)
    view = _candidates_view(found)
    manifest = store.snapshot()
    held = [
        str(t.number)
        for t in manifest.trays
        if not t.raw_removed
        and any(s.status is SlideStatus.BLANK and not is_empty_confirmed(t, s) for s in t.slides)
    ]
    if held and not view.trays:
        return replace(
            view,
            message="Raw scans kept for trays "
            + ", ".join(held)
            + ": review and confirm every empty frame before freeing space.",
        )
    if not view.trays:
        return replace(view, message=_cleanup_hold_message(manifest))
    return view


def _cleanup_hold_message(manifest: BatchManifest) -> str:
    remaining = [t for t in manifest.trays if not t.raw_removed]
    if not remaining:
        return ""
    reasons = []
    if manifest.defaults.keep_raw:
        reasons.append("Keep raw scans is enabled in batch settings")
    automatic = manifest.defaults.auto_export or any(t.auto not in ("", "off") for t in remaining)
    if automatic and (
        not manifest.first_tray_released
        or manifest.released_render != _release_defaults(manifest.defaults)
    ):
        reasons.append("first-tray raw removal needs owner approval")
    if any(
        (s.flags or s.position in t.auto_flagged)
        and not advisory_only(s.flags)
        and not s.owner_decided
        for t in remaining
        for s in t.slides
    ):
        reasons.append("flagged slides need owner review")
    return "Raw scans kept: " + "; ".join(reasons) + "." if reasons else ""


def _candidates_view(found: tuple[FreeableTray, ...]) -> FreeSpaceView:
    return FreeSpaceView(
        trays=tuple((f.number, f.bytes) for f in found),
        total_bytes=sum(f.bytes for f in found),
    )


def run_free_space(
    store: BatchStore,
    cancel: threading.Event,
    publish: Callable[[FreeSpaceView], None],
    numbers: Collection[int],
    *,
    release_first: bool = False,
) -> None:
    """Remove the raw scans of the confirmed trays; the final view carries the bytes freed."""
    before = raw_inventory(store, numbers)
    base = _candidates_view(
        tuple(
            FreeableTray(
                n,
                sum(
                    size for path, size in before.items() if path.is_relative_to(store.tray_dir(n))
                ),
            )
            for n in sorted(set(numbers))
        )
    )
    total = 0

    def progress(done: int, actual_total: int) -> None:
        nonlocal total
        total = actual_total
        publish(FreeSpaceView(base.trays, base.total_bytes, running=True, done=done, total=total))

    publish(FreeSpaceView(base.trays, base.total_bytes, running=True, done=0, total=total))
    try:
        freed = free_space(
            store, on_progress=progress, cancel=cancel, only=numbers, release_first=release_first
        )
    except BaseException as exc:
        _publish_cleanup_failure(store, numbers, before, base, publish, exc)
        raise
    skipped = len(base.trays) - total
    message = f"{skipped} tray(s) kept because cleanup is no longer permitted." if skipped else ""
    if cancel.is_set():
        message = "Cleanup cancelled; remaining raw scans kept."
    publish(FreeSpaceView(base.trays, base.total_bytes, total=total, freed=freed, message=message))


def _publish_cleanup_failure(store, numbers, before, base, publish, exc) -> None:
    """Reporting failure must preserve the cleanup exception and its traceback."""
    try:
        after = raw_inventory(store, numbers)
        outcome = raw_failure(store, numbers, before, after)
        freed = bytes_removed(before, after)
    except Exception as evidence_error:
        outcome = f"raw cleanup outcome unknown (could not inspect raw files: {evidence_error})"
        freed = None
    try:
        publish(
            FreeSpaceView(
                base.trays,
                base.total_bytes,
                running=False,
                freed=freed,
                message=f"Cleanup failed; {outcome}. {exc}",
            )
        )
    except Exception:
        pass  # preserve the original cleanup failure if publishing also fails
