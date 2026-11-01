import dataclasses
import shutil

import pytest

from opticfilm_linux.batch import export
from opticfilm_linux.batch.model import SlideStatus, TrayStage
from opticfilm_linux.batch.store import BatchError, BatchStore
from test_export import _mark
from test_export import template as template


@pytest.fixture
def store(tmp_path, template):
    shutil.copytree(template, tmp_path / "b")
    with BatchStore.open(tmp_path / "b") as opened:
        yield opened


def test_clear_exported_changes_only_chosen_accepted_slides_atomically(store, monkeypatch):
    for number in (1, 2):
        store.update_tray(number, stage=TrayStage.EXPORTED)
        for position in range(1, 5):
            _mark(store, number, position, exported=True)
    _mark(store, 1, 2, status=SlideStatus.NEEDS_LOOK)
    before = store.snapshot()
    calls = []
    real = store.update

    def update(change):
        calls.append(True)
        return real(change)

    monkeypatch.setattr(store, "update", update)
    export.clear_exported(store, frozenset({(1, 1), (1, 2)}))
    after = store.snapshot()
    assert len(calls) == 1
    assert after.trays[0].stage is TrayStage.REVIEW
    assert after.trays[0].slides[0] == dataclasses.replace(
        before.trays[0].slides[0], exported=False
    )
    assert after.trays[0].slides[1:] == before.trays[0].slides[1:]
    assert after.trays[1] == before.trays[1]


def test_clear_exported_refuses_freed_raw_without_partial_changes(store):
    store.update_tray(2, stage=TrayStage.EXPORTED, raw_removed=True)
    _mark(store, 1, 1, exported=True)
    before = store.snapshot()
    with pytest.raises(BatchError, match="raw scans.*removed"):
        export.clear_exported(store, frozenset({(1, 1), (2, 1)}))
    assert store.snapshot() == before


def test_clear_exported_defaults_to_all_accepted_and_preserves_other_choices(store):
    _mark(store, 1, 1, status=SlideStatus.NEEDS_LOOK, exported=True)
    _mark(store, 1, 2, exported=True)
    export.clear_exported(store)
    assert store.snapshot().trays[0].slides[0].exported
    assert not store.snapshot().trays[0].slides[1].exported


def test_export_keys_filter_limits_writes_and_progress(store, tmp_path):
    updates = []
    result = export.export_batch(
        store, tmp_path / "out", keys=frozenset({(1, 2), (2, 3)}), on_progress=updates.append
    )
    assert len(result.written) == 2
    assert updates[-1].done == updates[-1].total == 2
    marked = {
        (t.number, s.position) for t in store.snapshot().trays for s in t.slides if s.exported
    }
    assert marked == {(1, 2), (2, 3)}


def test_empty_export_keys_never_means_all(store, tmp_path):
    result = export.export_batch(store, tmp_path / "out", keys=frozenset())
    assert result.written == ()


def test_reexport_keeps_old_numbered_files(store, tmp_path):
    first = export.export_batch(store, tmp_path / "out")
    original = {p: p.read_bytes() for p in first.written}
    export.clear_exported(store)
    second = export.export_batch(store, tmp_path / "out")
    assert len(second.written) == len(first.written) == 8
    assert set(first.written).isdisjoint(second.written)
    assert all(p.read_bytes() == content for p, content in original.items())
