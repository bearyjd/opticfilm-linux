"""Export accepted slides, and the one explicit way raw scans are ever deleted.

Export always recomputes a slide from its raw sweep and stored settings; previews are never the
source. A slide is marked exported only after its file is written, so a crash can at worst
produce a duplicate file on the next export, never a missing one.
"""

import dataclasses
import shutil
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
class ExportResult:
    written: tuple[Path, ...]


def _tray(manifest: BatchManifest, number: int) -> TrayRecord:
    return next(t for t in manifest.trays if t.number == number)


def _mark_exported(store: BatchStore, number: int, position: int) -> None:
    def change(manifest: BatchManifest) -> BatchManifest:
        trays = []
        for tray in manifest.trays:
            if tray.number == number:
                slides = tuple(
                    dataclasses.replace(s, exported=True) if s.position == position else s
                    for s in tray.slides
                )
                tray = dataclasses.replace(tray, slides=slides)
            trays.append(tray)
        return dataclasses.replace(manifest, trays=tuple(trays))

    store.update(change)


def _finish_tray_if_done(store: BatchStore, number: int) -> None:
    tray = _tray(store.snapshot(), number)
    real = [s for s in tray.slides if s.status is not SlideStatus.BLANK]
    done = all(s.status is SlideStatus.ACCEPTED and s.exported for s in real)
    if tray.stage is TrayStage.REVIEW and tray.slides and done:
        store.update_tray(number, stage=TrayStage.EXPORTED)


def _export_tray(store: BatchStore, tray: TrayRecord, spec: OutputSpec) -> list[Path]:
    pending = [s for s in tray.slides if s.status is SlideStatus.ACCEPTED and not s.exported]
    if not pending or tray.raw_removed:
        return []
    defaults = store.snapshot().defaults
    sweep = load_sweep(store.sweep_dir(tray.number, tray.sweep_version))
    slides = split_slides(sweep)
    written = []
    for record in pending:
        options = options_for(defaults, tray.mode, record.settings)
        image = enhance_slide(slides[record.position - 1], options, sweep.dpi)
        written.append(write_slide(image, spec))
        _mark_exported(store, tray.number, record.position)
    return written


def export_batch(store: BatchStore, directory: Path, fmt: Format = "tiff16") -> ExportResult:
    manifest = store.snapshot()
    spec = OutputSpec(directory=directory, fmt=fmt, album=manifest.album, date=manifest.date)
    written: list[Path] = []
    for tray in manifest.trays:
        if tray.stage in (TrayStage.REVIEW, TrayStage.EXPORTED):
            written.extend(_export_tray(store, tray, spec))
            _finish_tray_if_done(store, tray.number)
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
