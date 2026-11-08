"""Metadata edits: their own skip rule, exact undo snapshots and tray pinning "from now on"."""

import dataclasses
import itertools
from pathlib import Path

import pytest

from opticfilm_linux.batch.export_contract import _render_signature
from opticfilm_linux.batch.metadata import MetaOverride, checked_override, resolve
from opticfilm_linux.batch.model import (
    BatchManifest,
    SlideRecord,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.review import (
    NoSuchSlide,
    edit_batch_meta,
    edit_meta_many,
    edit_tray_meta,
    restore_meta_many,
)
from opticfilm_linux.batch.store import BatchStore


class MemoryStore:
    def __init__(self, *trays, **batch):
        self.manifest = BatchManifest(name="meta", trays=trays, **batch)
        self.updates = 0

    def update(self, change):
        self.updates += 1
        self.manifest = change(self.manifest)
        return self.manifest

    def snapshot(self):
        return self.manifest


def _tray(number, stage=TrayStage.REVIEW, statuses=(SlideStatus.PENDING,) * 2, **changes):
    slides = tuple(SlideRecord(p, s) for p, s in enumerate(statuses, 1))
    skipped = tuple(s.position for s in slides if s.status is SlideStatus.SKIPPED)
    return TrayRecord(number, stage, slides=slides, skipped=skipped, **changes)


def _slide(store, number, position):
    tray = next(t for t in store.snapshot().trays if t.number == number)
    return next(s for s in tray.slides if s.position == position)


def _tray_meta(store, number):
    return next(t for t in store.snapshot().trays if t.number == number).meta


def test_checked_override_keeps_blank_and_inherit_apart_and_is_strict():
    assert checked_override("description", None) is None
    assert checked_override("description", "  ") == ""
    assert checked_override("date", "") == ""
    assert checked_override("date", " 1978-07 ") == "1978-07"
    for field, value in (("date", "1978-07-14"), ("date", "1850"), ("description", "x" * 501)):
        with pytest.raises(ValueError):
            checked_override(field, value)
    with pytest.raises(ValueError):
        checked_override("settings", "x")


def test_only_missing_keys_are_skipped():
    store = MemoryStore(
        _tray(1, raw_removed=True),
        _tray(2, TrayStage.EXPORTED, (SlideStatus.BLANK, SlideStatus.SKIPPED)),
        _tray(3, TrayStage.SCANNING),
    )
    keys = [(1, 1), (2, 1), (2, 2), (3, 2), (9, 9)]
    result = edit_meta_many(store, keys, {"description": "Lake"})
    assert result.skipped == {(9, 9): "missing"}
    assert set(result.before.slides) == set(keys) - {(9, 9)}
    assert all(_slide(store, *key).meta.description == "Lake" for key in keys[:-1])
    assert store.updates == 1


def test_slide_edit_then_restore_gives_exactly_the_previous_meta():
    old = MetaOverride("old", "1970")
    store = MemoryStore(_tray(1), dataclasses.replace(_tray(2)))
    store.update(lambda m: _with_slide_meta(m, (1, 2), old))
    before = store.snapshot()
    result = edit_meta_many(store, [(1, 1), (1, 2)], {"date": "1978-07"})
    assert _slide(store, 1, 2).meta == MetaOverride("old", "1978-07")
    restore_meta_many(store, result.before)
    assert store.snapshot() == before


def _with_slide_meta(manifest, key, meta):
    trays = tuple(
        dataclasses.replace(
            t,
            slides=tuple(
                dataclasses.replace(s, meta=meta) if (t.number, s.position) == key else s
                for s in t.slides
            ),
        )
        for t in manifest.trays
    )
    return dataclasses.replace(manifest, trays=trays)


def test_tray_edit_and_restore_and_missing_tray():
    store = MemoryStore(_tray(1, raw_removed=True), _tray(2))
    result = edit_tray_meta(store, 1, {"description": "", "date": "1980"})
    assert _tray_meta(store, 1) == MetaOverride("", "1980")
    assert result.before.trays == {1: MetaOverride()}
    restore_meta_many(store, result.before)
    assert _tray_meta(store, 1) == MetaOverride()
    with pytest.raises(NoSuchSlide):
        edit_tray_meta(store, 7, {"date": "1980"})


def test_batch_edit_from_now_on_pins_inheriting_trays_in_one_update():
    store = MemoryStore(
        _tray(1),
        _tray(2, meta=MetaOverride(description="own")),
        _tray(3, TrayStage.SCANNING),
        description="Old trip",
        date="1975-07-14",
    )
    result = edit_batch_meta(store, {"description": "New trip"}, scope="from_now_on")
    assert store.updates == 1
    manifest = store.snapshot()
    assert manifest.description == "New trip" and manifest.date == "1975-07-14"
    assert _tray_meta(store, 1) == MetaOverride(description="Old trip")
    assert _tray_meta(store, 2) == MetaOverride(description="own")
    assert _tray_meta(store, 3) == MetaOverride(description="Old trip")  # scanning: it exists
    result2 = edit_batch_meta(store, {"date": "1980"})  # from now on is the default
    assert _tray_meta(store, 1).date is None  # a legacy day date fails the rules: not pinned
    restore_meta_many(store, result2.before)
    restore_meta_many(store, result.before)
    assert store.snapshot().description == "Old trip"
    assert all(t.meta == m for t, m in zip(store.snapshot().trays, _metas(2), strict=True))


def _metas(own):
    return tuple(MetaOverride(description="own") if n == own else MetaOverride() for n in (1, 2, 3))


def test_batch_edit_pins_blank_when_batch_had_no_value_and_inheriting_scope_pins_nothing():
    store = MemoryStore(_tray(1))
    edit_batch_meta(store, {"date": "1980"}, scope="from_now_on")
    assert _tray_meta(store, 1) == MetaOverride(date="")
    assert resolve(store.snapshot(), store.snapshot().trays[0]).date is None
    store = MemoryStore(_tray(1))
    result = edit_batch_meta(store, {"description": "x", "date": ""}, scope="inheriting")
    assert _tray_meta(store, 1) == MetaOverride() and not result.before.trays
    assert store.snapshot().date is None and result.before.batch == ("", None)
    with pytest.raises(ValueError):
        edit_batch_meta(store, {"date": "1980"}, scope="sometimes")


def _render_part(manifest):
    """Everything but the metadata, which must be all a metadata edit changes."""
    trays = tuple(
        dataclasses.replace(
            t,
            meta=MetaOverride(),
            slides=tuple(dataclasses.replace(s, meta=MetaOverride()) for s in t.slides),
        )
        for t in manifest.trays
    )
    return dataclasses.replace(manifest, trays=trays, description="", date=None)


def _every_stage_and_status():
    s = SlideStatus  # a tray holds four openings, so every status takes two trays
    statuses = ((s.PENDING, s.NEEDS_LOOK, s.ACCEPTED, s.BLANK), (s.SKIPPED, s.ACCEPTED))
    combos = itertools.product(TrayStage, (False, True), statuses)
    trays = [
        _tray(n, stage, kinds, raw_removed=removed, auto="held")
        for n, (stage, removed, kinds) in enumerate(combos, 1)
    ]
    assert {x.status for t in trays for x in t.slides} == set(SlideStatus)
    trays = [
        dataclasses.replace(
            t,
            slides=tuple(
                dataclasses.replace(s, exported=True, preview_stale=bool(s.position % 2))
                for s in t.slides
            ),
        )
        for t in trays
    ]
    return MemoryStore(*trays, description="d", date="1970")


EDITS = {
    "slide": lambda store, keys: edit_meta_many(store, keys, {"description": "s", "date": ""}),
    "tray": lambda store, keys: edit_tray_meta(store, keys[0][0], {"date": "1981"}),
    "batch-now": lambda store, keys: edit_batch_meta(store, {"description": "b"}),
    "batch-all": lambda store, keys: edit_batch_meta(store, {"date": "1982"}, scope="inheriting"),
}


@pytest.mark.parametrize("edit", sorted(EDITS))
def test_no_metadata_edit_touches_render_status_or_export_state(edit):
    store = _every_stage_and_status()
    before = store.snapshot()
    keys = [(t.number, s.position) for t in before.trays for s in t.slides]
    result = EDITS[edit](store, keys)
    after = store.snapshot()
    assert after != before
    assert _render_part(after) == _render_part(before)
    root = Path("/out")
    signature = _render_signature(before.defaults, root, "jpeg", "full")
    assert _render_signature(after.defaults, root, "jpeg", "full") == signature
    restore_meta_many(store, result.before)
    assert store.snapshot() == before


def test_blank_override_survives_a_reload_from_disk(tmp_path):
    with BatchStore.create(tmp_path / "b", name="disk") as store:
        store.update(lambda m: dataclasses.replace(m, trays=(_tray(1),)))
        edit_meta_many(store, [(1, 1)], {"description": "", "date": ""})
        edit_tray_meta(store, 1, {"description": ""})
    with BatchStore.open(tmp_path / "b") as store:
        assert _slide(store, 1, 1).meta == MetaOverride("", "")
        assert _tray_meta(store, 1) == MetaOverride(description="")


def test_no_op_edits_change_nothing_and_leave_nothing_to_undo():
    store = MemoryStore(_tray(1), description="Trip", date="1970")
    before = store.snapshot()
    for result in (
        edit_batch_meta(store, {"description": "Trip"}),
        edit_batch_meta(store, {"date": "1970"}, scope="from_now_on"),
        edit_batch_meta(store, {}),
        edit_tray_meta(store, 1, {"description": None}),
        edit_tray_meta(store, 1, {}),
        edit_meta_many(store, [(1, 1), (1, 2)], {"date": None}),
    ):
        assert result.before.empty
    assert store.snapshot() == before


def test_pinning_skips_values_that_break_the_rules_and_keeps_them_inheriting():
    store = MemoryStore(_tray(1), _tray(2), description="x" * 600, date="1975-07-14")
    edit_batch_meta(store, {"description": "New", "date": "1980"}, scope="from_now_on")
    assert _tray_meta(store, 1) == _tray_meta(store, 2) == MetaOverride()
    store = MemoryStore(_tray(1), description="Trip", date="1975-07-14")
    edit_batch_meta(store, {"description": "New", "date": "1980"}, scope="from_now_on")
    assert _tray_meta(store, 1) == MetaOverride(description="Trip")
    store = MemoryStore(_tray(1), description="Trip")  # the default, the entire batch, pins nothing
    edit_batch_meta(store, {"description": "New"})
    assert _tray_meta(store, 1) == MetaOverride() and store.snapshot().description == "New"


def test_batch_value_of_the_wrong_type_is_a_value_error():
    store = MemoryStore(_tray(1))
    for change in ({"date": 1978}, {"description": 5}, {"rotation": "90"}):
        with pytest.raises(ValueError, match="must be"):
            edit_batch_meta(store, change)
