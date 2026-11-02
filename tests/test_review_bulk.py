"""Atomic bulk review edits, inverse snapshots and render invalidation."""

import dataclasses
from time import perf_counter

import pytest

from opticfilm_linux.batch.model import (
    BatchManifest,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.review import apply_settings_many, restore_many, set_status_many
from opticfilm_linux.batch.store import BatchError, BatchStore


class MemoryStore:
    def __init__(self, *trays):
        self.manifest = BatchManifest(name="bulk", trays=trays)
        self.updates = 0

    def update(self, change):
        self.updates += 1
        self.manifest = change(self.manifest)
        return self.manifest

    def snapshot(self):
        return self.manifest


def tray(number=1, stage=TrayStage.REVIEW, **changes):
    return TrayRecord(number, stage, slides=(SlideRecord(1), SlideRecord(2)), **changes)


def slide(store, number=1, position=1):
    return next(
        s
        for t in store.snapshot().trays
        if t.number == number
        for s in t.slides
        if s.position == position
    )


@pytest.mark.parametrize(
    "status", [SlideStatus.ACCEPTED, SlideStatus.NEEDS_LOOK, SlideStatus.PENDING]
)
def test_status_many_atomic_deduplicated_and_invertible(status):
    store = MemoryStore(tray(), tray(2))
    result = set_status_many(store, [(1, 1), (2, 2), (1, 1)], status)
    assert store.updates == 1
    assert result.skipped == {}
    assert len(result.before) == 2
    assert slide(store).status is status and slide(store).owner_decided
    assert result.before[1, 1] == (SlideSettings(), SlideStatus.PENDING, False)
    with pytest.raises(TypeError):
        result.before[1, 1] = None
    with pytest.raises(TypeError):
        result.skipped[9, 9] = "missing"
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.before = {}


@pytest.mark.parametrize(
    "operation",
    [
        lambda store, keys: set_status_many(store, keys, SlideStatus.ACCEPTED),
        lambda store, keys: apply_settings_many(store, keys, {"rotation": 90}),
    ],
)
def test_skipped_reasons_and_stages(operation):
    blank = dataclasses.replace(tray(), slides=(SlideRecord(1, SlideStatus.BLANK),))
    store = MemoryStore(
        blank,
        tray(2, raw_removed=True),
        *(
            tray(n + 3, stage)
            for n, stage in enumerate(
                [TrayStage.SCANNING, TrayStage.SCANNED, TrayStage.ENHANCING, TrayStage.FAILED]
            )
        ),
        tray(7, TrayStage.EXPORTED),
    )
    result = operation(store, [(n, 1) for n in range(1, 9)] + [(7, 9)])
    assert result.skipped == {
        (1, 1): "blank",
        (2, 1): "raw_removed",
        (3, 1): "not_editable",
        (4, 1): "not_editable",
        (5, 1): "not_editable",
        (6, 1): "not_editable",
        (8, 1): "missing",
        (7, 9): "missing",
    }
    assert set(result.before) == {(7, 1)}
    assert store.updates == 1


def test_partial_settings_preserve_other_fields_and_invalidate_export():
    original = SlideRecord(
        1, SlideStatus.ACCEPTED, settings=SlideSettings(mirror=True), exported=True
    )
    store = MemoryStore(dataclasses.replace(tray(stage=TrayStage.EXPORTED), slides=(original,)))
    result = apply_settings_many(store, [(1, 1)], {"rotation": 90})
    updated = slide(store)
    assert updated.settings == SlideSettings(rotation=90, mirror=True)
    assert updated.status is SlideStatus.ACCEPTED and updated.owner_decided
    assert updated.preview_stale and not updated.exported
    assert store.snapshot().trays[0].stage is TrayStage.REVIEW
    assert result.before[1, 1] == (original.settings, original.status, False)
    assert store.updates == 1


def test_unchanged_settings_preserve_preview_export_and_exported_tray():
    original = SlideRecord(1, exported=True)
    store = MemoryStore(dataclasses.replace(tray(stage=TrayStage.EXPORTED), slides=(original,)))
    apply_settings_many(store, [(1, 1)], SlideSettings())
    assert slide(store).exported and not slide(store).preview_stale
    assert store.snapshot().trays[0].stage is TrayStage.EXPORTED


def test_per_key_settings_and_exact_undo():
    original = SlideRecord(1, SlideStatus.NEEDS_LOOK, settings=SlideSettings(mirror=True))
    store = MemoryStore(dataclasses.replace(tray(), slides=(original, SlideRecord(2))))
    result = apply_settings_many(
        store, {(1, 1): SlideSettings(rotation=90), (1, 2): SlideSettings(brightness_ev=1)}
    )
    set_status_many(store, [(1, 1)], SlideStatus.ACCEPTED)
    undo = restore_many(store, result.before)
    restored = slide(store)
    assert (restored.settings, restored.status, restored.owner_decided) == (
        original.settings,
        original.status,
        original.owner_decided,
    )
    assert restored.preview_stale and not restored.exported
    assert slide(store, position=2).settings == SlideSettings()
    assert len(undo.before) == 2 and not undo.skipped
    assert store.updates == 3


def test_undo_skips_freed_or_busy_trays():
    store = MemoryStore(tray(), tray(2))
    result = apply_settings_many(store, [(1, 1), (2, 1)], {"rotation": 90})
    store.manifest = dataclasses.replace(
        store.manifest,
        trays=(
            dataclasses.replace(store.manifest.trays[0], raw_removed=True),
            dataclasses.replace(store.manifest.trays[1], stage=TrayStage.SCANNING),
        ),
    )
    undone = restore_many(store, result.before)
    assert undone.skipped == {(1, 1): "raw_removed", (2, 1): "not_editable"}
    assert not undone.before
    assert slide(store).settings.rotation == 90


@pytest.mark.parametrize(
    "operation",
    [
        lambda store: set_status_many(store, [], SlideStatus.ACCEPTED),
        lambda store: apply_settings_many(store, [], {"rotation": 90}),
        lambda store: restore_many(store, {}),
    ],
)
def test_empty_selection_is_one_harmless_atomic_update(operation):
    store = MemoryStore(tray())
    original = store.manifest
    result = operation(store)
    assert not result.before and not result.skipped
    assert store.manifest == original and store.updates == 1


def test_invalid_status_or_settings_leave_manifest_untouched():
    store = MemoryStore(tray())
    original = store.manifest
    with pytest.raises(BatchError):
        set_status_many(store, [(1, 1)], SlideStatus.BLANK)
    with pytest.raises(ValueError):
        apply_settings_many(store, [(1, 1)], {"rotation": 45})
    with pytest.raises(TypeError):
        apply_settings_many(store, [(1, 1)], {"unknown": 0})
    assert store.manifest == original


def test_real_store_bulk_edit_and_undo_persist(tmp_path):
    with BatchStore.create(tmp_path / "batch", name="bulk") as store:
        store.update(lambda m: dataclasses.replace(m, trays=(tray(),)))
        result = apply_settings_many(store, [(1, 1)], {"brightness_ev": 1})
        restore_many(store, result.before)
        with BatchStore.open(store.directory, readonly=True) as reopened:
            assert reopened.snapshot() == store.snapshot()
            assert slide(reopened).settings == SlideSettings()
            assert not slide(reopened).owner_decided


def test_select_and_edit_4000_slides_is_not_quadratic(tmp_path):
    from opticfilm_linux.app.selection import select_all, selectable_keys

    trays = tuple(
        dataclasses.replace(tray(n), slides=tuple(SlideRecord(p) for p in range(1, 5)))
        for n in range(1, 1001)
    )
    with BatchStore.create(tmp_path / "large", name="bulk") as store:
        store.update(lambda manifest: dataclasses.replace(manifest, trays=trays))
        started = perf_counter()
        selection = select_all(selectable_keys(store.snapshot()))
        result = apply_settings_many(store, selection.keys, {"brightness_ev": 0.5})
        assert perf_counter() - started < 8.0  # generous: guards a quadratic blowup, not CI speed
        assert len(result.before) == 4000
