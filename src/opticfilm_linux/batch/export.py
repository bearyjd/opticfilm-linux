"""Export accepted slides, and the one explicit way raw scans are ever deleted.

Export always recomputes a slide from its raw sweep and stored settings; previews are never the
source. Slides are marked exported only after writing; interrupted exports can produce duplicates.

Cleanup defends against application bugs, crashes, power loss, concurrent application actions,
malformed or stale manifests, and owner mistakes such as moved exports or symlinked raw folders.
Before committing removal, every raw split position must be represented exactly once, blank
positions require an explicit empty confirmation bound to the current sweep and raw hash,
and nonblank positions must match their current raw hash
and verified delivery receipt. The durable raw_removed marker records an attempted deletion:
whenever any sweep folder remains, subsequent invocations verify the delivery and current raw
again before deleting anything. Recorded sweep membership is not proof of valid remaining raw.
Automatic export and cleanup require ancestor sync to root; manual exports may lack durability.
Partially deleted current sweeps therefore require recovery instead of automatic deletion.
Only a tray with no remaining sweep folders can trust its removal marker without rehashing.
A live store remembers a failed postcommit verification whose rollback also failed, and must
retry that rollback before treating the tray as eligible for cleanup again.
It does not defend against hostile local processes racing checks within microseconds, or an
owner deliberately hand-forging export receipts in the manifest.
"""

