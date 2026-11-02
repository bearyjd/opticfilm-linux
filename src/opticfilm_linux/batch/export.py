"""Export accepted slides, and the one explicit way raw scans are ever deleted.

Export always recomputes a slide from its raw sweep and stored settings; previews are never the
source. A slide is marked exported only after its file is written, so a crash can at worst
produce a duplicate file on the next export, never a missing one.

Cleanup defends against application bugs, crashes, power loss, concurrent application actions,
malformed or stale manifests, and owner mistakes such as moved exports or symlinked raw folders.
It does not defend against hostile local processes racing checks within microseconds, or an
owner deliberately hand-forging export receipts in the manifest.
"""

import dataclasses
import hashlib
import json
import os
import shutil
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from opticfilm_linux.batch.enhance import RawRemoved, options_for
from opticfilm_linux.batch.layout import section_directory, slide_number
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    ExportRecord,
    Mode,
    SlideKey,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.output import Format, JpegSize, OutputSpec, jpeg_geometry, write_slide
from opticfilm_linux.pipeline.run import enhance_slide
from opticfilm_linux.pipeline.split import split_slides
from opticfilm_linux.provenance import jpeg_metadata, raw_region_sha256
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
    *,
    record: ExportRecord | None = None,
) -> Literal["marked", "stale_slide", "stale_tray"]:
    """Mark one slide exported, only if the file written still matches it.

    A re-scan (new sweep version), a mode change, a new batch orientation, or an edit made while
    the slide was rendering means the file on disk is obsolete: nothing is marked, so the next
    export redoes the slide.
    """
    outcome: Literal["marked", "stale_slide", "stale_tray"] = "stale_tray"

    def change(manifest: BatchManifest) -> BatchManifest:
        nonlocal outcome
        if _tray(manifest, number).raw_removed:
            raise RawRemoved(f"the raw scans of tray {number} were removed")
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
                        s = dataclasses.replace(
                            s,
                            exported=True,
                            export_path=record.export_path if record else "",
                            export_size=(record.width, record.height) if record else (),
                            export_sha256=record.sha256 if record else "",
                            exports=s.exports + ((record,) if record else ()),
                        )
                    slides.append(s)
                tray = dataclasses.replace(tray, slides=tuple(slides))
            trays.append(tray)
        return dataclasses.replace(manifest, trays=tuple(trays))

    store.update(change)
    return outcome


def _finish_tray_if_done(store: BatchStore, number: int) -> None:
    def done(tray: TrayRecord) -> bool:  # judged on the live tray, under the store's lock
        if tray.raw_removed:
            return False
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


def _checked_export_parent(directory: Path, root: Path, store: BatchStore) -> None:
    """Reject symlinks before creating a directory or opening an output file."""
    for component in (directory.absolute(), *directory.absolute().parents):
        if component.is_symlink():
            raise BatchError(f"export folder {component} is a symlink; choose a regular folder")
    resolved = directory.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise BatchError("export section must remain inside the export folder")
    if resolved.is_relative_to(store.directory.resolve()):
        raise BatchError("export to a folder outside the batch folder")


def _slide_spec(
    manifest: BatchManifest, tray: TrayRecord, record: SlideRecord, spec: OutputSpec
) -> OutputSpec:
    position = slide_number(tray.location, record.position) if tray.location else None
    if position is not None and position > manifest.section_size:
        raise BatchError(
            f"Tray {tray.number} slot {record.position} exceeds the section size "
            f"{manifest.section_size}; mark that empty position blank before export."
        )
    return dataclasses.replace(spec, position=position)


def _metadata_spec(
    manifest: BatchManifest, tray: TrayRecord, record: SlideRecord, slide, options, spec: OutputSpec
) -> OutputSpec:
    delivered_dpi = spec.scan_dpi
    if spec.fmt == "jpeg":
        _, _, delivered_dpi = jpeg_geometry(1, 1, spec.scan_dpi, spec.jpeg_size)
        spec = dataclasses.replace(
            spec,
            metadata=jpeg_metadata(
                manifest,
                tray,
                record,
                slide,
                options,
                spec.scan_dpi,
                delivered_dpi,
            ),
        )
    return spec


