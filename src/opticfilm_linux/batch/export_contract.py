"""Pure export specifications, receipt validation, and cleanup eligibility predicates."""

import dataclasses
import hashlib
import json
from pathlib import Path

from opticfilm_linux.batch.empty import is_empty_confirmed
from opticfilm_linux.batch.layout import slide_number
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    SlideKey,
    SlideRecord,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchError
from opticfilm_linux.output import Format, JpegSize, OutputSpec, jpeg_geometry
from opticfilm_linux.provenance import jpeg_metadata


def _render_defaults(defaults: BatchDefaults) -> BatchDefaults:
    """Remembered destinations and delivery sizes do not invalidate an active render."""
    return dataclasses.replace(defaults, export_dir="", export_format="jpeg", jpeg_size="full")


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


def _stale(tray: TrayRecord) -> bool:
    return any(s.preview_stale for s in tray.slides)


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


def _release_candidate(manifest: BatchManifest, number: int) -> bool:
    first = min(manifest.trays, key=lambda tray: tray.number)
    confirmed_empty = bool(first.slides) and all(
        is_empty_confirmed(first, slide) for slide in first.slides
    )
    return number == first.number or first.raw_removed or confirmed_empty


def _still_freeable(tray: TrayRecord) -> bool:
    real = [s for s in tray.slides if s.status is not SlideStatus.BLANK]
    return (
        tray.stage is TrayStage.EXPORTED
        and not tray.raw_removed
        and not _stale(tray)
        and all(is_empty_confirmed(tray, s) for s in tray.slides if s.status is SlideStatus.BLANK)
        and all(s.status is SlideStatus.ACCEPTED and s.exported for s in real)
    )


def _unreviewed_flags(tray: TrayRecord) -> bool:
    return any(
        slide.status is not SlideStatus.BLANK
        and not slide.owner_decided
        and (slide.position in tray.auto_flagged or slide.flags)
        for slide in tray.slides
    )