import dataclasses
import hashlib
import logging
import os
import shutil
import threading
import weakref
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from opticfilm_linux.batch.cleanup_recovery import (
    _check_interrupted_cleanup,
    _cleanup_listable,
    _compare_cleanup_evidence,
    _record_cleanup_complete,
)
from opticfilm_linux.batch.cleanup_recovery import (
    recover_interrupted_cleanup as recover_interrupted_cleanup,
)
from opticfilm_linux.batch.empty import check_raw_coverage, is_empty_confirmed, scanned_slides
from opticfilm_linux.batch.enhance import RawRemoved, options_for, require_scanned
from opticfilm_linux.batch.export_contract import (
    _export_receipt,
    _metadata_spec,
    _pending_slides,
    _release_candidate,
    _release_defaults,
    _render_defaults,
    _render_signature,
    _slide_spec,
    _stale,
    _still_freeable,
    _unreviewed_flags,
)
from opticfilm_linux.batch.layout import section_directory
from opticfilm_linux.batch.model import (
    NO_DELIVERY,
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
from opticfilm_linux.output import (
    Format,
    JpegSize,
    OutputSpec,
    _sync_directories,
    jpeg_geometry,
    write_slide,
)
from opticfilm_linux.pipeline.run import enhance_slide
from opticfilm_linux.pipeline.split import split_positions
from opticfilm_linux.provenance import raw_region_sha256
from opticfilm_linux.sweepstore import load_sweep

log = logging.getLogger(__name__)

_CLEANUP_LOCK = threading.Lock()
_PENDING_CLEANUP_RESETS: weakref.WeakKeyDictionary[
    BatchStore, dict[int, tuple[BatchManifest, bool]]
] = weakref.WeakKeyDictionary()


class CleanupRefused(BatchError):
    """Raw scans were not removed: the tray is not exported, or a path looked unsafe."""


class CleanupPolicyRefused(CleanupRefused):
    """Cleanup must wait for owner policy, review, or an unfinished application action."""


class CleanupRetryRequired(CleanupRefused):
    """Fresh evidence was superseded by a concurrent change; retry from a new snapshot."""


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
    """Attach a receipt only while the slide is accepted and its render inputs still match."""
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
                    if (
                        s.position == position
                        and s.settings == settings
                        and s.status is SlideStatus.ACCEPTED
                    ):
                        outcome = "marked"
                        s = dataclasses.replace(
                            s,
                            exported=True,
                            empty_confirmed=None,
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
        real = [s for s in tray.slides if s.status not in NO_DELIVERY]
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
                    dataclasses.replace(s, exported=False, empty_confirmed=None)
                    if s.position in chosen
                    else s
                    for s in tray.slides
                )
                tray = dataclasses.replace(tray, slides=slides, stage=TrayStage.REVIEW)
            trays.append(tray)
        return dataclasses.replace(manifest, trays=tuple(trays))

    store.update(change)


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
    spec = dataclasses.replace(
        spec,
        export_root=root,
        require_directory_sync=defaults.auto_export or tray.auto not in ("", "off"),
    )
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
    slides = require_scanned(split_positions(sweep), tray.number, (r.position for r in pending))
    for record in pending:
        if cancel is not None and cancel.is_set():
            return False
        slide_spec = _slide_spec(manifest, tray, record, spec)
        options = options_for(defaults, tray.mode, record.settings, record.position)
        slide = slides[record.position]
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
    """A particular slide failed its durable export verification."""

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
    _sync_directories(path.parent, Path(path.absolute().anchor))
    return digest.hexdigest()


def _verify_export(tray: TrayRecord, *, jpeg_only: bool) -> None:
    if tray.stage is not TrayStage.EXPORTED or not tray.slides:
        raise BatchError(f"tray {tray.number} is not fully exported")
    for slide in tray.slides:
        if slide.status in NO_DELIVERY:
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
            if not jpeg_only and path.suffix.lower() in (".tif", ".tiff"):
                # The receipt hashes the exact bytes encoded through the writer's open
                # descriptor. Matching it proves the full 16-bit artifact survived intact;
                # Pillow's TIFF decoder cannot preserve every supported 16-bit RGB mode.
                continue
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
    return tray.raw_removed and any(store.tray_dir(tray.number).glob("sweep-*"))


def _safe_receipt_paths(store: BatchStore, tray: TrayRecord) -> None:
    root = store.directory.resolve()
    for slide in tray.slides:
        if slide.status not in NO_DELIVERY:
            try:
                path, *_ = _export_receipt(slide)
            except ValueError:
                continue  # verification reports malformed receipts
            if path.resolve().is_relative_to(root):
                raise CleanupRefused(
                    "export is inside the batch; move/re-export it before freeing raw"
                )


def _checked_sweep_versions(tray: TrayRecord, folders: tuple[Path, ...]) -> None:
    if any(
        not p.name.removeprefix("sweep-").isdigit()
        or int(p.name.removeprefix("sweep-")) > tray.sweep_version
        for p in folders
    ):
        raise CleanupRefused("raw sweep versions changed; re-export before freeing raw")
    if tray.raw_removed and tray.cleanup_sweeps:
        if any(int(p.name.removeprefix("sweep-")) not in tray.cleanup_sweeps for p in folders):
            raise CleanupRefused("unverified raw sweep appeared during interrupted removal")


def _verify_raw(store: BatchStore, tray: TrayRecord, folders: tuple[Path, ...]) -> None:
    current = store.sweep_dir(tray.number, tray.sweep_version)
    if current not in folders:
        raise CleanupRefused("current raw sweep is missing; cleanup refused")
    try:
        slides = split_positions(load_sweep(current))
        check_raw_coverage(tray, slides)
        for slide in scanned_slides(tray):
            current_digest = raw_region_sha256(slides[slide.position])
            if slide.status is SlideStatus.BLANK:
                if not is_empty_confirmed(tray, slide) or slide.blank_fingerprint != current_digest:
                    raise ValueError("empty confirmation does not match the current raw scan")
                continue
            if slide.exports[-1].raw_sha256 != current_digest:
                raise ValueError("export receipt does not match the current raw scan")
    except (OSError, ValueError, IndexError) as exc:
        raise CleanupRefused(
            f"raw verification failed; re-export before freeing raw: {exc}"
        ) from exc


def _checked_snapshot(store: BatchStore, tray: TrayRecord, folders: tuple[Path, ...]) -> None:
    if tuple(_checked_sweep_folders(store, tray.number)) != folders:
        raise CleanupRefused("raw sweep folders changed meanwhile; raw scans must be kept")
    _checked_sweep_versions(tray, folders)
    if tray.raw_removed and not folders:
        return  # completed removal has no remaining raw to endanger
    _safe_receipt_paths(store, tray)
    _verify_export(tray, jpeg_only=False)
    _safe_receipt_paths(store, tray)
    _verify_raw(store, tray, folders)


def _rollback_cleanup(
    store: BatchStore, before: BatchManifest, number: int, *, release_first: bool
) -> None:
    tray = _tray(before, number)
    if tray.raw_removed:
        return

    def reset(manifest: BatchManifest) -> BatchManifest:
        current = _tray(manifest, number)
        restored = dataclasses.replace(current, raw_removed=False, cleanup_sweeps=())
        own_release = release_first and manifest.released_render == _release_defaults(
            before.defaults
        )
        return dataclasses.replace(
            manifest,
            first_tray_released=before.first_tray_released
            if own_release
            else manifest.first_tray_released,
            released_render=before.released_render if own_release else manifest.released_render,
            trays=tuple(restored if t.number == number else t for t in manifest.trays),
        )

    store.update(reset)


def _delete(
    store: BatchStore,
    before: BatchManifest,
    number: int,
    folders: tuple[Path, ...],
    *,
    release_first: bool,
) -> None:
    tray = _tray(before, number)
    try:
        _checked_snapshot(store, tray, folders)
    except Exception:
        try:
            _rollback_cleanup(store, before, number, release_first=release_first)
        except Exception:
            _PENDING_CLEANUP_RESETS.setdefault(store, {})[number] = (before, release_first)
            raise
        raise
    # Verify the pre-commit tray above: this invocation must not trust its own removal marker.
    current = store.sweep_dir(tray.number, tray.sweep_version)
    try:
        for path in sorted(folders, key=lambda p: p == current):
            shutil.rmtree(path)
    except OSError as exc:
        raise BatchError(
            f"the raw scans of tray {tray.number} were only partly removed; removal pending. "
            "Verify remaining scans before recovery. Restore damaged or missing current raw "
            f"from its original raw folders, or rescan this tray: {exc}"
        ) from exc


def _check_render_receipts(manifest: BatchManifest, tray: TrayRecord) -> None:
    defaults = manifest.defaults
    for slide in tray.slides:
        if slide.status in NO_DELIVERY:
            continue
        if not slide.exports or not slide.exports[-1].render_signature:
            raise CleanupPolicyRefused(
                "export has no settings receipt; re-export before automatic cleanup"
            )
        record = slide.exports[-1]
        # Infer unremembered destinations from the delivery's relative path.
        try:
            root = Path(defaults.export_dir)
            if not defaults.export_dir:
                root = Path(record.export_path).parents[len(Path(record.path).parts) - 1]
        except IndexError as exc:
            raise CleanupPolicyRefused("invalid delivery path; re-export before cleanup") from exc
        expected = _render_signature(defaults, root, "jpeg", defaults.jpeg_size)
        if record.render_signature != expected:
            raise CleanupPolicyRefused(
                "export settings differ from released settings; re-export before cleanup"
            )


def _verify_release_evidence(manifest: BatchManifest, tray: TrayRecord) -> None:
    if tray.raw_removed:
        raise CleanupRefused("a freed first tray cannot release new settings")
    _check_blank_decisions(tray)
    if _unreviewed_flags(tray):
        raise CleanupPolicyRefused("flagged slides require owner review before releasing settings")
    _verify_export(tray, jpeg_only=True)
    if not any(s.status is SlideStatus.ACCEPTED for s in tray.slides):
        raise CleanupRefused("first tray has no verified JPEGs to approve settings")
    _check_render_receipts(manifest, tray)


def release_first_tray(store: BatchStore, number: int) -> None:
    """Approve current JPEG settings using the first tray, or one surviving its removal."""
    with _CLEANUP_LOCK:
        _retry_cleanup_resets(store)
        evidence = store.snapshot()
        if not _release_candidate(evidence, number):
            raise CleanupRefused("only the first tray can release automatic cleanup")
        _verify_release_evidence(evidence, _tray(evidence, number))

        def release(manifest: BatchManifest) -> BatchManifest:
            _compare_cleanup_evidence(manifest, evidence, number, release_first=True)
            return dataclasses.replace(
                manifest,
                first_tray_released=True,
                released_render=_release_defaults(manifest.defaults),
            )

        store.update(release)


def _check_blank_decisions(tray: TrayRecord) -> None:
    count = sum(
        s.status is SlideStatus.BLANK and not is_empty_confirmed(tray, s) for s in tray.slides
    )
    if count:
        raise CleanupPolicyRefused(
            f"{count} frame(s) look empty - confirm they are empty to free raw"
        )


def _cleanup_policy(manifest: BatchManifest, number: int, *, release_first: bool) -> None:
    if _tray(manifest, number).raw_removed:
        return  # finish an already committed removal even if automatic mode was disabled
    if manifest.defaults.keep_raw:
        raise CleanupPolicyRefused("keep raw is enabled; raw scans must be kept")
    tray = _tray(manifest, number)
    _check_blank_decisions(tray)
    if _unreviewed_flags(tray):
        raise CleanupPolicyRefused("flagged slides require owner review before freeing raw")
    automatic = manifest.defaults.auto_export or any(
        t.auto not in ("", "off") for t in manifest.trays
    )
    released = manifest.first_tray_released and manifest.released_render == _release_defaults(
        manifest.defaults
    )
    if automatic and not released and not (release_first and _release_candidate(manifest, number)):
        raise CleanupPolicyRefused(
            "the first held tray requires explicit owner release for these settings"
        )
    if automatic:
        _check_render_receipts(manifest, tray)


def _recheck_released_raw(store: BatchStore, manifest: BatchManifest, number: int) -> None:
    """A failed release rollback must not authorize another tray after reopening the batch."""
    if not manifest.first_tray_released:
        return
    for tray in manifest.trays:
        if tray.number != number and _half_cleaned(store, tray):
            try:
                _checked_snapshot(store, tray, tuple(_checked_sweep_folders(store, tray.number)))
            except BatchError as exc:
                raise CleanupPolicyRefused(
                    f"tray {tray.number}: recover intact raw, restore original folders or rescan "
                    f"before freeing other raw scans: {exc}"
                ) from exc


def check_cleanup_allowed(
    store: BatchStore, number: int, *, release_first: bool = False
) -> TrayRecord:
    """Freshly check policy and durable artifacts for either cleanup entry point."""
    manifest = store.snapshot()
    tray = next((t for t in manifest.trays if t.number == number), None)
    if tray is None:
        raise BatchError(f"no tray {number} in this batch")
    _recheck_released_raw(store, manifest, number)
    _cleanup_policy(manifest, number, release_first=release_first)
    if tray.raw_removed:
        _check_interrupted_cleanup(store, tray)
        return tray
    if tray.stage is not TrayStage.EXPORTED:
        raise CleanupPolicyRefused(
            f"tray {number} is {tray.stage}; only exported trays can be cleaned up"
        )
    if _stale(tray):
        raise CleanupPolicyRefused(
            f"tray {number} still has a preview to update; try again shortly"
        )
    folders = tuple(_checked_sweep_folders(store, number))
    _checked_snapshot(store, tray, folders)
    return tray


def cleanup_raw(store: BatchStore, number: int, *, release_first: bool = False) -> int:
    """Delete exported raw sweeps; serialize verification, commit, deletion and reporting."""
    with _CLEANUP_LOCK:
        return _cleanup_raw(store, number, release_first=release_first)


def _cleanup_raw(store: BatchStore, number: int, *, release_first: bool) -> int:
    _retry_cleanup_resets(store)
    tray = check_cleanup_allowed(store, number, release_first=release_first)
    folders = tuple(_checked_sweep_folders(store, number))
    freed = sum(_folder_size(path) for path in folders)
    before = _commit_cleanup(store, tray, folders, release_first=release_first)
    _delete(store, before, number, folders, release_first=release_first)
    _record_cleanup_complete(store, tray)
    return freed + _drop_turn_masters(store, number)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:  # removed meanwhile (a render replaced or dropped it)
        return 0


def _turn_masters(store: BatchStore, number: int) -> list[Path]:
    return sorted((store.tray_dir(number) / "previews").glob("*-master*.png"))


def _drop_turn_masters(store: BatchStore, number: int) -> int:
    """Free the lossless preview masters (a tray without raw scans is never turned again);
    returns the bytes freed."""
    freed = 0
    for path in _turn_masters(store, number):
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:  # the raw scans are gone either way; a leftover master only costs disk
            log.warning("could not remove the preview master %s", path, exc_info=True)
            continue
        freed += size
    return freed


def _retry_cleanup_resets(store: BatchStore) -> None:
    """Reset known-invalid cleanup and release markers before any cleanup or approval."""
    pending = _PENDING_CLEANUP_RESETS.get(store, {})
    for number, (before, release_first) in tuple(pending.items()):
        _rollback_cleanup(store, before, number, release_first=release_first)
        del pending[number]


def _commit_cleanup(
    store: BatchStore, tray: TrayRecord, folders: tuple[Path, ...], *, release_first: bool
) -> BatchManifest:
    evidence = store.snapshot()
    current = _tray(evidence, tray.number)
    if current != tray or not (current.raw_removed or _still_freeable(current)):
        raise CleanupRetryRequired(f"tray {tray.number} changed meanwhile; retry cleanup")
    _cleanup_policy(evidence, tray.number, release_first=release_first)
    released = (
        release_first and not current.raw_removed and _release_candidate(evidence, tray.number)
    )
    if released:
        _verify_release_evidence(evidence, current)

    def commit(manifest: BatchManifest) -> BatchManifest:
        _compare_cleanup_evidence(manifest, evidence, tray.number, release_first=release_first)
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
            trays=tuple(updated if t.number == tray.number else t for t in manifest.trays),
        )

    store.update(commit)
    return evidence


def freeable_trays(store: BatchStore, *, release_first: bool = False) -> tuple[FreeableTray, ...]:
    """Verified trays currently allowed by cleanup policy, with the raw space they use.

    Includes trays whose cleanup was interrupted (marked removed, sweep folders still there).
    """
    found = []
    for tray in store.snapshot().trays:
        candidate = _still_freeable(tray) or _half_cleaned(store, tray)
        if candidate and _cleanup_listable(store, tray.number, release_first=release_first):
            size = sum(_folder_size(p) for p in _checked_sweep_folders(store, tray.number))
            size += sum(_size(p) for p in _turn_masters(store, tray.number))
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
    candidates = freeable_trays(store, release_first=release_first)
    trays = tuple(t for t in candidates if only is None or t.number in only)
    freed = 0
    for done, tray in enumerate(trays, start=1):
        if cancel is not None and cancel.is_set():
            break
        freed += cleanup_raw(store, tray.number, release_first=release_first)
        if on_progress is not None:
            on_progress(done, len(trays))
    return freed
