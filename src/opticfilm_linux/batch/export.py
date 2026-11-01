"""Export accepted slides, and the one explicit way raw scans are ever deleted.

Export always recomputes a slide from its raw sweep and stored settings; previews are never the
source. A slide is marked exported only after its file is written, so a crash can at worst
produce a duplicate file on the next export, never a missing one.
"""

import dataclasses
import shutil
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from opticfilm_linux.batch.enhance import options_for
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    Mode,
    SlideKey,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.output import Format, JpegSize, OutputSpec, write_slide
from opticfilm_linux.pipeline.run import enhance_slide
from opticfilm_linux.pipeline.split import split_slides
from opticfilm_linux.sweepstore import load_sweep


class CleanupRefused(BatchError):
    """Raw scans were not removed: the tray is not exported, or a path looked unsafe."""


@dataclass(frozen=True)
class ExportProgress:
    done: int  # slides written so far
    total: int  # slides this export will write


@dataclass(frozen=True)
class ExportResult:
    written: tuple[Path, ...]
    cancelled: bool = False


@dataclass(frozen=True)
class FreeableTray:
    number: int
    bytes: int


def _tray(manifest: BatchManifest, number: int) -> TrayRecord:
    return next(t for t in manifest.trays if t.number == number)


def _render_defaults(defaults: BatchDefaults) -> BatchDefaults:
    """Remembered destinations and delivery sizes do not invalidate an active render."""
    return dataclasses.replace(defaults, export_dir="", export_format="jpeg", jpeg_size="full")


def _mark_exported(
    store: BatchStore,
    number: int,
    position: int,
    version: int,
    mode: Mode,
    settings: SlideSettings,
    defaults: BatchDefaults,
) -> Literal["marked", "stale_slide", "stale_tray"]:
    """Mark one slide exported, only if the file written still matches it.

    A re-scan (new sweep version), a mode change, a new batch orientation, or an edit made while
    the slide was rendering means the file on disk is obsolete: nothing is marked, so the next
    export redoes the slide.
    """
    outcome: Literal["marked", "stale_slide", "stale_tray"] = "stale_tray"

    def change(manifest: BatchManifest) -> BatchManifest:
        nonlocal outcome
        if _render_defaults(manifest.defaults) != _render_defaults(defaults):
            return manifest
        trays = []
        for tray in manifest.trays:
            if tray.number == number and tray.sweep_version == version and tray.mode == mode:
                outcome = "stale_slide"
                slides = []
                for s in tray.slides:
                    if s.position == position and s.settings == settings:
                        outcome = "marked"
                        s = dataclasses.replace(s, exported=True)
                    slides.append(s)
                tray = dataclasses.replace(tray, slides=tuple(slides))
            trays.append(tray)
        return dataclasses.replace(manifest, trays=tuple(trays))

    store.update(change)
    return outcome


def _finish_tray_if_done(store: BatchStore, number: int) -> None:
    def done(tray: TrayRecord) -> bool:  # judged on the live tray, under the store's lock
        real = [s for s in tray.slides if s.status is not SlideStatus.BLANK]
        everything = all(s.status is SlideStatus.ACCEPTED and s.exported for s in real)
        return tray.stage is TrayStage.REVIEW and bool(tray.slides) and everything

    store.update_tray_if(number, done, stage=TrayStage.EXPORTED)


def clear_exported(store: BatchStore, keys: frozenset[SlideKey] | None = None) -> None:
    """Allow chosen accepted slides to be exported again, in one atomic manifest change."""

    def change(manifest: BatchManifest) -> BatchManifest:
        trays = []
        for tray in manifest.trays:
            if tray.stage not in (TrayStage.REVIEW, TrayStage.EXPORTED):
                trays.append(tray)
                continue
            chosen = {
                s.position
                for s in tray.slides
                if s.status is SlideStatus.ACCEPTED
                and (keys is None or (tray.number, s.position) in keys)
            }
            if chosen and tray.raw_removed:
                raise BatchError(
                    f"Cannot re-export tray {tray.number}: its raw scans were removed."
                )
            if chosen:
                slides = tuple(
                    dataclasses.replace(s, exported=False) if s.position in chosen else s
                    for s in tray.slides
                )
                tray = dataclasses.replace(tray, slides=slides, stage=TrayStage.REVIEW)
            trays.append(tray)
        return dataclasses.replace(manifest, trays=tuple(trays))

    store.update(change)


