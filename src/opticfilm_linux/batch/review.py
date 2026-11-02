"""What the owner can do in review. Each operation is one atomic manifest update.

Every explicit choice sets ``owner_decided``, which re-enhancement respects: previews and flags
are refreshed but the owner's status and settings are never overwritten.
"""

import dataclasses
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType

from opticfilm_linux.batch.empty import empty_frame_fingerprints
from opticfilm_linux.batch.enhance import RawRemoved
from opticfilm_linux.batch.model import (
    BatchManifest,
    EmptyConfirmation,
    Mode,
    SlideKey,
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
    _require_raw(tray)
    new_slide = dataclasses.replace(slide, empty_confirmed=None, **changes)
    slides = tuple(new_slide if s.position == slide.position else s for s in tray.slides)
    trays = tuple(
        _reviewed_tray(t, slides) if t.number == tray.number else t for t in manifest.trays
    )
    return dataclasses.replace(manifest, trays=trays)


def _reviewed_tray(tray: TrayRecord, slides, *, render_changed: bool = False) -> TrayRecord:
    changed = render_changed or any(
        a.settings != b.settings or a.preview_stale != b.preview_stale or a.status != b.status
        for a, b in zip(tray.slides, slides, strict=True)
    )
    rearm = changed and tray.auto in ("held", "exported", "verified")
    return dataclasses.replace(
        tray,
        slides=slides,
        stage=TrayStage.REVIEW if changed and tray.stage is TrayStage.EXPORTED else tray.stage,
        auto="pending" if rearm else tray.auto,
        auto_error="" if rearm else tray.auto_error,
        auto_failed=() if rearm else tray.auto_failed,
    )


EDITABLE_STAGES = (TrayStage.REVIEW, TrayStage.ENHANCING, TrayStage.EXPORTED)


def _require_raw(tray: TrayRecord) -> None:
    if tray.raw_removed:
        raise RawRemoved(f"the raw scans of tray {tray.number} were removed")


def _require_editable(tray: TrayRecord) -> None:
    _require_raw(tray)
    if tray.stage not in EDITABLE_STAGES:
        raise BatchError(
            f"tray {tray.number} is {tray.stage}; slides can only be edited while it is in review"
        )


def set_slide_status(store: BatchStore, number: int, position: int, status: SlideStatus) -> None:
    if status not in SETTABLE_STATUSES:
        raise BatchError(f"a slide can only be set to accepted or needs_look, not {status}")

    def change(manifest: BatchManifest) -> BatchManifest:
        tray, slide = _find(manifest, number, position)
        return _replace_slide(
            manifest,
            tray,
            slide,
            status=status,
            owner_decided=True,
            auto_accepted=False,
            preview_stale=slide.preview_stale or slide.status is SlideStatus.BLANK,
        )

    store.update(change)


def confirm_empty_frames(
    store: BatchStore,
    number: int,
    expected_positions: Iterable[int],
    *,
    expected_fingerprints: Mapping[int, str],
    expected_sweep_version: int,
) -> None:
    """Record the owner's explicit inspection of this tray's empty frames."""

    positions = tuple(expected_positions)
    fingerprints = dict(expected_fingerprints)

    def change(manifest: BatchManifest) -> BatchManifest:
        tray, _ = _find(manifest, number)
        _require_raw(tray)
        if tray.stage not in (TrayStage.REVIEW, TrayStage.EXPORTED):
            raise BatchError(f"tray {number} is not ready for empty-frame confirmation")
        current = empty_frame_fingerprints(tray)
        if (
            not current
            or expected_sweep_version != tray.sweep_version
            or len(set(positions)) != len(positions)
            or set(positions) != set(current)
            or fingerprints != current
            or not all(current.values())
        ):
            raise BatchError("Empty frames changed; inspect the current frames before confirming")
        slides = tuple(
            dataclasses.replace(
                s,
                empty_confirmed=EmptyConfirmation(tray.sweep_version, current[s.position]),
                auto_accepted=False,
            )
            if s.status is SlideStatus.BLANK
            else s
            for s in tray.slides
        )
        return dataclasses.replace(
            manifest,
            trays=tuple(
                dataclasses.replace(t, slides=slides) if t.number == number else t
                for t in manifest.trays
            ),
        )

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
            auto_accepted=False,
            # new settings make the exported file and the preview obsolete
            exported=slide.exported and not changed,
            preview_stale=slide.preview_stale or changed,
        )

    store.update(change)


