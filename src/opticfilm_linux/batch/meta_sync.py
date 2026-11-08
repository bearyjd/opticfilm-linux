"""Which exported JPEGs no longer say what the batch says about their description and date.

This is computed, never stored as a flag: the receipt of each export keeps the signature of the
description and date fields written into the file (``ExportRecord.meta_signature``), and a slide
is out of date when the signature of what it resolves to now differs. A flag set at edit time
would miss batch and tray edits, which change every inheriting slide without touching it, and an
edit made while that tray's export was running, whose receipt carries the values it wrote.

A receipt made before signatures existed has an empty one, which equals the signature of no
fields: such a slide is out of date only if it now resolves to a description or a date.

``update_exported_metadata`` is the one way such files are brought up to date: on the owner's
request (the Update JPEGs button) or, with the app setting on, by the controller after an edit
that made some out of date. Each file is checked against its receipt first, so a file the
owner or another program changed is reported and left alone. The receipt then gets the new hash,
size and signature, the signature taken from the same snapshot whose values were written, so an
edit made during the update still shows as out of date afterwards.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path

from opticfilm_linux.batch.export_contract import _export_receipt
from opticfilm_linux.batch.model import (
    BatchManifest,
    ExportRecord,
    SlideKey,
    SlideRecord,
    SlideStatus,
    TrayRecord,
)
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.jpeg_rewrite import (
    clean_stale_temporaries,
    fingerprint,
    rewrite_xmp_file,
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


@dataclass(frozen=True)
class MetaUpdateProgress:
    done: int
    total: int


@dataclass(frozen=True)
class MetaUpdateResult:
    updated: tuple[SlideKey, ...]
    failed: tuple[tuple[SlideKey, str], ...]  # each with a plain reason; never fatal


def _find(manifest: BatchManifest, key: SlideKey) -> tuple[TrayRecord, SlideRecord] | None:
    for tray in manifest.trays:
        if tray.number == key[0]:
            for slide in tray.slides:
                if slide.position == key[1]:
                    return tray, slide
    return None


def _store_receipt(store: BatchStore, key: SlideKey, old: ExportRecord, new: ExportRecord) -> None:
    stored = False

    def renew(slide: SlideRecord) -> SlideRecord:
        nonlocal stored
        if slide.position != key[1] or not slide.exports or slide.exports[-1] != old:
            return slide
        stored = True
        exports = (*slide.exports[:-1], new)
        return dataclasses.replace(slide, exports=exports, export_sha256=new.sha256)

    def change(manifest: BatchManifest) -> BatchManifest:
        trays = tuple(
            dataclasses.replace(t, slides=tuple(renew(s) for s in t.slides))
            if t.number == key[0]
            else t
            for t in manifest.trays
        )
        return dataclasses.replace(manifest, trays=trays)

    store.update(change)
    if not stored:
        raise BatchError("the slide was exported again meanwhile; its receipt was left as it is")


def _finish_interrupted(
    store: BatchStore, key: SlideKey, old: ExportRecord, path: Path
) -> ExportRecord | None:
    """If an earlier rewrite replaced the file but its receipt was never finished, finish it
    now (so planning the next rewrite cannot lose the hash the file has) and return it."""
    if not old.pending_sha256 or fingerprint(path) != (old.pending_sha256, old.pending_size):
        return None
    finished = dataclasses.replace(
        old,
        sha256=old.pending_sha256,
        size_bytes=old.pending_size,
        pending_sha256="",
        pending_size=0,
    )
    _store_receipt(store, key, old, finished)
    return finished


def _update_one(store: BatchStore, key: SlideKey) -> bool:
    """Rewrite one file from one snapshot; False when there was nothing (left) to do.

    The planned hash goes into the receipt before the file is replaced, so a crash after the
    rename leaves a receipt that names both the old and the planned file: the next pass accepts
    either, and finishes the receipt."""
    manifest = store.snapshot()
    found = _find(manifest, key)
    if found is None or not metadata_out_of_date(manifest, *found):
        return False
    tray, slide = found
    path, _, digest, size = _export_receipt(slide)
    estimate = _estimate_fields(manifest, tray, slide)
    old = slide.exports[-1]
    clean_stale_temporaries(path)
    finished = _finish_interrupted(store, key, old, path)
    if finished is not None:
        old = finished
        digest, size = finished.sha256, finished.size_bytes
    current = old

    def record_plan(new_digest: str, new_size: int) -> None:
        nonlocal current
        planned = dataclasses.replace(old, pending_sha256=new_digest, pending_size=new_size)
        _store_receipt(store, key, current, planned)
        current = planned

    new_digest, new_size = rewrite_xmp_file(
        path,
        digest,
        size or 0,
        estimate,
        pending=("", 0),
        before_replace=record_plan,
    )
    new = dataclasses.replace(
        old,
        sha256=new_digest,
        size_bytes=new_size,
        meta_signature=estimate_signature(estimate),
        pending_sha256="",
        pending_size=0,
    )
    _store_receipt(store, key, current, new)
    return True


def update_exported_metadata(
    store: BatchStore,
    keys: Collection[SlideKey] | None = None,
    *,
    on_progress: Callable[[MetaUpdateProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> MetaUpdateResult:
    """Bring every out-of-date exported JPEG (of ``keys``, or of the batch) up to date."""
    targets = [k for k in out_of_date_keys(store.snapshot()) if keys is None or k in keys]
    updated: list[SlideKey] = []
    failed: list[tuple[SlideKey, str]] = []
    for done, key in enumerate(targets, start=1):
        if cancel is not None and cancel.is_set():
            break
        try:
            if _update_one(store, key):
                updated.append(key)
        except (OSError, ValueError, BatchError) as exc:
            failed.append((key, str(exc)))
        if on_progress is not None:
            on_progress(MetaUpdateProgress(done, len(targets)))
    return MetaUpdateResult(tuple(updated), tuple(failed))
