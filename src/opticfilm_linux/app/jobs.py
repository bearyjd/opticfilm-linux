"""Long-running jobs (export, free disk space) that report progress as immutable views."""

import threading
from collections.abc import Callable, Collection
from dataclasses import replace
from pathlib import Path

from opticfilm_linux.app.state import ExportView, FreeSpaceView
from opticfilm_linux.batch.export import (
    FreeableTray,
    export_batch,
    free_space,
    freeable_trays,
)
from opticfilm_linux.batch.model import BatchManifest, SlideKey, SlideStatus, TrayStage
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.output import Format


def run_export(
    store: BatchStore,
    directory: Path,
    fmt: Format,
    cancel: threading.Event,
    publish: Callable[[ExportView], None],
    keys: frozenset[SlideKey] | None = None,
) -> None:
    """Export accepted slides; always ends by publishing a finished view, then re-raises errors."""
    last = ExportView(True, 0, 0)
    reexport_blocked = False
    publish(last)

    def selection(manifest: BatchManifest) -> None:
        nonlocal last, reexport_blocked
        last = _export_counts(manifest, keys)
        reexport_blocked = any(
            tray.raw_removed and any(s.status is SlideStatus.ACCEPTED for s in tray.slides)
            for tray in manifest.trays
            if tray.stage in (TrayStage.REVIEW, TrayStage.EXPORTED)
        )

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
    publish(
        replace(
            last,
            running=False,
            done=last.done,
            total=last.total,
            finished=True,
            written=len(result.written),
            cancelled=result.cancelled,
            message=(
                _nothing_to_write(last, reexport_blocked)
                if not result.written and not result.cancelled
                else ""
            ),
        )
    )


def _export_counts(manifest: BatchManifest, keys: frozenset[SlideKey] | None) -> ExportView:
    already_exported = not_accepted = raw_freed = 0
    for tray in manifest.trays:
        for slide in tray.slides:
            if keys is not None and (tray.number, slide.position) not in keys:
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
    return _candidates_view(found)


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
) -> None:
    """Remove the raw scans of the confirmed trays; the final view carries the bytes freed."""
    base = _candidates_view(tuple(f for f in freeable_trays(store) if f.number in numbers))
    total = len(base.trays)

    def progress(done: int, _total: int) -> None:
        publish(FreeSpaceView(base.trays, base.total_bytes, running=True, done=done, total=total))

    publish(FreeSpaceView(base.trays, base.total_bytes, running=True, done=0, total=total))
    try:
        freed = free_space(store, on_progress=progress, cancel=cancel, only=numbers)
    except BaseException:
        publish(FreeSpaceView(base.trays, base.total_bytes, running=False, freed=0))
        raise
    publish(FreeSpaceView(base.trays, base.total_bytes, running=False, total=total, freed=freed))
