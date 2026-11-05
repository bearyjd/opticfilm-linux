"""Turn and flip a finished preview in place of re-enhancing a slide whose orientation changed.

A turn or a flip is the last step of the pipeline, so when it is the only change, the current
"after" preview turned and flipped is what a full re-enhance would produce. The "before" preview
is the raw slide, never oriented, so it is left alone. Flags are kept: brightness, clipping and
infrared dust do not depend on orientation. Anything else changed, a preview already stale or
missing, or a blank slide: this does nothing and the caller keeps the background re-enhance.
"""

import dataclasses
import os
from pathlib import Path

import numpy as np
from PIL import Image

from opticfilm_linux.batch.model import (
    BatchManifest,
    SlideKey,
    SlideRecord,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.preview_transform import (
    Orientation,
    effective_orientation,
    preview_transform,
    transform_preview,
)
from opticfilm_linux.batch.previews import write_preview
from opticfilm_linux.batch.store import BatchStore

SHOWN_STAGES = (TrayStage.REVIEW, TrayStage.EXPORTED)


def _find(manifest: BatchManifest, key: SlideKey) -> tuple[TrayRecord, SlideRecord] | None:
    tray = next((t for t in manifest.trays if t.number == key[0]), None)
    slide = None if tray is None else next((s for s in tray.slides if s.position == key[1]), None)
    return None if slide is None else (tray, slide)


def _only_turned(old, new) -> bool:
    return dataclasses.replace(old, rotation=new.rotation, mirror=new.mirror) == new


def _same_tray(old: TrayRecord, new: TrayRecord) -> bool:
    return (
        new.stage in SHOWN_STAGES
        and not new.raw_removed
        and (old.sweep_version, old.mode) == (new.sweep_version, new.mode)
    )


def _eligible(old: BatchManifest, new: BatchManifest, key: SlideKey) -> bool:
    found_old, found_new = _find(old, key), _find(new, key)
    if found_old is None or found_new is None:
        return False
    (old_tray, was), (new_tray, now) = found_old, found_new
    return (
        _same_tray(old_tray, new_tray)
        and not was.preview_stale
        and now.preview_stale
        and SlideStatus.BLANK not in (was.status, now.status)
        and _only_turned(was.settings, now.settings)
        and _only_turned(old.defaults, new.defaults)
    )


def _orientation(manifest: BatchManifest, key: SlideKey) -> Orientation:
    tray, slide = _find(manifest, key)
    return effective_orientation(manifest.defaults, tray.mode, slide.settings, slide.position)


def _staged(path: Path) -> Path:
    # not ".pending.jpg": a re-enhance of the same slide may be writing that one right now
    return path.with_name(path.stem + ".turned.jpg")


def _still_wanted(manifest: BatchManifest, target: BatchManifest, key: SlideKey) -> bool:
    found, wanted = _find(manifest, key), _find(target, key)
    if found is None or not _same_tray(wanted[0], found[0]):
        return False
    slide = found[1]
    return (
        manifest.defaults == target.defaults
        and slide.settings == wanted[1].settings
        and slide.preview_stale
        and slide.status is not SlideStatus.BLANK
    )


def _publish(store: BatchStore, target: BatchManifest, key: SlideKey, staged: Path) -> bool:
    """Rename the turned preview into place and mark it current, only if nothing moved on."""
    published = False

    def change(manifest: BatchManifest) -> BatchManifest:
        nonlocal published
        if not _still_wanted(manifest, target, key):
            return manifest  # a newer edit or a finished re-enhance owns the preview now
        os.replace(staged, store.preview_path(*key, "after"))
        published = True
        trays = tuple(
            dataclasses.replace(
                t,
                slides=tuple(
                    dataclasses.replace(s, preview_stale=False) if s.position == key[1] else s
                    for s in t.slides
                ),
            )
            if t.number == key[0]
            else t
            for t in manifest.trays
        )
        return dataclasses.replace(manifest, trays=trays)

    store.update(change)
    return published


def reorient_preview(
    store: BatchStore, old: BatchManifest, new: BatchManifest, key: SlideKey
) -> bool:
    """Turn the "after" preview of ``key`` from its look in ``old`` to its look in ``new``.

    Returns whether the turned preview was published. On any failure the previous preview is
    left as it was and nothing half-written remains; the exception goes to the caller.
    """
    after = store.preview_path(*key, "after")
    if not _eligible(old, new, key) or not after.is_file():
        return False
    transform = preview_transform(_orientation(old, key), _orientation(new, key))
    staged = _staged(after)
    try:
        with Image.open(after) as image:
            pixels = np.asarray(image.convert("RGB"))
        write_preview(transform_preview(pixels, transform), staged)
        return _publish(store, new, key, staged)
    finally:
        staged.unlink(missing_ok=True)
