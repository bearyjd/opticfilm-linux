"""Turn a scanned tray into previews, flags and slide records, and refresh single slides.

Owner choices are never overwritten: a slide record with ``owner_decided`` keeps its status and
settings, and only its previews and flags are refreshed.
"""

import dataclasses
import os
from collections.abc import Callable, Iterable
from pathlib import Path

from opticfilm_linux.batch.flags import compute_flags
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    Flag,
    Mode,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.previews import make_preview, write_preview
from opticfilm_linux.batch.slots import compose, slot_orientation
from opticfilm_linux.batch.store import BatchError, BatchStore, is_interrupted
from opticfilm_linux.model import RawSweep, Slide
from opticfilm_linux.pipeline.run import ProcessOptions, enhance_slide
from opticfilm_linux.pipeline.split import SlideSplitError, is_blank, split_positions
from opticfilm_linux.provenance import raw_region_sha256
from opticfilm_linux.sweepstore import SweepFormatError, load_sweep

BEFORE_GAMMA = 2.2  # raw scans are linear; this makes the "before" preview look natural


class RawRemoved(BatchError):
    """The tray's raw scans were cleaned up, so it can no longer be re-enhanced."""


def options_for(
    defaults: BatchDefaults, mode: Mode, settings: SlideSettings, position: int
) -> ProcessOptions:
    """Combine the batch defaults with the slot's default and one slide's own settings.

    Orientations are applied in turn, each to the image as shown: the batch's, then the default
    for the slide's holder slot, then the slide's own, as if each were done by hand on the image
    the previous one produced (see ``slots.compose``).
    """

    def pick(override: float | None, default: float) -> float:
        return default if override is None else override

    batch = (defaults.rotation, defaults.mirror)
    slot = compose(batch, slot_orientation(defaults, position))
    rotation, mirror = compose(slot, (settings.rotation, settings.mirror))
    return ProcessOptions(
        mode=mode,
        remove_dust=defaults.remove_dust,
        color_strength=pick(settings.color_strength, defaults.color_strength),
        sharpen_amount=pick(settings.sharpen_amount, defaults.sharpen_amount),
        grain_strength=pick(settings.grain_strength, defaults.grain_strength),
        skip_blank=defaults.skip_blank,
        brightness_ev=settings.brightness_ev,
        rotation=rotation,
        mirror=mirror,
    )


def _tray(manifest: BatchManifest, number: int) -> TrayRecord:
    for tray in manifest.trays:
        if tray.number == number:
            return tray
    raise BatchError(f"no tray {number} in this batch")


def _merge_slide(
    store: BatchStore,
    number: int,
    tray: TrayRecord,
    snapshot: SlideRecord,
    flags: tuple[Flag, ...],
    blank: bool,
    defaults: BatchDefaults,
    publish: Callable[[], None] = lambda: None,
    fingerprint: str = "",
) -> None:
    """Keep owner choices and publish current previews atomically under the store lock."""

    def change(manifest: BatchManifest) -> BatchManifest:
        now = _tray(manifest, number)
        if now.raw_removed:
            return manifest
        if now.sweep_version != tray.sweep_version or now.mode != tray.mode:
            return manifest
        if manifest.defaults != defaults:
            return manifest
        current = next((s for s in now.slides if s.position == snapshot.position), None)
        if current is not None and current.settings != snapshot.settings:
            return manifest
        base = current or snapshot
        if blank and _protect_content(base) and base.status is not SlideStatus.BLANK:
            return manifest
        record = _classified_record(base, flags, blank, fingerprint)
        others = tuple(s for s in now.slides if s.position != snapshot.position)
        slides = tuple(sorted(others + (record,), key=lambda s: s.position))
        trays = tuple(
            dataclasses.replace(t, slides=slides) if t.number == number else t
            for t in manifest.trays
        )
        publish()
        return dataclasses.replace(manifest, trays=trays)

    store.update(change)


def _protect_content(slide: SlideRecord) -> bool:
    return slide.auto_accepted or slide.exported or slide.owner_decided


def _classified_record(
    base: SlideRecord, flags: tuple[Flag, ...], blank: bool, fingerprint: str
) -> SlideRecord:
    auto = SlideStatus.NEEDS_LOOK if flags else SlideStatus.ACCEPTED
    return dataclasses.replace(
        base,
        status=base.status if base.owner_decided else SlideStatus.BLANK if blank else auto,
        flags=() if blank else flags,
        preview_stale=False,
        blank_fingerprint=fingerprint if blank else "",
        empty_confirmed=None,
        auto_accepted=base.auto_accepted and not blank,
    )


