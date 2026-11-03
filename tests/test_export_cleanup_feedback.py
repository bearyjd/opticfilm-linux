"""Cleanup reports actionable recovery advice and preserves manual progress."""

import dataclasses

import pytest

from opticfilm_linux.batch import export
from test_export_safety import _delivered
from test_export_safety import delivery as delivery


@pytest.mark.parametrize("progress", ["", "off"])
def test_manual_cleanup_preserves_manual_progress(delivery, progress):
    store, _ = _delivered(delivery)
    store.update_tray(1, auto=progress)
    assert export.cleanup_raw(store, 1) > 0
    assert store.snapshot().trays[0].raw_removed
    assert store.snapshot().trays[0].auto == progress


def test_cleanup_completion_preserves_concurrent_owner_progress(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    store.update_tray(1, auto="held")
    remove = export.shutil.rmtree

    def remove_and_change(path):
        remove(path)
        store.update_tray(1, auto="off", auto_error="owner changed progress")

    monkeypatch.setattr(export.shutil, "rmtree", remove_and_change)
    assert export.cleanup_raw(store, 1, release_first=True) > 0
    tray = store.snapshot().trays[0]
    assert tray.raw_removed and tray.auto == "off"
    assert tray.auto_error == "owner changed progress"


def test_unrecoverable_release_block_explains_restore_or_rescan(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    first = store.snapshot().trays[0]
    second = dataclasses.replace(first, number=2)
    store.update(lambda m: dataclasses.replace(m, first_tray_released=True, trays=(first, second)))
    store.update_tray(1, raw_removed=True, cleanup_sweeps=(1,))

    def damaged_current(*args):
        raise export.CleanupRefused("current raw sweep is damaged")

    monkeypatch.setattr(export, "_checked_snapshot", damaged_current)
    with pytest.raises(export.CleanupPolicyRefused, match="restore.*rescan.*before freeing"):
        export.check_cleanup_allowed(store, 2)
    assert store.sweep_dir(1, 1).exists()
