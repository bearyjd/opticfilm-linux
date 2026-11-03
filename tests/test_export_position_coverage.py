import dataclasses

import pytest

from opticfilm_linux.batch import export
from test_export_safety import _assert_raw_kept, _confirmed_blank, _delivered
from test_export_safety import delivery as delivery


@pytest.mark.parametrize("positions", [(1,), (1, 1), (1, 3), (0, 2), (-1, 2), (True, 2)])
def test_cleanup_requires_unique_complete_raw_positions(delivery, monkeypatch, positions):
    store, _ = _delivered(delivery)
    split = export.split_slides(None)
    monkeypatch.setattr(export, "split_slides", lambda _: (*split, *split))
    slide = store.snapshot().trays[0].slides[0]
    store.update_tray(1, slides=tuple(dataclasses.replace(slide, position=p) for p in positions))
    with pytest.raises(export.CleanupRefused, match="positions"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_blank_positions_count_toward_complete_raw_coverage(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    split = export.split_slides(None)
    monkeypatch.setattr(export, "split_slides", lambda _: (*split, *split))
    slide = store.snapshot().trays[0].slides[0]
    blank = _confirmed_blank(store.snapshot().trays[0], slide, position=2, exports=())
    store.update_tray(1, slides=(slide, blank))
    assert export.cleanup_raw(store, 1) > 0
    assert store.snapshot().trays[0].raw_removed


def test_duplicate_blank_cannot_conceal_unrepresented_raw_position(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    split = export.split_slides(None)
    monkeypatch.setattr(export, "split_slides", lambda _: (*split, *split))
    slide = store.snapshot().trays[0].slides[0]
    blank = _confirmed_blank(store.snapshot().trays[0], slide, exports=())
    store.update_tray(1, slides=(slide, blank))
    with pytest.raises(export.CleanupRefused, match="positions"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


@pytest.mark.parametrize("change", [{"sweep_version": 2}, {"mode": "negative"}])
def test_export_receipt_cannot_attach_after_scan_or_mode_change(delivery, change):
    store, _ = _delivered(delivery)
    before = store.snapshot()
    old_tray = before.trays[0]
    old_slide = old_tray.slides[0]
    store.update_tray(1, slides=(dataclasses.replace(old_slide, exported=False, exports=()),))
    store.update_tray(1, **change)
    changed = store.snapshot()
    result = export._mark_exported(
        store,
        1,
        1,
        old_tray.sweep_version,
        old_tray.mode,
        old_slide.settings,
        before.defaults,
        record=old_slide.exports[-1],
    )
    assert result == "stale_tray"
    assert store.snapshot() == changed
