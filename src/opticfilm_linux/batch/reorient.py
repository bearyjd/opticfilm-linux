"""Turn and flip a finished preview in place of re-enhancing a slide whose orientation changed.

A turn or a flip is the last step of the pipeline, so when it is the only change, the last full
render turned and flipped is what a full re-enhance would produce. The turn is made from the
render's lossless master (tagged with the orientation it was rendered at), never from the JPEG,
so any number of turns stays one JPEG save away from the render. A slide without a readable
master (a batch rendered before masters existed, or a damaged one) is turned once from its JPEG
preview: that costs ONE extra JPEG generation, accepted, and the turned picture becomes its
master, so every later turn is lossless from there. The "before" preview is the raw slide,
never oriented, so it is left alone. Flags are kept: brightness, clipping and infrared dust do
not depend on orientation. Anything else changed, a preview already stale or missing, or a
blank slide: this does nothing and the caller keeps the background re-enhance.
"""

import dataclasses
import os
import threading
from collections.abc import Mapping
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
from opticfilm_linux.batch.previews import read_master, write_master, write_preview
from opticfilm_linux.batch.store import BatchStore

SHOWN_STAGES = (TrayStage.REVIEW, TrayStage.EXPORTED)


@dataclasses.dataclass(frozen=True)
class StagedTurn:
    """A turned preview written beside the shown one, and a new master when one is made."""

    preview: Path
    master: Path | None = None

    def discard(self) -> None:
        self.preview.unlink(missing_ok=True)
        if self.master is not None:
            self.master.unlink(missing_ok=True)


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
    # not ".pending.jpg": a re-enhance of the same slide may be writing that one right now; one
    # name per thread, so a quick turn and a batch turn of the same slide never share a file
    return path.with_name(f"{path.stem}.turned-{threading.get_ident()}.jpg")


def _staged_master(store: BatchStore, key: SlideKey) -> Path:
    master = store.master_path(*key)
    return master.with_name(f"{master.stem}.turned-{threading.get_ident()}.png")


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


def publish_turns(
    store: BatchStore, target: BatchManifest, staged: Mapping[SlideKey, StagedTurn]
) -> frozenset[SlideKey]:
    """Rename turned previews into place and mark them current in ONE manifest write.

    Only slides where nothing moved on since ``target`` are published; returns those keys. The
    staged files of the others are left for the caller to remove.
    """
    published: set[SlideKey] = set()

    def change(manifest: BatchManifest) -> BatchManifest:
        published.clear()
        for key, turn in staged.items():
            if _still_wanted(manifest, target, key):  # else a newer edit or render owns it
                if turn.master is not None:
                    os.replace(turn.master, store.master_path(*key))
                os.replace(turn.preview, store.preview_path(*key, "after"))
                published.add(key)
        if not published:
            return manifest
        trays = tuple(
            dataclasses.replace(
                t,
                slides=tuple(
                    dataclasses.replace(s, preview_stale=False)
                    if (t.number, s.position) in published
                    else s
                    for s in t.slides
                ),
            )
            for t in manifest.trays
        )
        return dataclasses.replace(manifest, trays=trays)

    store.update(change)
    return frozenset(published)


def stage_turn(
    store: BatchStore, old: BatchManifest, new: BatchManifest, key: SlideKey
) -> StagedTurn | None:
    """Write the "after" preview of ``key`` as it looks in ``new`` beside the current one.

    Returns what was staged, or None when the slide cannot be turned (see the module text). On
    a failure nothing half-written remains and the exception goes to the caller.
    """
    after = store.preview_path(*key, "after")
    if not _eligible(old, new, key) or not after.is_file():
        return None
    wanted = _orientation(new, key)
    master = read_master(store.master_path(*key))
    turn = StagedTurn(_staged(after), None if master is not None else _staged_master(store, key))
    try:
        if master is not None:
            pixels, rendered = master
            write_preview(
                transform_preview(pixels, preview_transform(rendered, wanted)), turn.preview
            )
        else:  # no usable master: one generation from the JPEG, then lossless from the new one
            with Image.open(after) as image:
                shown = np.asarray(image.convert("RGB"))
            turned = transform_preview(shown, preview_transform(_orientation(old, key), wanted))
            write_preview(turned, turn.preview)
            write_master(turned, wanted, turn.master)
    except BaseException:
        turn.discard()
        raise
    return turn


def reorient_preview(
    store: BatchStore, old: BatchManifest, new: BatchManifest, key: SlideKey
) -> bool:
    """Turn the "after" preview of ``key`` from its look in ``old`` to its look in ``new``.

    Returns whether the turned preview was published. On any failure the previous preview is
    left as it was and nothing half-written remains; the exception goes to the caller.
    """
    staged = stage_turn(store, old, new, key)
    if staged is None:
        return False
    try:
        return key in publish_turns(store, new, {key: staged})
    finally:
        staged.discard()
