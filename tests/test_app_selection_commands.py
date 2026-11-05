"""Controller selection uses cached order and delegates atomic edits to its runner."""

import dataclasses
import threading
from unittest.mock import Mock

import pytest

from controller_helpers import make_controller, wait_until
from opticfilm_linux.batch.metadata import MetaOverride
from opticfilm_linux.batch.model import (
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchStore
from test_batch_runner_bulk import _render


@pytest.fixture
def selected(sweep600_dir, tmp_path, monkeypatch):
    directory = tmp_path / "batch"
    with BatchStore.create(directory, name="selection") as store:
        trays = tuple(
            TrayRecord(number, TrayStage.REVIEW, slides=(SlideRecord(1), SlideRecord(2)))
            for number in (1, 2, 3)
        )
        store.update(lambda manifest: dataclasses.replace(manifest, trays=trays))
    controller = make_controller(sweep600_dir)
    monkeypatch.setattr("opticfilm_linux.batch.runner.reenhance_slide", _render)
    controller.open_batch(directory)
    wait_until(controller, lambda: len(controller.state().trays) == 3)
    yield controller
    controller.shutdown()


def _slides(controller):
    return {
        (tray.number, slide.position): slide
        for tray in controller._session.store.snapshot().trays
        for slide in tray.slides
    }


def test_gestures_use_cached_order_and_keep_selection_on_tray_change(selected, monkeypatch):
    store = selected._session.store
    original = store.snapshot
    main_thread = threading.get_ident()

    def snapshot():
        assert threading.get_ident() != main_thread
        assert selected._lock.acquire(blocking=False)
        selected._lock.release()
        return original()

    monkeypatch.setattr(store, "snapshot", snapshot)
    selected.select_slide(1, 1)
    selected.select_slide(2, 2, shift=True)
    assert selected._selection_order == ((3, 1), (3, 2), (2, 1), (2, 2), (1, 1), (1, 2))
    assert selected._selection.keys == {(1, 1), (2, 2)}
    selected.select_slide(3, 2, control=True)
    selected.select_tray(3)
    wait_until(selected, lambda: selected.state().selected == 3)
    assert selected.state().selection == {(1, 1), (2, 2), (3, 2)}
    selected.select_slide(1, 2, control=True, shift=True)
    assert selected._selection.keys == set(selected._selection_order) - {(3, 1)}
    selected.clear_selection()
    wait_until(selected, lambda: not selected.state().selection)


def test_select_all_prunes_busy_trays_and_clears_on_close(selected):
    selected.select_all()
    selected._session.store.update_tray(2, stage=TrayStage.ENHANCING)
    wait_until(selected, lambda: (2, 1) not in selected.state().selection)
    selected.select_slide(2, 1)
    assert (2, 1) not in selected._selection.keys
    selected.select_all()
    assert selected._selection.keys == {(1, 1), (1, 2), (3, 1), (3, 2)}
    selected.close_batch()
    wait_until(selected, lambda: selected.state().batch_name is None)
    assert not selected.state().selection and not selected._selection.keys


def test_select_all_synchronizes_selected_tray_with_new_focus(selected):
    selected.select_slide(2, 1)
    assert selected.state().selected == 2
    selected.select_all()
    state = selected.state()
    assert state.focus == (3, 1)
    assert state.selected == 3
    assert state.selection == set(selected._selection_order)
    selected._rebuild()
    assert selected.state().selected == selected.state().focus[0] == 3


def test_bulk_status_uses_runner_and_schedules_automatic_work(selected, monkeypatch):
    selected.select_slide(1, 1)
    selected.select_slide(2, 2, control=True)
    submit = Mock()
    monkeypatch.setattr(selected, "_submit_auto_due", submit)
    selected.set_selection_status(SlideStatus.ACCEPTED)
    selected.clear_selection()  # action targets the keys captured when it was requested
    wait_until(selected, lambda: _slides(selected)[2, 2].status is SlideStatus.ACCEPTED)
    assert _slides(selected)[1, 1].owner_decided
    assert _slides(selected)[1, 2].status is SlideStatus.PENDING
    assert {1, 2} <= selected._auto_due and submit.called


def test_bulk_settings_preserve_owner_status_skip_freed_and_report_summary(selected):
    selected._session.store.update_tray(3, raw_removed=True)
    selected.select_all()
    selected.set_selection_status(SlideStatus.NEEDS_LOOK)
    selected.adjust_selection({"brightness_ev": 0.5})
    selected.rotate_selection(1)
    selected.mirror_selection()
    wait_until(selected, lambda: _slides(selected)[2, 2].settings.mirror)
    slides = _slides(selected)
    assert slides[1, 1].settings == SlideSettings(rotation=90, mirror=True, brightness_ev=0.5)
    assert slides[1, 1].status is SlideStatus.NEEDS_LOOK and slides[1, 1].owner_decided
    assert slides[3, 1].settings == SlideSettings() and not slides[3, 1].owner_decided
    wait_until(
        selected,
        lambda: dict(selected.state().selection_summary.settings).get("brightness_ev", 0.0) is None,
    )
    assert selected.state().selection_summary.count == 6
    assert "removed" in selected.state().banner.message
    selected.reset_selection()
    wait_until(selected, lambda: _slides(selected)[1, 1].settings == SlideSettings())


def test_blank_status_is_skipped_and_busy_change_is_rechecked(selected):
    store = selected._session.store
    store.update_tray(1, slides=(SlideRecord(1, SlideStatus.BLANK), SlideRecord(2)))
    selected.select_all()
    store.update_tray(2, stage=TrayStage.SCANNING)
    selected.set_selection_status(SlideStatus.ACCEPTED)
    wait_until(selected, lambda: _slides(selected)[3, 2].status is SlideStatus.ACCEPTED)
    assert _slides(selected)[1, 1].status is SlideStatus.BLANK
    assert _slides(selected)[2, 1].status is SlideStatus.PENDING
    wait_until(selected, lambda: selected.state().banner is not None)
    assert "blank" in selected.state().banner.message


def test_readonly_selection_can_be_changed_but_not_edited(selected, monkeypatch):
    session = selected._session
    monkeypatch.setattr(selected, "_session", dataclasses.replace(session, runner=None))
    selected.select_all()
    selected.adjust_selection({"brightness_ev": 0.5})
    wait_until(selected, lambda: selected.state().banner is not None)
    assert "read-only" in selected.state().banner.message
    assert _slides(selected)[1, 1].settings == SlideSettings()


def test_gestures_publish_the_state_immediately_without_waiting_for_a_tick(selected):
    selected.tick()
    wait_until(selected, lambda: selected.state().selected == 1)
    selected.select_slide(2, 2)
    state = selected.state()  # no tick and no wait: the gesture itself republished
    assert state.selection == {(2, 2)} and state.focus == (2, 2) and state.selected == 2
    assert state.selection_summary.count == 1
    selected.select_all()
    assert len(selected.state().selection) == 6
    selected.move_focus(1)
    assert selected.state().focus == selected._selection.focus
    selected.select_tray_slides(3)
    state = selected.state()
    assert state.selection == {(3, 1), (3, 2)} and state.selected == 3
    selected.clear_selection()
    assert not selected.state().selection and selected.state().selection_summary.count == 0


def test_select_tray_without_selectable_slides_selects_tray_only(selected):
    selected.select_slide(1, 1)
    selected._session.store.update_tray(2, stage=TrayStage.ENHANCING)
    wait_until(selected, lambda: (2, 1) not in selected._selection_order)
    selected.select_tray_slides(2)
    selected.select_tray_slides(9)
    assert selected._selected == 2 and not selected._selection.keys
    assert selected.state().selection_summary.trays == (2,)
    selected._rebuild()
    assert selected.state().selection_summary.trays == (2,)


def test_rotate_and_mirror_are_one_atomic_store_update(selected, monkeypatch):
    selected.select_all()
    store = selected._session.store

    def no_read(*_):
        raise AssertionError("must not read a snapshot outside the edit")

    monkeypatch.setattr(selected, "_open_store", no_read)
    selected.rotate_selection(1)
    selected.mirror_selection()
    wait_until(selected, lambda: _slides(selected)[3, 2].settings.mirror)
    assert _slides(selected)[1, 1].settings == SlideSettings(rotation=90, mirror=True)
    assert store.snapshot() is not None


def test_callable_edit_skips_missing_slides_instead_of_failing(sweep600_dir, tmp_path):
    from opticfilm_linux.batch import review

    with BatchStore.create(tmp_path / "b", name="callable") as store:
        store.update(
            lambda m: dataclasses.replace(
                m, trays=(TrayRecord(1, TrayStage.REVIEW, slides=(SlideRecord(1),)),)
            )
        )
        result = review.apply_settings_many(
            store, [(1, 1), (9, 9)], lambda s: dataclasses.replace(s, rotation=90)
        )
        assert result.skipped == {(9, 9): "missing"}
        assert store.snapshot().trays[0].slides[0].settings.rotation == 90


def test_empty_selection_starts_no_job_and_touches_no_store(selected, monkeypatch):
    writer = Mock()
    monkeypatch.setattr(selected, "_run_writer", writer)
    for command in (
        lambda: selected.set_selection_status(SlideStatus.ACCEPTED),
        lambda: selected.adjust_selection({"brightness_ev": 0.5}),
        selected.reset_selection,
        lambda: selected.rotate_selection(1),
        selected.mirror_selection,
    ):
        command()
    writer.assert_not_called()
    assert selected.state().banner is None


def _metas(controller):
    return {key: slide.meta for key, slide in _slides(controller).items()}


def test_select_all_meta_edit_changes_every_slide_even_on_a_freed_tray(selected):
    selected._session.store.update_tray(3, raw_removed=True)
    selected.select_all()
    selected.set_selection_meta("description", "Lake trip")
    wait_until(
        selected, lambda: all(m.description == "Lake trip" for m in _metas(selected).values())
    )
    wait_until(
        selected,
        lambda: dict(selected.state().selection_summary.meta)["description"] == "Lake trip",
    )
    assert dict(selected.state().selection_summary.meta_levels)["description"] == "slide"
    assert selected.state().banner is None


def test_shift_range_meta_edit_then_undo_restores_exactly(selected):
    selected.select_slide(1, 1)
    selected.set_selection_meta("date", "1978")
    selected.select_slide(2, 1)
    selected.select_slide(1, 2, shift=True)
    selected.set_selection_meta("date", "")
    wait_until(selected, lambda: _metas(selected)[2, 1].date == "")
    selected.undo()
    wait_until(selected, lambda: _metas(selected)[2, 1].date is None)
    metas = _metas(selected)
    assert metas[1, 1] == MetaOverride(date="1978") and metas[1, 2] == MetaOverride()
    selected.reset_selection_meta("date")  # selection is (2,1)-(1,2); None means inherit
    wait_until(selected, lambda: selected._undo and selected._undo[-1].label == "Edit metadata")


def test_batch_meta_from_now_on_and_its_undo(selected):
    store = selected._session.store
    store.update(lambda m: dataclasses.replace(m, description="Old"))
    selected.set_tray_meta(2, "description", "Tray two")
    selected.set_batch_meta("description", "New")  # default scope: from now on
    wait_until(selected, lambda: store.snapshot().description == "New")
    trays = {t.number: t.meta for t in store.snapshot().trays}
    assert trays == {1: MetaOverride("Old"), 2: MetaOverride("Tray two"), 3: MetaOverride("Old")}
    selected.undo()
    wait_until(selected, lambda: store.snapshot().description == "Old")
    assert {t.number: t.meta.description for t in store.snapshot().trays} == {
        1: None,
        2: "Tray two",
        3: None,
    }
    selected.set_batch_meta("date", "1980", scope="inheriting")
    wait_until(selected, lambda: store.snapshot().date == "1980")
    assert store.snapshot().trays[0].meta == MetaOverride()


def test_interleaved_rotate_and_meta_edits_undo_in_order(selected):
    selected.select_all()
    selected.rotate_selection(1)
    selected.set_selection_meta("description", "A")
    selected.rotate_selection(1)
    wait_until(selected, lambda: _slides(selected)[1, 1].settings.rotation == 180)
    selected.undo()
    wait_until(selected, lambda: _slides(selected)[1, 1].settings.rotation == 90)
    assert _metas(selected)[1, 1].description == "A"
    selected.undo()
    wait_until(selected, lambda: _metas(selected)[1, 1].description is None)
    assert _slides(selected)[1, 1].settings.rotation == 90
    selected.undo()
    wait_until(selected, lambda: _slides(selected)[1, 1].settings.rotation == 0)


def test_meta_edits_stay_off_the_render_path(selected, monkeypatch):
    runner = selected._session.runner
    for name in ("_hold_turns", "_queue_stale_previews", "_submit", "_emit"):
        monkeypatch.setattr(runner, name, Mock(side_effect=AssertionError(name)))
    invalidate = Mock()
    monkeypatch.setattr(selected._session.previews, "invalidate", invalidate)
    monkeypatch.setattr(selected, "_submit_auto_due", Mock())  # the tick would drain it
    selected._auto_due = set()
    before = selected._session.store.snapshot()
    selected.select_all()
    selected.set_selection_meta("description", "x")
    selected.set_tray_meta(1, "date", "1990")
    selected.set_batch_meta("description", "b")
    selected.undo()
    wait_until(selected, lambda: selected._session.store.snapshot().description == "")
    after = selected._session.store.snapshot()
    assert [s.settings for t in after.trays for s in t.slides] == [
        s.settings for t in before.trays for s in t.slides
    ]
    assert selected.state().banner is None
    assert not invalidate.called and not selected._auto_due


def test_invalid_meta_is_refused_at_once_and_starts_no_job(selected, monkeypatch):
    writer = Mock()
    monkeypatch.setattr(selected, "_run_writer", writer)
    selected.select_all()
    for command in (
        lambda: selected.set_selection_meta("date", "1850"),
        lambda: selected.set_tray_meta(1, "description", "x" * 501),
        lambda: selected.set_batch_meta("date", "July"),
        lambda: selected.set_batch_meta("date", "1980", scope="later"),
        lambda: selected.set_selection_meta("rotation", "90"),
    ):
        with pytest.raises(ValueError):
            command()
    writer.assert_not_called()


def test_meta_commands_refuse_what_cannot_start_before_queueing(selected, monkeypatch):
    writer = Mock()
    monkeypatch.setattr(selected, "_run_writer", writer)
    selected.clear_selection()
    with pytest.raises(ValueError, match="Select at least one slide"):
        selected.set_selection_meta("description", "nobody")
    selected._session.store.update_tray(2, stage=TrayStage.ENHANCING)
    wait_until(selected, lambda: (2, 1) not in selected._selection_order)
    selected.select_tray_slides(2)  # a tray with nothing selectable: no slides selected
    with pytest.raises(ValueError, match="Select at least one slide"):
        selected.reset_selection_meta("date")
    with pytest.raises(ValueError, match="no tray 9"):
        selected.set_tray_meta(9, "date", "1980")
    session = selected._session
    monkeypatch.setattr(selected, "_session", dataclasses.replace(session, runner=None))
    with pytest.raises(ValueError, match="read-only"):
        selected.set_batch_meta("description", "x")
    writer.assert_not_called()


def test_meta_edit_requeues_verified_trays_and_no_op_leaves_no_undo(selected, monkeypatch):
    store = selected._session.store
    store.update_tray(1, auto="verified")
    submit = Mock()
    monkeypatch.setattr(selected, "_submit_auto_due", submit)
    selected._auto_due = set()
    selected.set_tray_meta(1, "date", "1980")
    wait_until(selected, lambda: selected._auto_due == {1})
    assert submit.called
    depth = len(selected._undo)
    selected.set_tray_meta(1, "date", "1980")  # the same value again: nothing to undo
    selected.set_batch_meta("description", "")  # already blank
    selected.set_tray_meta(2, "date", "1981")
    wait_until(selected, lambda: store.snapshot().trays[1].meta.date == "1981")
    assert len(selected._undo) == depth + 1


def test_slides_meta_edits_the_given_keys_not_the_live_selection(selected):
    selected.select_slide(1, 1)
    selected.set_slides_meta([(1, 2), (2, 1)], "description", "Lake")
    wait_until(selected, lambda: _metas(selected)[2, 1].description == "Lake")
    metas = _metas(selected)
    assert metas[1, 2].description == "Lake" and metas[1, 1] == MetaOverride()
    selected.set_slides_meta([(1, 2)], "description", None)
    wait_until(selected, lambda: _metas(selected)[1, 2] == MetaOverride())
    with pytest.raises(ValueError, match="no longer"):
        selected.set_slides_meta([(1, 2), (99, 1)], "description", "x")
    with pytest.raises(ValueError):
        selected.set_slides_meta([], "description", "x")
    with pytest.raises(ValueError):
        selected.set_slides_meta([(1, 2)], "date", "1850")
