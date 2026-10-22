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

from opticfilm_linux.batch.enhance import options_for
from opticfilm_linux.batch.model import BatchManifest, SlideStatus, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.output import Format, OutputSpec, write_slide
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


def _mark_exported(store: BatchStore, number: int, position: int, version: int) -> bool:
    """Mark one slide exported, unless the tray was re-scanned since the export began."""
    applied = False

    def change(manifest: BatchManifest) -> BatchManifest:
        nonlocal applied
        trays = []
        for tray in manifest.trays:
            if tray.number == number and tray.sweep_version == version:
                applied = True
                slides = tuple(
                    dataclasses.replace(s, exported=True) if s.position == position else s
                    for s in tray.slides
                )
                tray = dataclasses.replace(tray, slides=slides)
            trays.append(tray)
        return dataclasses.replace(manifest, trays=tuple(trays))

    store.update(change)
    return applied


def _finish_tray_if_done(store: BatchStore, number: int) -> None:
    tray = _tray(store.snapshot(), number)
    real = [s for s in tray.slides if s.status is not SlideStatus.BLANK]
    done = all(s.status is SlideStatus.ACCEPTED and s.exported for s in real)
    if tray.stage is TrayStage.REVIEW and tray.slides and done:
        store.update_tray(number, stage=TrayStage.EXPORTED)


def _pending_slides(tray: TrayRecord) -> list:
    if tray.raw_removed:
        return []
    return [s for s in tray.slides if s.status is SlideStatus.ACCEPTED and not s.exported]


def _export_tray(
    store: BatchStore,
    tray: TrayRecord,
    spec: OutputSpec,
    report: Callable[[Path], None],
    cancel: threading.Event | None,
) -> bool:
    """Write a tray's pending slides; returns False if cancelled before finishing."""
    pending = _pending_slides(tray)
    if not pending:
        return True
    defaults = store.snapshot().defaults
    sweep = load_sweep(store.sweep_dir(tray.number, tray.sweep_version))
    slides = split_slides(sweep)
    for record in pending:
        if cancel is not None and cancel.is_set():
            return False
        options = options_for(defaults, tray.mode, record.settings)
        image = enhance_slide(slides[record.position - 1], options, sweep.dpi)
        path = write_slide(image, spec)
        report(path)
        if not _mark_exported(store, tray.number, record.position, tray.sweep_version):
            return True  # re-scanned meanwhile: these slides are stale, the next export redoes it
    return True


def export_batch(
    store: BatchStore,
    directory: Path,
    fmt: Format = "tiff16",
    on_progress: Callable[[ExportProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> ExportResult:
    manifest = store.snapshot()
    spec = OutputSpec(directory=directory, fmt=fmt, album=manifest.album, date=manifest.date)
    exportable = [t for t in manifest.trays if t.stage in (TrayStage.REVIEW, TrayStage.EXPORTED)]
    total = sum(len(_pending_slides(t)) for t in exportable)
    written: list[Path] = []

    def report(path: Path | None) -> None:
        if path is not None:
            written.append(path)
        if on_progress is not None:
            on_progress(ExportProgress(done=len(written), total=total))

    report(None)
    for tray in exportable:
        finished = _export_tray(store, tray, spec, report, cancel)
        _finish_tray_if_done(store, tray.number)
        if not finished:
            return ExportResult(written=tuple(written), cancelled=True)
    return ExportResult(written=tuple(written))


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


def cleanup_raw(store: BatchStore, number: int) -> int:
    """Delete an exported tray's raw sweeps (all versions). Returns the bytes freed."""
    tray = next((t for t in store.snapshot().trays if t.number == number), None)
    if tray is None:
        raise BatchError(f"no tray {number} in this batch")
    if tray.stage is not TrayStage.EXPORTED:
        raise CleanupRefused(
            f"tray {number} is {tray.stage}; only exported trays can be cleaned up"
        )
    folders = _checked_sweep_folders(store, number)
    freed = sum(_folder_size(path) for path in folders)
    for path in folders:
        shutil.rmtree(path)
    store.update_tray(number, raw_removed=True)
    return freed


def freeable_trays(store: BatchStore) -> tuple[FreeableTray, ...]:
    """Fully exported trays whose raw scans are still on disk, with the space they use."""
    found = []
    for tray in store.snapshot().trays:
        if tray.stage is TrayStage.EXPORTED and not tray.raw_removed:
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