def _pending_slides(tray: TrayRecord, keys: frozenset[SlideKey] | None = None) -> list[SlideRecord]:
    if tray.raw_removed:
        return []
    return [
        s
        for s in tray.slides
        if s.status is SlideStatus.ACCEPTED
        and not s.exported
        and (keys is None or (tray.number, s.position) in keys)
    ]


def _export_tray(
    store: BatchStore,
    tray: TrayRecord,
    spec: OutputSpec,
    report: Callable[[Path], None],
    cancel: threading.Event | None,
    keys: frozenset[SlideKey] | None,
) -> bool:
    """Write a tray's pending slides; returns False if cancelled before finishing."""
    pending = _pending_slides(tray, keys)
    if not pending:
        return True
    defaults = store.snapshot().defaults
    sweep = load_sweep(store.sweep_dir(tray.number, tray.sweep_version))
    spec = dataclasses.replace(spec, scan_dpi=sweep.dpi)
    slides = split_slides(sweep)
    for record in pending:
        if cancel is not None and cancel.is_set():
            return False
        options = options_for(defaults, tray.mode, record.settings, record.position)
        image = enhance_slide(slides[record.position - 1], options, sweep.dpi)
        path = write_slide(image, spec)
        report(path)
        outcome = _mark_exported(
            store,
            tray.number,
            record.position,
            tray.sweep_version,
            tray.mode,
            record.settings,
            defaults,
        )
        if outcome == "stale_tray":
            return True  # re-scanned, mode or orientation changed: the next export redoes it
    return True


def export_batch(
    store: BatchStore,
    directory: Path,
    fmt: Format = "tiff16",
    on_progress: Callable[[ExportProgress], None] | None = None,
    cancel: threading.Event | None = None,
    keys: frozenset[SlideKey] | None = None,
    on_selection: Callable[[BatchManifest], None] | None = None,
    *,
    jpeg_size: JpegSize = "full",
) -> ExportResult:
    if directory.resolve().is_relative_to(store.directory.resolve()):
        raise BatchError(f"export to a folder outside the batch folder, not {directory}")
    manifest = store.snapshot()
    if on_selection is not None:
        on_selection(manifest)
    spec = OutputSpec(
        directory=directory, fmt=fmt, album=manifest.album, date=manifest.date, jpeg_size=jpeg_size
    )
    exportable = [t for t in manifest.trays if t.stage in (TrayStage.REVIEW, TrayStage.EXPORTED)]
    total = sum(len(_pending_slides(t, keys)) for t in exportable)
    written: list[Path] = []

    def report(path: Path | None) -> None:
        if path is not None:
            written.append(path)
        if on_progress is not None:
            on_progress(ExportProgress(done=len(written), total=total))

    report(None)
    for tray in exportable:
        finished = _export_tray(store, tray, spec, report, cancel, keys)
        _finish_tray_if_done(store, tray.number)
        if not finished:
            return ExportResult(written=tuple(written), cancelled=True)
    return ExportResult(written=tuple(written))


def _stale(tray: TrayRecord) -> bool:
    return any(s.preview_stale for s in tray.slides)


