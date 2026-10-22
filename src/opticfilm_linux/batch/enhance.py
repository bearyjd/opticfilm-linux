"""Turn a scanned tray into previews, flags and slide records, and refresh single slides.

Owner choices are never overwritten: a slide record with ``owner_decided`` keeps its status and
settings, and only its previews and flags are refreshed.
"""

import dataclasses
from collections.abc import Callable

from opticfilm_linux.batch.flags import compute_flags
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    Mode,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.previews import make_preview, write_preview
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.model import RawSweep, Slide
from opticfilm_linux.pipeline.run import ProcessOptions, enhance_slide
from opticfilm_linux.pipeline.split import SlideSplitError, is_blank, split_slides
from opticfilm_linux.sweepstore import SweepFormatError, load_sweep

BEFORE_GAMMA = 2.2  # raw scans are linear; this makes the "before" preview look natural


class RawRemoved(BatchError):
    """The tray's raw scans were cleaned up, so it can no longer be re-enhanced."""


def options_for(defaults: BatchDefaults, mode: Mode, settings: SlideSettings) -> ProcessOptions:
    def pick(override: float | None, default: float) -> float:
        return default if override is None else override

    return ProcessOptions(
        mode=mode,
        remove_dust=defaults.remove_dust,
        color_strength=pick(settings.color_strength, defaults.color_strength),
        sharpen_amount=pick(settings.sharpen_amount, defaults.sharpen_amount),
        grain_strength=pick(settings.grain_strength, defaults.grain_strength),
        skip_blank=defaults.skip_blank,
        brightness_ev=settings.brightness_ev,
        rotation=settings.rotation,
    )


def _tray(manifest: BatchManifest, number: int) -> TrayRecord:
    for tray in manifest.trays:
        if tray.number == number:
            return tray
    raise BatchError(f"no tray {number} in this batch")


def _put_slide(store: BatchStore, number: int, record: SlideRecord) -> None:
    def change(manifest: BatchManifest) -> BatchManifest:
        tray = _tray(manifest, number)
        others = tuple(s for s in tray.slides if s.position != record.position)
        slides = tuple(sorted(others + (record,), key=lambda s: s.position))
        trays = tuple(
            dataclasses.replace(t, slides=slides) if t.number == number else t
            for t in manifest.trays
        )
        return dataclasses.replace(manifest, trays=trays)

    store.update(change)


def _process_slide(
    store: BatchStore,
    number: int,
    slide: Slide,
    sweep: RawSweep,
    tray: TrayRecord,
    current: SlideRecord | None,
) -> SlideRecord:
    defaults = store.snapshot().defaults
    position = slide.index + 1
    record = current or SlideRecord(position=position)
    if defaults.skip_blank and is_blank(slide):
        return dataclasses.replace(record, status=SlideStatus.BLANK, flags=())
    enhanced = enhance_slide(slide, options_for(defaults, tray.mode, record.settings), sweep.dpi)
    write_preview(
        make_preview(slide.rgb, gamma=BEFORE_GAMMA), store.preview_path(number, position, "before")
    )
    write_preview(make_preview(enhanced), store.preview_path(number, position, "after"))
    flags = compute_flags(enhanced, slide.ir, sweep.dpi)
    if record.owner_decided:
        status = record.status
    else:
        status = SlideStatus.NEEDS_LOOK if flags else SlideStatus.ACCEPTED
    return dataclasses.replace(record, status=status, flags=flags)


def _existing(tray: TrayRecord, position: int) -> SlideRecord | None:
    return next((s for s in tray.slides if s.position == position), None)


def enhance_tray(
    store: BatchStore, number: int, on_progress: Callable[[int, int], None] | None = None
) -> None:
    tray = _tray(store.snapshot(), number)
    if tray.stage is TrayStage.SCANNING:
        raise BatchError(f"tray {number} is still scanning")
    if tray.raw_removed:
        raise RawRemoved(f"the raw scans of tray {number} were removed")
    store.update_tray(number, stage=TrayStage.ENHANCING, error=None)
    try:
        sweep = load_sweep(store.sweep_dir(number, tray.sweep_version))
        slides = split_slides(sweep)
    except (SweepFormatError, SlideSplitError) as exc:
        store.update_tray(number, stage=TrayStage.FAILED, error=str(exc))
        return
    for slide in slides:
        record = _process_slide(store, number, slide, sweep, tray, _existing(tray, slide.index + 1))
        _put_slide(store, number, record)
        if on_progress is not None:
            on_progress(slide.index + 1, len(slides))
    store.update_tray(number, stage=TrayStage.REVIEW)


def reenhance_slide(store: BatchStore, number: int, position: int) -> None:
    tray = _tray(store.snapshot(), number)
    if tray.raw_removed:
        raise RawRemoved(f"the raw scans of tray {number} were removed")
    current = _existing(tray, position)
    if current is None:
        raise BatchError(f"tray {number} has no slide {position}")
    sweep = load_sweep(store.sweep_dir(number, tray.sweep_version))
    slides = split_slides(sweep)
    _put_slide(
        store, number, _process_slide(store, number, slides[position - 1], sweep, tray, current)
    )
