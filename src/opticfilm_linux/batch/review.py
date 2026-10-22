"""What the owner can do in review. Each operation is one atomic manifest update.

Every explicit choice sets ``owner_decided``, which re-enhancement respects: previews and flags
are refreshed but the owner's status and settings are never overwritten.
"""

import dataclasses

from opticfilm_linux.batch.enhance import RawRemoved
from opticfilm_linux.batch.model import (
    BatchManifest,
    Mode,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchError, BatchStore

SETTABLE_STATUSES = (SlideStatus.ACCEPTED, SlideStatus.NEEDS_LOOK)


class NoSuchSlide(LookupError):
    """There is no such tray or slide position in this batch."""


def _find(manifest: BatchManifest, number: int, position: int | None = None):
    tray = next((t for t in manifest.trays if t.number == number), None)
    if tray is None:
        raise NoSuchSlide(f"no tray {number} in this batch")
    if position is None:
        return tray, None
    slide = next((s for s in tray.slides if s.position == position), None)
    if slide is None:
        raise NoSuchSlide(f"tray {number} has no slide {position}")
    return tray, slide


def _replace_slide(
    manifest: BatchManifest, tray: TrayRecord, slide: SlideRecord, **changes
) -> BatchManifest:
    new_slide = dataclasses.replace(slide, **changes)
    slides = tuple(new_slide if s.position == slide.position else s for s in tray.slides)
    trays = tuple(
        dataclasses.replace(t, slides=slides) if t.number == tray.number else t
        for t in manifest.trays
    )
    return dataclasses.replace(manifest, trays=trays)


EDITABLE_STAGES = (TrayStage.REVIEW, TrayStage.ENHANCING)


def _require_editable(tray: TrayRecord) -> None:
    if tray.raw_removed:
        raise RawRemoved(f"the raw scans of tray {tray.number} were removed")
    if tray.stage not in EDITABLE_STAGES:
        raise BatchError(
            f"tray {tray.number} is {tray.stage}; slides can only be edited while it is in review"
        )


def set_slide_status(store: BatchStore, number: int, position: int, status: SlideStatus) -> None:
    if status not in SETTABLE_STATUSES:
        raise BatchError(f"a slide can only be set to accepted or needs_look, not {status}")

    def change(manifest: BatchManifest) -> BatchManifest:
        tray, slide = _find(manifest, number, position)
        if slide.status is SlideStatus.BLANK:
            raise BatchError(f"slide {position} of tray {number} is blank")
        return _replace_slide(manifest, tray, slide, status=status, owner_decided=True)

    store.update(change)


def apply_slide_settings(
    store: BatchStore, number: int, position: int, settings: SlideSettings
) -> None:
    def change(manifest: BatchManifest) -> BatchManifest:
        tray, slide = _find(manifest, number, position)
        _require_editable(tray)
        if slide.status is SlideStatus.BLANK:
            raise BatchError(f"slide {position} of tray {number} is blank")
        return _replace_slide(manifest, tray, slide, settings=settings, owner_decided=True)

    store.update(change)


def accept_all(store: BatchStore) -> None:
    def accepted(slide: SlideRecord) -> SlideRecord:
        if slide.status is SlideStatus.BLANK:
            return slide
        return dataclasses.replace(slide, status=SlideStatus.ACCEPTED, owner_decided=True)

    def change(manifest: BatchManifest) -> BatchManifest:
        trays = tuple(
            dataclasses.replace(t, slides=tuple(accepted(s) for s in t.slides))
            if t.stage is TrayStage.REVIEW
            else t
            for t in manifest.trays
        )
        return dataclasses.replace(manifest, trays=trays)

    store.update(change)


def set_tray_mode(store: BatchStore, number: int, mode: Mode) -> None:
    def change(manifest: BatchManifest) -> BatchManifest:
        tray, _ = _find(manifest, number)
        _require_editable(tray)
        trays = tuple(
            dataclasses.replace(t, mode=mode) if t.number == number else t for t in manifest.trays
        )
        return dataclasses.replace(manifest, trays=trays)

    store.update(change)
