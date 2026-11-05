"""Which exported JPEGs no longer say what the batch says about their description and date.

This is computed, never stored as a flag: the receipt of each export keeps the signature of the
description and date fields written into the file (``ExportRecord.meta_signature``), and a slide
is out of date when the signature of what it resolves to now differs. A flag set at edit time
would miss batch and tray edits, which change every inheriting slide without touching it, and an
edit made while that tray's export was running, whose receipt carries the values it wrote.

A receipt made before signatures existed has an empty one, which equals the signature of no
fields: such a slide is out of date only if it now resolves to a description or a date.
"""

from __future__ import annotations

from pathlib import Path

from opticfilm_linux.batch.model import (
    BatchManifest,
    SlideKey,
    SlideRecord,
    SlideStatus,
    TrayRecord,
)
from opticfilm_linux.provenance import _estimate_fields, estimate_signature

JPEG_SUFFIXES = (".jpg", ".jpeg")


def current_signature(manifest: BatchManifest, tray: TrayRecord, slide: SlideRecord) -> str:
    return estimate_signature(_estimate_fields(manifest, tray, slide))


def metadata_out_of_date(manifest: BatchManifest, tray: TrayRecord, slide: SlideRecord) -> bool:
    """True for an accepted, exported JPEG whose description or date fields are not current."""
    if slide.status is not SlideStatus.ACCEPTED or not slide.exported or not slide.exports:
        return False
    record = slide.exports[-1]
    if Path(record.path).suffix.lower() not in JPEG_SUFFIXES:
        return False  # only JPEGs carry the fields
    return record.meta_signature != current_signature(manifest, tray, slide)


def out_of_date_keys(manifest: BatchManifest) -> tuple[SlideKey, ...]:
    return tuple(
        (tray.number, slide.position)
        for tray in manifest.trays
        for slide in tray.slides
        if metadata_out_of_date(manifest, tray, slide)
    )
