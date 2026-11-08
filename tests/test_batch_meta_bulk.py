"""Bulk description and date edits: every scope, its count, one undo step, the cap in append."""

import dataclasses

import pytest

from opticfilm_linux.batch.meta_bulk import BulkMetaEdit, apply, plan
from opticfilm_linux.batch.metadata import MAX_DESCRIPTION, MetaOverride, resolve
from opticfilm_linux.batch.model import (
    BatchManifest,
    SlideRecord,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.review import restore_meta_many


class MemoryStore:
    def __init__(self, manifest):
        self.manifest = manifest
        self.updates = 0

    def update(self, change):
        self.updates += 1
        self.manifest = change(self.manifest)
        return self.manifest

    def snapshot(self):
        return self.manifest


def _tray(number, stage=TrayStage.REVIEW, **changes):
    slides = tuple(SlideRecord(p, SlideStatus.ACCEPTED) for p in (1, 2))
    return TrayRecord(number, stage, slides=slides, **changes)


def _store(*trays, **batch):
    return MemoryStore(BatchManifest(name="bulk", trays=trays, **batch))


def _text(manifest, number, position, field="description"):
    tray = next(t for t in manifest.trays if t.number == number)
    slide = next(s for s in tray.slides if s.position == position)
    meta = resolve(manifest, tray, slide)
    return meta.description if field == "description" else (meta.date.text() if meta.date else "")


def _edit(scope, value, field="description", **kw):
    return BulkMetaEdit(scope, field, value, **kw)


def test_entire_batch_reaches_every_inheriting_slide_and_keeps_own_values():
    store = _store(
        _tray(1, TrayStage.EXPORTED),
        _tray(2, meta=MetaOverride(description="Tray own")),
        _tray(3),
        description="Old",
    )
    slide = dataclasses.replace(store.manifest.trays[2].slides[0], meta=MetaOverride("Mine"))
    store.manifest = dataclasses.replace(
        store.manifest,
        trays=(
            *store.manifest.trays[:2],
            dataclasses.replace(
                store.manifest.trays[2], slides=(slide, store.manifest.trays[2].slides[1])
            ),
        ),
    )
    preview = plan(store.manifest, _edit("batch", "New"))
    assert (preview.slides_changed, preview.trays_changed) == (3, 0)  # tray 1 (2) + tray 3 slide 2
    assert preview.summary() == "Changes 3 slides"
    apply(store, _edit("batch", "New"))
    m = store.manifest
    assert m.description == "New"
    assert [_text(m, 1, 1), _text(m, 1, 2)] == ["New", "New"]
    assert _text(m, 2, 1) == "Tray own" and _text(m, 3, 1) == "Mine" and _text(m, 3, 2) == "New"


def test_from_now_on_stamps_the_old_value_and_changes_no_existing_slide():
    store = _store(_tray(1), _tray(2, meta=MetaOverride(description="Own")), description="Old")
    before = {
        (t.number, s.position): _text(store.manifest, t.number, s.position)
        for t in store.manifest.trays
        for s in t.slides
    }
    preview = plan(store.manifest, _edit("from_now_on", "New"))
    assert preview.slides_changed == 0 and preview.trays_changed == 1
    assert (
        "trays scanned from now on"
        in plan(_store(description="Old").manifest, _edit("from_now_on", "New")).summary()
    )
    apply(store, _edit("from_now_on", "New"))
    m = store.manifest
    assert m.description == "New" and m.trays[0].meta.description == "Old"
    assert {
        (t.number, s.position): _text(m, t.number, s.position) for t in m.trays for s in t.slides
    } == before
    later = TrayRecord(3, TrayStage.REVIEW, slides=(SlideRecord(1, SlideStatus.ACCEPTED),))
    assert _text(dataclasses.replace(m, trays=(*m.trays, later)), 3, 1) == "New"


def test_selected_slides_and_one_tray():
    store = _store(_tray(1), _tray(2))
    apply(store, _edit("slides", "A", keys=frozenset({(1, 2), (2, 1)})))
    m = store.manifest
    assert [_text(m, 1, 1), _text(m, 1, 2), _text(m, 2, 1), _text(m, 2, 2)] == ["", "A", "A", ""]
    preview = plan(m, _edit("tray", "T", tray=2))
    assert (preview.slides_changed, preview.trays_changed) == (1, 1)  # slide 2.1 keeps its own
    apply(store, _edit("tray", "T", tray=2))
    assert _text(store.manifest, 2, 2) == "T" and _text(store.manifest, 2, 1) == "A"
    with pytest.raises(ValueError):
        plan(m, _edit("slides", "x"))


def test_tray_range_stamps_each_existing_tray_in_the_range():
    store = _store(*(_tray(n) for n in range(1, 8)))
    edit = _edit("tray_range", "1975", "date", first=3, last=5)
    preview = plan(store.manifest, edit)
    assert (preview.slides_changed, preview.trays_changed) == (6, 3)
    apply(store, edit)
    assert [t.meta.date for t in store.manifest.trays] == [
        None,
        None,
        "1975",
        "1975",
        "1975",
        None,
        None,
    ]
    assert plan(store.manifest, _edit("tray_range", "", "date", first=4, last=9)).trays_changed == 4
    for bad in (dict(first=5, last=3), dict(first=20, last=30), dict(first=None, last=None)):
        with pytest.raises(ValueError):
            plan(store.manifest, _edit("tray_range", "x", **bad))
    with pytest.raises(ValueError):
        plan(store.manifest, _edit("tray_range", "1800", "date", first=1, last=2))


def test_fill_empty_never_overwrites_a_value_at_any_level():
    slide = SlideRecord(1, SlideStatus.ACCEPTED, meta=MetaOverride("slide own"))
    blank = SlideRecord(2, SlideStatus.ACCEPTED, meta=MetaOverride(""))
    store = _store(
        TrayRecord(
            1, TrayStage.REVIEW, slides=(slide, blank, SlideRecord(3, SlideStatus.ACCEPTED))
        ),
        _tray(2, meta=MetaOverride(description="tray own")),
        _tray(3, meta=MetaOverride(description="")),
        _tray(4),
    )
    preview = plan(store.manifest, _edit("empty_only", "Filled"))
    assert preview.slides_changed == 3  # tray 1 slide 3 and both slides of tray 4
    apply(store, _edit("empty_only", "Filled"))
    m = store.manifest
    assert [_text(m, 1, p) for p in (1, 2, 3)] == ["slide own", "", "Filled"]
    assert _text(m, 2, 1) == "tray own" and _text(m, 3, 1) == ""
    assert _text(m, 4, 1) == _text(m, 4, 2) == "Filled"
    with pytest.raises(ValueError):
        plan(m, _edit("empty_only", ""))  # fill only: no blanking or resetting
    # a batch value fills nothing, and the date is filled the same way
    withbatch = dataclasses.replace(m, date="1970")
    assert plan(withbatch, _edit("empty_only", "1980", "date")).slides_changed == 0
    assert plan(m, _edit("empty_only", "1980", "date")).slides_changed == 9


def test_append_adds_with_a_separator_at_slide_level_and_is_not_repeated():
    store = _store(
        _tray(1, meta=MetaOverride(description="Tray")),
        _tray(2),
        description="Batch",
    )
    store.manifest = dataclasses.replace(
        store.manifest,
        trays=(
            store.manifest.trays[0],
            dataclasses.replace(
                store.manifest.trays[1],
                slides=(
                    SlideRecord(1, SlideStatus.ACCEPTED, meta=MetaOverride("Own")),
                    SlideRecord(2, SlideStatus.ACCEPTED),
                ),
            ),
        ),
    )
    apply(store, _edit("append", "with Anna"))
    m = store.manifest
    assert [_text(m, 1, 1), _text(m, 2, 1), _text(m, 2, 2)] == [
        "Tray; with Anna",
        "Own; with Anna",
        "Batch; with Anna",
    ]
    assert m.description == "Batch" and m.trays[0].meta.description == "Tray"  # levels untouched
    assert plan(m, _edit("append", "with Anna")).slides_changed == 0
    only = plan(m, _edit("append", "x", tray=2))
    assert only.slides_changed == 2
    selected = plan(m, _edit("append", "x", keys=frozenset({(1, 1)})))
    assert selected.slides_changed == 1
    with pytest.raises(ValueError, match="description only"):
        plan(m, _edit("append", "1980", "date"))
    with pytest.raises(ValueError):
        plan(m, _edit("append", "  "))


def test_append_over_the_cap_is_refused_whole():
    long = "x" * (MAX_DESCRIPTION - 3)
    store = _store(_tray(1), _tray(2, meta=MetaOverride(description=long)))
    with pytest.raises(ValueError, match="would pass 500 characters.*tray 2, slide 1.*nothing"):
        apply(store, _edit("append", "abcdef"))
    assert store.manifest.trays[0].meta == MetaOverride()  # nothing was written
    assert all(s.meta == MetaOverride() for t in store.manifest.trays for s in t.slides)
    apply(store, _edit("append", "a"))  # 497 + "; " + 1 is exactly the cap
    assert len(_text(store.manifest, 2, 1)) == MAX_DESCRIPTION


@pytest.mark.parametrize(
    "edit",
    [
        _edit("batch", "New"),
        _edit("from_now_on", "New"),
        _edit("slides", "New", keys=frozenset({(1, 1), (2, 2)})),
        _edit("tray", "New", tray=2),
        _edit("tray_range", "New", first=1, last=2),
        _edit("empty_only", "New"),
        _edit("append", "New"),
    ],
    ids=lambda e: e.scope,
)
def test_every_scope_is_one_write_one_exact_undo_and_touches_nothing_else(edit):
    store = _store(_tray(1), _tray(2, meta=MetaOverride(date="1970")))
    original = store.manifest
    result, planned = apply(store, edit)
    assert store.updates == 1 and not result.before.empty
    changed = store.manifest
    # only description and date: statuses, settings and the render signature are the same
    assert changed.defaults == original.defaults
    for old, new in zip(original.trays, changed.trays, strict=True):
        assert (old.stage, old.auto, old.raw_removed) == (new.stage, new.auto, new.raw_removed)
        for a, b in zip(old.slides, new.slides, strict=True):
            assert dataclasses.replace(b, meta=a.meta) == a
    restore_meta_many(store, result.before)
    assert store.manifest == original
    assert planned.before == result.before


def test_a_refused_edit_writes_nothing_and_an_unchanged_one_has_nothing_to_undo():
    store = _store(_tray(1), description="Old")
    with pytest.raises(ValueError):
        apply(store, _edit("batch", "x" * 501))
    with pytest.raises(ValueError):
        apply(store, _edit("nonsense", "x"))
    with pytest.raises(ValueError):
        apply(store, _edit("batch", "x", field="settings"))
    assert store.manifest.description == "Old"
    result, planned = apply(store, _edit("batch", "Old"))
    assert result.before.empty and planned.summary() == "Changes nothing"


def test_append_only_skips_text_that_is_the_whole_or_a_separated_ending():
    store = _store(_tray(1, meta=MetaOverride(description="Lake party")), description="party")
    # "Lake party" merely ends with the letters "party": the text is still added
    assert plan(store.manifest, _edit("append", "party", tray=1)).slides_changed == 2
    apply(store, _edit("append", "party", tray=1))
    assert _text(store.manifest, 1, 1) == "Lake party; party"
    assert plan(store.manifest, _edit("append", "party", tray=1)).slides_changed == 0
    store.manifest = dataclasses.replace(store.manifest, trays=(_tray(2),), description="party")
    assert (
        plan(store.manifest, _edit("append", "party")).slides_changed == 0
    )  # it is the whole text
