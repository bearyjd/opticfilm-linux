"""Contact-sheet commands preserve their captured selection and batch session."""

import dataclasses
import threading
from unittest.mock import Mock

from controller_helpers import wait_until
from opticfilm_linux.batch.model import Flag, SlideStatus, TrayStage
from test_app_selection_commands import _slides

pytest_plugins = ["test_app_selection_commands"]


def test_tray_range_includes_whole_anchor_and_target_trays(selected):
    selected.select_slide(1, 2)
    selected.select_tray_slides(3, shift=True)
    assert selected.state().selection == set(selected._selection_order)
    selected.select_slide(3, 2)
    selected.select_tray_slides(1, shift=True)
    assert selected.state().selection == set(selected._selection_order)


def test_filters_read_cached_manifest_and_skip_busy(selected, monkeypatch):
    store = selected._session.store
    tray = store.snapshot().trays[0]
    slides = (dataclasses.replace(tray.slides[0], flags=(Flag.DUST,)), tray.slides[1])
    store.update_tray(1, slides=slides, auto_flagged=(2,))
    store.update_tray(2, auto_failed=(2,))
    store.update_tray(3, stage=TrayStage.FAILED, auto="failed")
    wait_until(selected, lambda: selected._manifest.trays[2].stage is TrayStage.FAILED)
    monkeypatch.setattr(store, "snapshot", Mock(side_effect=AssertionError("UI disk read")))
    selected.select_where("flagged")
    assert selected.state().selection == {(1, 1), (1, 2)}
    selected.select_where("failed")
    assert selected.state().selection == {(2, 2), (3, 1), (3, 2)}


def test_toggle_accept_and_undo_restore_owner_decisions(selected):
    selected.select_all()
    selected.toggle_selection_accepted()
    wait_until(
        selected, lambda: all(s.status is SlideStatus.ACCEPTED for s in _slides(selected).values())
    )
    selected.toggle_selection_accepted()
    wait_until(
        selected, lambda: all(s.status is SlideStatus.PENDING for s in _slides(selected).values())
    )
    selected.undo()
    wait_until(
        selected, lambda: all(s.status is SlideStatus.ACCEPTED for s in _slides(selected).values())
    )
    selected.undo()
    wait_until(selected, lambda: all(not s.owner_decided for s in _slides(selected).values()))


def test_undo_settings_queues_preview_and_does_not_retarget_selection(selected):
    selected.select_slide(1, 1)
    selected.adjust_selection({"brightness_ev": 1.0})
    wait_until(selected, lambda: _slides(selected)[1, 1].settings.brightness_ev == 1.0)
    selected.select_slide(2, 2)
    selected.undo()
    wait_until(selected, lambda: _slides(selected)[1, 1].settings.brightness_ev == 0.0)
    assert _slides(selected)[2, 2].settings.brightness_ev == 0.0
    assert selected._session.runner.wait_idle(5)
    assert not _slides(selected)[1, 1].preview_stale


def test_queued_edit_drops_when_session_identity_changes(selected, monkeypatch):
    started, release = threading.Event(), threading.Event()
    selected._run(lambda: (started.set(), release.wait(5)), "block")
    assert started.wait(5)
    selected.select_all()
    selected.adjust_selection({"brightness_ev": 1.0})
    selected._session = dataclasses.replace(selected._session)
    release.set()
    selected._command.submit(lambda: None).result(5)
    assert all(s.settings.brightness_ev == 0.0 for s in _slides(selected).values())


def test_reexport_uses_captured_keys_and_saved_destination(selected, tmp_path, monkeypatch):
    store = selected._session.store
    output = tmp_path / "album"
    store.update(
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, export_dir=str(output))
        )
    )
    selected.select_all()
    selected.set_selection_status(SlideStatus.ACCEPTED)
    wait_until(
        selected, lambda: all(s.status is SlideStatus.ACCEPTED for s in _slides(selected).values())
    )
    store.update_tray(3, raw_removed=True)
    selected.select_slide(1, 1)
    selected.select_slide(3, 1, control=True)
    exported = Mock()
    monkeypatch.setattr("opticfilm_linux.app.controller.run_export", exported)
    selected.reexport_selection()
    selected.clear_selection()
    wait_until(selected, lambda: exported.called)
    assert exported.call_args.args[1] == output
    assert exported.call_args.kwargs["keys"] == frozenset({(1, 1)})
    wait_until(selected, lambda: selected.state().banner is not None)
    assert "removed" in selected.state().banner.message


def test_undo_is_bounded_and_cleared_by_a_new_session(selected):
    selected.select_slide(1, 1)
    for index in range(23):
        selected.adjust_selection({"brightness_ev": (index % 3) * 0.5})
    selected._command.submit(lambda: None).result(10)
    with selected._lock:
        assert len(selected._undo) == 20
        assert selected._selection_undo_label() == "Adjust selected slides"
        selected._session = dataclasses.replace(selected._session)
        assert selected._selection_undo_label() == ""
        assert not selected._undo


def test_undo_skips_freed_raw(selected):
    selected.select_slide(1, 1)
    selected.adjust_selection({"brightness_ev": 1.0})
    selected._command.submit(lambda: None).result(5)
    session = selected._session
    session.store.update_tray(1, raw_removed=True)
    selected.undo()
    selected._command.submit(lambda: None).result(5)
    assert _slides(selected)[1, 1].settings.brightness_ev == 1.0
    wait_until(selected, lambda: selected.state().banner is not None)
    assert "removed" in selected.state().banner.message


def test_reexport_refuses_missing_destination_without_clearing_marks(selected, monkeypatch):
    selected.select_slide(1, 1)
    selected.set_selection_status(SlideStatus.ACCEPTED)
    selected._command.submit(lambda: None).result(5)
    exported = Mock()
    monkeypatch.setattr("opticfilm_linux.app.controller.run_export", exported)
    selected.reexport_selection()
    wait_until(selected, lambda: selected.state().banner is not None)
    assert "folder" in selected.state().banner.message
    assert not exported.called