def accept_all(store: BatchStore) -> None:
    def accepted(slide: SlideRecord) -> SlideRecord:
        if slide.status is SlideStatus.BLANK or slide.owner_decided:
            return slide
        return dataclasses.replace(
            slide,
            status=SlideStatus.ACCEPTED,
            owner_decided=True,
            auto_accepted=False,
            empty_confirmed=None,
        )

    def change(manifest: BatchManifest) -> BatchManifest:
        for tray in manifest.trays:
            if tray.stage is TrayStage.REVIEW:
                _require_raw(tray)
        trays = tuple(
            dataclasses.replace(t, slides=tuple(accepted(s) for s in t.slides))
            if t.stage is TrayStage.REVIEW
            else t
            for t in manifest.trays
        )
        return dataclasses.replace(manifest, trays=trays)

    store.update(change)


def accept_tray(store: BatchStore, number: int, *, automatic: bool = False) -> None:
    """Accept a tray, preserving owner choices and automatic review classifications."""

    def change(manifest: BatchManifest) -> BatchManifest:
        tray, _ = _find(manifest, number)
        _require_raw(tray)
        if tray.stage not in (TrayStage.REVIEW, TrayStage.EXPORTED):
            raise BatchError(f"tray {number} is not ready for automatic acceptance")
        slides = tuple(
            s
            if s.owner_decided
            or (automatic and s.status in (SlideStatus.BLANK, SlideStatus.NEEDS_LOOK))
            else dataclasses.replace(
                s,
                status=SlideStatus.ACCEPTED,
                owner_decided=not automatic,
                auto_accepted=automatic,
                empty_confirmed=None,
            )
            for s in tray.slides
        )
        return dataclasses.replace(
            manifest,
            trays=tuple(
                _reviewed_tray(t, slides) if t.number == number else t for t in manifest.trays
            ),
        )

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
                tuple(
                    dataclasses.replace(s, exported=False, empty_confirmed=None) for s in t.slides
                )
                if changed
                else t.slides
            )
            return dataclasses.replace(_reviewed_tray(t, slides, render_changed=changed), mode=mode)

        return dataclasses.replace(manifest, trays=tuple(renewed(t) for t in manifest.trays))

    store.update(change)


BUSY_STAGES = (TrayStage.SCANNING, TrayStage.ENHANCING)


def _reoriented(tray: TrayRecord) -> TrayRecord:
    """Every non-blank slide needs a new preview and a new export; owner choices are kept."""
    slides = tuple(
        dataclasses.replace(s, empty_confirmed=None)
        if s.status is SlideStatus.BLANK
        else dataclasses.replace(s, preview_stale=True, exported=False, empty_confirmed=None)
        for s in tray.slides
    )
    # an exported tray now has slides to export again, so it is back in review
    stage = TrayStage.REVIEW if tray.stage is TrayStage.EXPORTED else tray.stage
    return dataclasses.replace(_reviewed_tray(tray, slides), stage=stage)


def set_batch_orientation(store: BatchStore, rotation: int, mirror: bool) -> tuple[int, ...]:
    """Change how every slide of the batch is turned and flipped (see ``options_for``).

    Every non-blank slide of a tray whose raw scans are kept gets ``preview_stale`` (it must be
    enhanced again) and loses its export mark; an exported tray goes back to review. Trays whose
    raw scans were removed cannot be re-rendered: they are left untouched and their numbers are
    returned (a tray whose raw cleanup has started is marked removed first, so it is skipped
    too). Setting the current value again changes nothing, even while a tray is busy; any
    other change is refused while a tray is scanning or enhancing.
    """
    skipped: tuple[int, ...] = ()

    def change(manifest: BatchManifest) -> BatchManifest:
        nonlocal skipped
        defaults = dataclasses.replace(manifest.defaults, rotation=rotation, mirror=mirror)
        if defaults == manifest.defaults:
            return manifest
        automatic = manifest.defaults.auto_export or any(
            t.auto not in ("", "off") for t in manifest.trays
        )
        if manifest.first_tray_released and automatic:
            raise BatchError("Batch orientation cannot change after releasing the first tray")
        busy = [t.number for t in manifest.trays if t.stage in BUSY_STAGES]
        if busy:
            raise BatchError(
                f"tray {busy[0]} is still being scanned or enhanced; change the batch "
                "orientation once it has finished"
            )
        skipped = tuple(t.number for t in manifest.trays if t.raw_removed)
        trays = tuple(t if t.raw_removed else _reoriented(t) for t in manifest.trays)
        return dataclasses.replace(manifest, defaults=defaults, trays=trays)

    store.update(change)
    return skipped


BeforeEdit = tuple[SlideSettings, SlideStatus, bool]