def _folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _checked_sweep_folders(store: BatchStore, number: int) -> list[Path]:
    root = store.directory.resolve()
    tray_dir = store.tray_dir(number)
    if tray_dir.is_symlink() or not tray_dir.resolve().is_relative_to(root):
        raise CleanupRefused(f"tray folder {tray_dir} is a symlink or outside the batch")
    folders = sorted(tray_dir.glob("sweep-*"))
    for path in folders:
        if path.is_symlink():
            raise CleanupRefused(f"{path} is a symlink; refusing to delete through it")
        if not path.is_dir() or not path.resolve().is_relative_to(root):
            raise CleanupRefused(f"{path} is not a sweep folder inside the batch")
    return folders


def _half_cleaned(store: BatchStore, tray: TrayRecord) -> bool:
    """Marked removed by a cleanup that did not finish: its sweep folders are still there."""
    return (
        tray.stage is TrayStage.EXPORTED
        and tray.raw_removed
        and any(store.tray_dir(tray.number).glob("sweep-*"))
    )


def _delete(number: int, folders: list[Path]) -> None:
    try:
        for path in folders:
            shutil.rmtree(path)
    except OSError as exc:
        raise BatchError(
            f"the raw scans of tray {number} were only partly removed and can no longer be "
            f"used; free disk space again to finish: {exc}"
        ) from exc


def cleanup_raw(store: BatchStore, number: int) -> int:
    """Delete an exported tray's raw sweeps (all versions). Returns the bytes freed.

    A cleanup that failed after marking the tray removed is finished by calling this again.
    """
    tray = next((t for t in store.snapshot().trays if t.number == number), None)
    if tray is None:
        raise BatchError(f"no tray {number} in this batch")
    if _half_cleaned(store, tray):
        folders = _checked_sweep_folders(store, number)
        freed = sum(_folder_size(path) for path in folders)
        _delete(number, folders)
        return freed
    if tray.stage is not TrayStage.EXPORTED:
        raise CleanupRefused(
            f"tray {number} is {tray.stage}; only exported trays can be cleaned up"
        )
    if _stale(tray):
        raise CleanupRefused(f"tray {number} still has a preview to update; try again shortly")
    folders = _checked_sweep_folders(store, number)
    freed = sum(_folder_size(path) for path in folders)
    # Commit first, under the store's lock: once marked removed, nothing (a new batch
    # orientation, an edit) can send the tray back to review while its raw scans go.
    if not store.update_tray_if(number, _still_freeable, raw_removed=True):
        raise CleanupRefused(f"tray {number} changed meanwhile; it is no longer fully exported")
    _delete(number, folders)
    return freed


def _still_freeable(tray: TrayRecord) -> bool:
    real = [s for s in tray.slides if s.status is not SlideStatus.BLANK]
    return (
        tray.stage is TrayStage.EXPORTED
        and not tray.raw_removed
        and not _stale(tray)
        and all(s.status is SlideStatus.ACCEPTED and s.exported for s in real)
    )


def freeable_trays(store: BatchStore) -> tuple[FreeableTray, ...]:
    """Fully exported trays whose raw scans are still on disk, with the space they use.

    Includes trays whose cleanup was interrupted (marked removed, sweep folders still there).
    """
    found = []
    for tray in store.snapshot().trays:
        whole = tray.stage is TrayStage.EXPORTED and not tray.raw_removed and not _stale(tray)
        if whole or _half_cleaned(store, tray):
            size = sum(_folder_size(p) for p in _checked_sweep_folders(store, tray.number))
            found.append(FreeableTray(number=tray.number, bytes=size))
    return tuple(found)


def free_space(
    store: BatchStore,
    on_progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    only: Collection[int] | None = None,
) -> int:
    """Remove the raw scans of fully exported trays; stops between trays when cancelled.

    ``only`` limits it to the trays the owner was shown and confirmed. Returns the bytes freed.
    """
    trays = tuple(t for t in freeable_trays(store) if only is None or t.number in only)
    freed = 0
    for done, tray in enumerate(trays, start=1):
        if cancel is not None and cancel.is_set():
            break
        freed += cleanup_raw(store, tray.number)
        if on_progress is not None:
            on_progress(done, len(trays))
    return freed
