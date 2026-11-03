"""Paused interrupted cleanup is waiting; completed removal can still reconcile."""

from pathlib import Path

import pytest

from opticfilm_linux.batch.model import BatchManifest, TrayRecord, TrayStage
from test_app_state import _build


@pytest.mark.parametrize("remaining", [False, True])
@pytest.mark.parametrize("error", ["", "Retry cleanup"])
def test_interrupted_cleanup_status_uses_existing_inventory_without_filesystem_reads(
    monkeypatch, remaining, error
):
    tray = TrayRecord(1, TrayStage.EXPORTED, auto="verified", auto_error=error, raw_removed=True)
    manifest = BatchManifest("Recovery", trays=(tray,))
    monkeypatch.setattr(Path, "glob", lambda *_: pytest.fail("state must use supplied evidence"))
    state = _build(manifest, raw_pending=frozenset({1}) if remaining else frozenset())
    waiting = remaining or bool(error)
    assert f"{int(not waiting)} in progress" in state.auto_status
    assert f"{int(waiting)} waiting" in state.auto_status


def test_other_tray_inventory_does_not_pause_completed_cleanup():
    tray = TrayRecord(1, TrayStage.EXPORTED, auto="verified", raw_removed=True)
    state = _build(BatchManifest("Recovery", trays=(tray,)), raw_pending=frozenset({2}))
    assert "1 in progress" in state.auto_status
    assert "0 waiting" in state.auto_status