@dataclasses.dataclass(frozen=True)
class BulkEditResult:
    """Skipped keys and immutable inverse snapshots of successfully edited slides."""

    skipped: Mapping[SlideKey, str]
    before: Mapping[SlideKey, BeforeEdit]

    def __post_init__(self) -> None:
        object.__setattr__(self, "skipped", MappingProxyType(dict(self.skipped)))
        object.__setattr__(self, "before", MappingProxyType(dict(self.before)))


def _bulk_skip(tray: TrayRecord, slide: SlideRecord) -> str | None:
    if tray.raw_removed:
        return "raw_removed"
    if tray.stage not in (TrayStage.REVIEW, TrayStage.EXPORTED):
        return "not_editable"
    if slide.status is SlideStatus.BLANK:
        return "blank"
    return None


def _bulk_slide(slide: SlideRecord, values: BeforeEdit) -> SlideRecord:
    settings, status, owner_decided = values
    changed = settings != slide.settings
    entering_blank = status is SlideStatus.BLANK and slide.status is not SlideStatus.BLANK
    return dataclasses.replace(
        slide,
        settings=settings,
        status=status,
        owner_decided=owner_decided,
        auto_accepted=False,
        empty_confirmed=None,
        exported=slide.exported and not changed,
        preview_stale=slide.preview_stale or changed or entering_blank,
        blank_fingerprint="" if entering_blank else slide.blank_fingerprint,
    )


def _edit_many(
    store: BatchStore,
    keys: Iterable[SlideKey],
    edit: Callable[[SlideKey, SlideRecord], BeforeEdit],
) -> BulkEditResult:
    selected = set(keys)
    skipped = dict.fromkeys(selected, "missing")
    before: dict[SlideKey, BeforeEdit] = {}

    def change(manifest: BatchManifest) -> BatchManifest:
        trays = []
        for tray in manifest.trays:
            slides = []
            settings_changed = False
            for slide in tray.slides:
                key = (tray.number, slide.position)
                if key in selected:
                    reason = _bulk_skip(tray, slide)
                    if reason:
                        skipped[key] = reason
                    else:
                        del skipped[key]
                        before[key] = (slide.settings, slide.status, slide.owner_decided)
                        updated = _bulk_slide(slide, edit(key, slide))
                        settings_changed |= updated.settings != slide.settings
                        slide = updated
                slides.append(slide)
            trays.append(_reviewed_tray(tray, tuple(slides), render_changed=settings_changed))
        return dataclasses.replace(manifest, trays=tuple(trays))

    store.update(change)
    return BulkEditResult(skipped, before)


def set_status_many(
    store: BatchStore,
    keys: Iterable[SlideKey],
    status: SlideStatus,
) -> BulkEditResult:
    """Set an owner decision in one update, skipping unsafe trays and blank slides."""
    if status not in (*SETTABLE_STATUSES, SlideStatus.PENDING):
        raise BatchError(f"a slide cannot be set to {status}")
    return _edit_many(store, keys, lambda key, slide: (slide.settings, status, True))


def apply_settings_many(
    store: BatchStore,
    keys: Iterable[SlideKey] | Mapping[SlideKey, SlideSettings],
    changes: SlideSettings | Mapping[str, object] | Mapping[SlideKey, SlideSettings] | None = None,
) -> BulkEditResult:
    """Apply common partial/full settings or a per-key settings map atomically.

    The two-argument form accepts a map from slide keys to full settings. Partial
    settings preserve every field omitted from the mapping on each selected slide.
    """
    if changes is None:
        if not isinstance(keys, Mapping):
            raise TypeError("provide settings changes or a per-key settings map")
        changes = dict(keys)
    elif isinstance(changes, Mapping):
        changes = dict(changes)

    def edit(key: SlideKey, slide: SlideRecord) -> BeforeEdit:
        if isinstance(changes, SlideSettings):
            settings = changes
        elif key in changes:
            settings = changes[key]
            if not isinstance(settings, SlideSettings):
                raise TypeError("per-key settings must be SlideSettings")
        else:
            settings = dataclasses.replace(slide.settings, **changes)
        return settings, slide.status, True

    return _edit_many(store, keys, edit)


def restore_many(
    store: BatchStore,
    before: Mapping[SlideKey, BeforeEdit],
) -> BulkEditResult:
    """Restore settings, status and owner decisions through the same atomic edit path.

    Changed settings invalidate previews and exports again: undo cannot restore an
    output file that may already have been rendered using the intervening settings.
    """
    saved = dict(before)
    return _edit_many(store, saved, lambda key, slide: saved[key])