def _pending(path: Path) -> Path:
    return path.with_name(path.stem + ".pending.jpg")


def _process_slide(
    store: BatchStore,
    number: int,
    slide: Slide,
    sweep: RawSweep,
    tray: TrayRecord,
    snapshot: SlideRecord,
) -> None:
    defaults = store.snapshot().defaults
    position = snapshot.position  # the holder opening; the sweep index differs once one is skipped
    blank = (
        snapshot.status is SlideStatus.BLANK
        and (snapshot.owner_decided or snapshot.preview_stale or not snapshot.blank_fingerprint)
    ) or (defaults.skip_blank and not _protect_content(snapshot) and is_blank(slide))
    enhanced = (
        slide.rgb
        if blank
        else enhance_slide(
            slide, options_for(defaults, tray.mode, snapshot.settings, snapshot.position), sweep.dpi
        )
    )
    before = store.preview_path(number, position, "before")
    after = store.preview_path(number, position, "after")

    def publish() -> None:
        os.replace(_pending(before), before)
        os.replace(_pending(after), after)

    try:
        write_preview(make_preview(slide.rgb, gamma=BEFORE_GAMMA), _pending(before))
        write_preview(make_preview(enhanced, gamma=BEFORE_GAMMA if blank else 1.0), _pending(after))
        flags = () if blank else compute_flags(enhanced, slide.ir, sweep.dpi, visible=slide.rgb)
        _merge_slide(
            store,
            number,
            tray,
            snapshot,
            flags,
            blank,
            defaults,
            publish,
            raw_region_sha256(slide) if blank else "",
        )
    finally:  # a result that was dropped leaves nothing behind
        _pending(before).unlink(missing_ok=True)
        _pending(after).unlink(missing_ok=True)


def _existing(tray: TrayRecord, position: int) -> SlideRecord | None:
    return next((s for s in tray.slides if s.position == position), None)


def enhance_tray(
    store: BatchStore, number: int, on_progress: Callable[[int, int], None] | None = None
) -> None:
    tray = _tray(store.snapshot(), number)
    if tray.raw_removed:
        raise RawRemoved(f"the raw scans of tray {number} were removed")
    version = tray.sweep_version
    started = store.update_tray_if(
        number,
        lambda t: (
            t.stage is not TrayStage.SCANNING
            and not t.raw_removed
            and not is_interrupted(t)
            and t.sweep_version == version
        ),
        stage=TrayStage.ENHANCING,
        error=None,
    )
    if not started:
        now = _tray(store.snapshot(), number)
        if now.raw_removed:
            raise RawRemoved(f"the raw scans of tray {number} were removed")
        if now.stage is TrayStage.SCANNING:
            raise BatchError(f"tray {number} is still scanning")
        if is_interrupted(now):  # a partial payload is never treated as a finished tray
            raise BatchError(f"tray {number} was interrupted; rescan it")
        return  # the tray was re-scanned meanwhile; a newer job will handle it

    def current(t: TrayRecord) -> bool:
        return not t.raw_removed and t.sweep_version == version and t.stage is TrayStage.ENHANCING

    try:
        sweep = load_sweep(store.sweep_dir(number, version))
        slides = split_positions(sweep)
    except (SweepFormatError, SlideSplitError) as exc:
        store.update_tray_if(number, current, stage=TrayStage.FAILED, error=str(exc))
        return
    for position, slide in slides.items():
        snapshot = _existing(tray, position) or SlideRecord(position=position)
        _process_slide(store, number, slide, sweep, tray, snapshot)
        if on_progress is not None:
            on_progress(position, len(slides))
    store.update_tray_if(number, current, stage=TrayStage.REVIEW)


def require_scanned(
    slides: dict[int, Slide], number: int, positions: Iterable[int]
) -> dict[int, Slide]:
    """``slides``, unless a slide record's holder opening is not in the sweep (no raw data)."""
    if missing := sorted(set(positions) - set(slides)):
        names = ", ".join(map(str, missing))
        raise BatchError(f"tray {number} has no scanned opening {names}")
    return slides


def reenhance_slide(store: BatchStore, number: int, position: int) -> None:
    tray = _tray(store.snapshot(), number)
    if tray.raw_removed:
        raise RawRemoved(f"the raw scans of tray {number} were removed")
    snapshot = _existing(tray, position)
    if snapshot is None:
        raise BatchError(f"tray {number} has no slide {position}")
    sweep = load_sweep(store.sweep_dir(number, tray.sweep_version))
    slides = require_scanned(split_positions(sweep), number, (position,))
    _process_slide(store, number, slides[position], sweep, tray, snapshot)