def _write_export(
    store: BatchStore,
    root: Path,
    tray: TrayRecord,
    record: SlideRecord,
    slide,
    options,
    spec: OutputSpec,
    defaults: BatchDefaults,
) -> tuple[Path, ExportRecord]:
    delivered_dpi = spec.scan_dpi
    raw_hash = spec.metadata.raw_sha256 if spec.metadata else raw_region_sha256(slide)
    image = enhance_slide(slide, options, spec.scan_dpi)
    height, width = image.shape[:2]
    if spec.fmt == "jpeg":
        width, height, delivered_dpi = jpeg_geometry(width, height, spec.scan_dpi, spec.jpeg_size)
    _checked_export_parent(spec.directory, root, store)
    receipts: list[tuple[Path, str, int]] = []
    spec = dataclasses.replace(spec, export_root=root)
    path = write_slide(
        image,
        spec,
        on_written=lambda p, digest: receipts.append((p, digest, p.stat().st_size)),
    )
    if len(receipts) != 1 or receipts[0][0] != path:
        raise BatchError("export writer did not provide a matching durable artifact record")
    exported = ExportRecord(
        str(path.relative_to(root)),
        width,
        height,
        spec.scan_dpi,
        delivered_dpi,
        f"T{tray.number:03d}S{record.position}",
        raw_sha256=raw_hash,
        export_path=str(path.absolute()),
        size_bytes=receipts[0][2],
        sha256=receipts[0][1],
        render_signature=_render_signature(defaults, root, spec.fmt, spec.jpeg_size),
    )
    return path, exported


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
    manifest = store.snapshot()
    defaults = manifest.defaults
    root = spec.directory
    if tray.location is not None:
        spec = dataclasses.replace(
            spec,
            directory=section_directory(root, tray.location),
            sequence_stride=manifest.section_size,
        )
    _checked_export_parent(spec.directory, root, store)
    sweep = load_sweep(store.sweep_dir(tray.number, tray.sweep_version))
    spec = dataclasses.replace(spec, scan_dpi=sweep.dpi)
    slides = split_slides(sweep)
    for record in pending:
        if cancel is not None and cancel.is_set():
            return False
        slide_spec = _slide_spec(manifest, tray, record, spec)
        options = options_for(defaults, tray.mode, record.settings, record.position)
        slide = slides[record.position - 1]
        slide_spec = _metadata_spec(manifest, tray, record, slide, options, slide_spec)
        path, exported = _write_export(
            store, root, tray, record, slide, options, slide_spec, defaults
        )
        report(path)
        outcome = _mark_exported(
            store,
            tray.number,
            record.position,
            tray.sweep_version,
            tray.mode,
            record.settings,
            defaults,
            record=exported,
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


class ExportVerificationFailed(BatchError):
    """A particular slide failed its decoded JPEG verification."""

    def __init__(self, number: int, position: int, reason: Exception):
        self.position = position
        super().__init__(f"tray {number} slide {position}: verification failed: {reason}")


MIN_JPEG_BYTES = 512


def _durable_digest(path: Path) -> str:
    """Read in bounded chunks and persist both the artifact and its directory entry."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _export_receipt(slide: SlideRecord) -> tuple[Path, tuple[int, ...], str, int | None]:
    if not slide.exports:
        raise ValueError(
            "legacy export has no durable receipt; re-export as JPEG before freeing raw"
        )
    record = slide.exports[-1]
    if (
        not record.export_path
        or not Path(record.export_path).is_absolute()
        or record.width <= 0
        or record.height <= 0
        or record.size_bytes <= 0
        or not record.sha256
    ):
        raise ValueError(
            "missing export record: incomplete receipt; re-export as JPEG before freeing raw"
        )
    size = (record.width, record.height)
    if (slide.export_path, slide.export_size, slide.export_sha256) != (
        record.export_path,
        size,
        record.sha256,
    ):
        raise ValueError("export record differs from current delivery")
    return Path(record.export_path), size, record.sha256, record.size_bytes


def _verify_export(tray: TrayRecord, *, jpeg_only: bool) -> None:
    if tray.stage is not TrayStage.EXPORTED or not tray.slides:
        raise BatchError(f"tray {tray.number} is not fully exported")
    for slide in tray.slides:
        if slide.status is SlideStatus.BLANK:
            continue
        try:
            if (
                slide.status is not SlideStatus.ACCEPTED
                or not slide.exported
                or slide.preview_stale
            ):
                raise ValueError("slide is not marked exported or has pending edits")
            path, size, digest, byte_length = _export_receipt(slide)
            actual_length = path.stat().st_size
            if jpeg_only and actual_length <= MIN_JPEG_BYTES:
                raise ValueError("JPEG is too short")
            if _durable_digest(path) != digest:
                raise ValueError("export digest differs from written artifact")
            if byte_length is not None and actual_length != byte_length:
                raise ValueError("export byte length differs from written artifact")
            if path.suffix.lower() in (".tif", ".tiff"):
                raise ValueError(
                    "16-bit TIFF verification is unavailable; re-export as JPEG to free raw"
                )
            with Image.open(path) as image:
                if jpeg_only and image.format != "JPEG":
                    raise ValueError("export is not a JPEG")
                image.load()
                if image.size != size:
                    raise ValueError(f"export size {image.size} differs from written {size}")
        except (OSError, ValueError) as exc:
            raise ExportVerificationFailed(tray.number, slide.position, exc) from exc


def verify_tray_export(store: BatchStore, number: int) -> None:
    """Decode JPEGs and verify write-time geometry, streamed digest and durability."""
    _verify_export(_tray(store.snapshot(), number), jpeg_only=True)


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


def _safe_receipt_paths(store: BatchStore, tray: TrayRecord) -> None:
    root = store.directory.resolve()
    for slide in tray.slides:
        if slide.status is not SlideStatus.BLANK:
            path, *_ = _export_receipt(slide)
            if path.resolve().is_relative_to(root):
                raise CleanupRefused(
                    "export is inside the batch; move/re-export it before freeing raw"
                )


def _verify_raw(store: BatchStore, tray: TrayRecord, folders: tuple[Path, ...]) -> None:
    current = store.sweep_dir(tray.number, tray.sweep_version)
    if any(
        not p.name.removeprefix("sweep-").isdigit()
        or int(p.name.removeprefix("sweep-")) > tray.sweep_version
        for p in folders
    ):
        raise CleanupRefused("raw sweep versions changed; re-export before freeing raw")
    if tray.raw_removed and tray.cleanup_sweeps:
        if any(int(p.name.removeprefix("sweep-")) not in tray.cleanup_sweeps for p in folders):
            raise CleanupRefused("unverified raw sweep appeared during interrupted removal")
        return  # persisted proof from the pre-deletion verification; raw may be partly removed
    if current not in folders:
        if tray.raw_removed and not folders:
            return
        raise CleanupRefused("current raw sweep is missing; removal pending, raw still present")
    try:
        slides = split_slides(load_sweep(current))
        for slide in tray.slides:
            if slide.status is SlideStatus.BLANK:
                continue
            if slide.exports[-1].raw_sha256 != raw_region_sha256(slides[slide.position - 1]):
                raise ValueError("export receipt does not match the current raw scan")
    except (OSError, ValueError, IndexError) as exc:
        raise CleanupRefused(
            f"raw verification failed; re-export before freeing raw: {exc}"
        ) from exc


def _checked_snapshot(store: BatchStore, tray: TrayRecord, folders: tuple[Path, ...]) -> None:
    if tuple(_checked_sweep_folders(store, tray.number)) != folders:
        raise CleanupRefused("raw sweep folders changed meanwhile; raw scans must be kept")
    _verify_export(tray, jpeg_only=False)
    _safe_receipt_paths(store, tray)
    _verify_raw(store, tray, folders)


def _delete(store: BatchStore, tray: TrayRecord, folders: tuple[Path, ...]) -> None:
    try:
        _checked_snapshot(store, tray, folders)
    except Exception:
        if not tray.raw_removed:
            store.update_tray(tray.number, raw_removed=False, cleanup_sweeps=())
        raise
    # Preserve the receipt-bound current version until all older folders have gone. A retry
    # can therefore still prove the receipt belongs to raw that has not yet been removed.
    current = store.sweep_dir(tray.number, tray.sweep_version)
    try:
        for path in sorted(folders, key=lambda p: p == current):
            shutil.rmtree(path)
    except OSError as exc:
        raise BatchError(
            f"the raw scans of tray {tray.number} were only partly removed and can no longer be "
            f"used; removal pending; free disk space again to finish: {exc}"
        ) from exc


def _release_defaults(defaults: BatchDefaults) -> BatchDefaults:
    return dataclasses.replace(defaults, keep_raw=False, auto_export=False)


def _render_signature(defaults: BatchDefaults, root: Path, fmt: Format, jpeg_size: JpegSize) -> str:
    settings = dataclasses.replace(
        _release_defaults(defaults),
        export_dir=str(root.absolute()),
        export_format=fmt,
        jpeg_size=jpeg_size,
    )
    canonical = json.dumps(dataclasses.asdict(settings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _check_render_receipts(manifest: BatchManifest, tray: TrayRecord) -> None:
    defaults = manifest.defaults
    for slide in tray.slides:
        if slide.status is SlideStatus.BLANK:
            continue
        if not slide.exports or not slide.exports[-1].render_signature:
            raise CleanupRefused(
                "export has no settings receipt; re-export before automatic cleanup"
            )
        record = slide.exports[-1]
        # Manual deliveries can precede a remembered destination. Their relative delivery
        # path still identifies the root whose settings the owner is now releasing.
        root = (
            Path(defaults.export_dir)
            if defaults.export_dir
            else (Path(record.export_path).parents[len(Path(record.path).parts) - 1])
        )
        expected = _render_signature(defaults, root, "jpeg", defaults.jpeg_size)
        if record.render_signature != expected:
            raise CleanupRefused(
                "export settings differ from released settings; re-export before cleanup"
            )


def release_first_tray(store: BatchStore, number: int) -> None:
    """Record the owner's settings release even if the first tray cannot be cleaned."""

    def release(manifest: BatchManifest) -> BatchManifest:
        if number != min(t.number for t in manifest.trays):
            raise CleanupRefused("only the first tray can release automatic cleanup")
        return dataclasses.replace(
            manifest,
            first_tray_released=True,
            released_render=_release_defaults(manifest.defaults),
        )

    store.update(release)


def _cleanup_policy(manifest: BatchManifest, number: int, *, release_first: bool) -> None:
    if _tray(manifest, number).raw_removed:
        return  # finish an already committed removal even if automatic mode was disabled
    if manifest.defaults.keep_raw:
        raise CleanupRefused("keep raw is enabled; raw scans must be kept")
    tray = _tray(manifest, number)
    unresolved = set(tray.auto_flagged) - {s.position for s in tray.slides if s.owner_decided}
    if unresolved:
        raise CleanupRefused("flagged slides require owner review before freeing raw")
    automatic = manifest.defaults.auto_export or any(
        t.auto not in ("", "off") for t in manifest.trays
    )
    released = manifest.first_tray_released and manifest.released_render == _release_defaults(
        manifest.defaults
    )
    first = min(t.number for t in manifest.trays)
    if automatic and not released and not (release_first and number == first):
        raise CleanupRefused(
            "the first held tray requires explicit owner release for these settings"
        )
    if automatic:
        _check_render_receipts(manifest, tray)


def check_cleanup_allowed(
    store: BatchStore, number: int, *, release_first: bool = False
) -> TrayRecord:
    """Freshly check policy and durable artifacts for either cleanup entry point."""
    manifest = store.snapshot()
    tray = next((t for t in manifest.trays if t.number == number), None)
    if tray is None:
        raise BatchError(f"no tray {number} in this batch")
    _cleanup_policy(manifest, number, release_first=release_first)
    if tray.stage is not TrayStage.EXPORTED:
        raise CleanupRefused(
            f"tray {number} is {tray.stage}; only exported trays can be cleaned up"
        )
    if _stale(tray):
        raise CleanupRefused(f"tray {number} still has a preview to update; try again shortly")
    folders = tuple(_checked_sweep_folders(store, number))
    _checked_snapshot(store, tray, folders)
    return tray


def cleanup_raw(store: BatchStore, number: int, *, release_first: bool = False) -> int:
    """Delete an exported tray's raw sweeps (all versions). Returns the bytes freed.

    A cleanup that failed after marking the tray removed is finished by calling this again.
    """
    tray = check_cleanup_allowed(store, number, release_first=release_first)
    folders = tuple(_checked_sweep_folders(store, number))
    _checked_snapshot(store, tray, folders)
    freed = sum(_folder_size(path) for path in folders)

    half_cleaned = _half_cleaned(store, tray)  # lock-free filesystem inspection, before update

    # Commit first, under the store's lock: once marked removed, nothing (a new batch
    # orientation, an edit) can send the tray back to review while its raw scans go.
    def commit(manifest: BatchManifest) -> BatchManifest:
        _cleanup_policy(manifest, number, release_first=release_first)
        current = _tray(manifest, number)
        if current != tray or not (
            _still_freeable(current) or half_cleaned or (current.raw_removed and not folders)
        ):
            raise CleanupRefused(f"tray {number} changed meanwhile; it is no longer fully exported")
        released = release_first and number == min(t.number for t in manifest.trays)
        updated = dataclasses.replace(
            current,
            raw_removed=True,
            cleanup_sweeps=tuple(int(p.name.removeprefix("sweep-")) for p in folders),
        )
        return dataclasses.replace(
            manifest,
            first_tray_released=manifest.first_tray_released or released,
            released_render=_release_defaults(manifest.defaults)
            if released
            else manifest.released_render,
            trays=tuple(updated if t.number == number else t for t in manifest.trays),
        )

    store.update(commit)
    _delete(store, tray, folders)
    if tray.auto:
        store.update_tray(number, auto="freed", auto_error="", auto_failed=())
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
    *,
    release_first: bool = False,
) -> int:
    """Remove the raw scans of fully exported trays; stops between trays when cancelled.

    ``only`` limits it to the trays the owner was shown and confirmed. Returns the bytes freed.
    """
    trays = tuple(t for t in freeable_trays(store) if only is None or t.number in only)
    freed = 0
    for done, tray in enumerate(trays, start=1):
        if cancel is not None and cancel.is_set():
            break
        freed += cleanup_raw(store, tray.number, release_first=release_first)
        if on_progress is not None:
            on_progress(done, len(trays))
    return freed
