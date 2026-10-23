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
        changed = settings != slide.settings
        return _replace_slide(
            manifest,
            tray,
            slide,
            settings=settings,
            owner_decided=True,
            # new settings make the exported file and the preview obsolete
            exported=slide.exported and not changed,
            preview_stale=slide.preview_stale or changed,
        )

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
        changed = tray.mode != mode

        def renewed(t: TrayRecord) -> TrayRecord:
            if t.number != number:
                return t
            # files exported in the old mode are obsolete
            slides = (
                tuple(dataclasses.replace(s, exported=False) for s in t.slides)
                if changed
                else t.slides
            )
            return dataclasses.replace(t, mode=mode, slides=slides)

        return dataclasses.replace(manifest, trays=tuple(renewed(t) for t in manifest.trays))

    store.update(change)


BUSY_STAGES = (TrayStage.SCANNING, TrayStage.ENHANCING)


def _reoriented(tray: TrayRecord) -> TrayRecord:
    """Every non-blank slide needs a new preview and a new export; owner choices are kept."""
    slides = tuple(
        s
        if s.status is SlideStatus.BLANK
        else dataclasses.replace(s, preview_stale=True, exported=False)
        for s in tray.slides
    )
    # an exported tray now has slides to export again, so it is back in review
    stage = TrayStage.REVIEW if tray.stage is TrayStage.EXPORTED else tray.stage
    return dataclasses.replace(tray, slides=slides, stage=stage)


def set_batch_orientation(store: BatchStore, rotation: int, mirror: bool) -> tuple[int, ...]:
    """Change how every slide of the batch is turned and flipped (see ``options_for``).

    Every non-blank slide of a tray whose raw scans are kept gets ``preview_stale`` (it must be
    enhanced again) and loses its export mark; an exported tray goes back to review. Trays whose
    raw scans were removed cannot be re-rendered: they are left untouched and their numbers are
    returned. Refused while any tray is scanning or enhancing. Setting the current value again
    changes nothing.
    """
    skipped: tuple[int, ...] = ()

    def change(manifest: BatchManifest) -> BatchManifest:
        nonlocal skipped
        busy = [t.number for t in manifest.trays if t.stage in BUSY_STAGES]
        if busy:
            raise BatchError(
                f"tray {busy[0]} is still being scanned or enhanced; change the batch "
                "orientation once it has finished"
            )
        defaults = dataclasses.replace(manifest.defaults, rotation=rotation, mirror=mirror)
        skipped = tuple(t.number for t in manifest.trays if t.raw_removed)
        if defaults == manifest.defaults:
            return manifest
        trays = tuple(t if t.raw_removed else _reoriented(t) for t in manifest.trays)
        return dataclasses.replace(manifest, defaults=defaults, trays=trays)

    store.update(change)
    return skipped
