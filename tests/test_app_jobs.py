import threading
import time

import pytest

from controller_helpers import (
    make_controller,
    open_new,
    scan_when_ready,
    wait_all_in_review,
    wait_until,
)
from opticfilm_linux.batch import export as export_module
from opticfilm_linux.batch.model import SlideStatus, TrayStage
from opticfilm_linux.batch.store import BatchLocked, BatchStore

GATE_TIMEOUT = 30


@pytest.fixture
def two_trays(sweep600_dir, tmp_path):
    """A controller with two scanned, reviewed and accepted trays (8 slides)."""
    c = make_controller(sweep600_dir)
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    scan_when_ready(c)
    wait_all_in_review(c, 2)
    c.accept_all()
    wait_until(c, lambda: _all_accepted(c))
    yield c
    c.shutdown()


def _all_accepted(c):
    store = c._session.store
    return all(
        s.status in (SlideStatus.ACCEPTED, SlideStatus.BLANK)
        for t in store.snapshot().trays
        for s in t.slides
    )


def _exported_count(c):
    return sum(s.exported for t in c._session.store.snapshot().trays for s in t.slides)


def _export_all(c, out):
    c.export(out, "tiff16")
    wait_until(c, lambda: c.state().export is not None and c.state().export.finished)
    c.dismiss_export()
    wait_until(c, lambda: c.state().export is None)


def _gate_after_first_call(monkeypatch, name):
    """Make the first call to export_module.<name> block until released (after doing its work)."""
    real = getattr(export_module, name)
    reached, release = threading.Event(), threading.Event()
    calls = []

    def wrapper(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            reached.set()
            release.wait(GATE_TIMEOUT)
        return result

    monkeypatch.setattr(export_module, name, wrapper)
    return reached, release


def test_export_progress_reaches_total_and_finishes(two_trays, tmp_path):
    two_trays.export(tmp_path / "out", "tiff16")
    wait_until(two_trays, lambda: two_trays.state().export and two_trays.state().export.finished)
    view = two_trays.state().export
    files = list((tmp_path / "out").iterdir())
    assert view.done == view.total == view.written == len(files) > 0
    assert not view.cancelled and not view.running


def test_export_cancel_marks_cancelled_and_second_export_continues(
    two_trays, tmp_path, monkeypatch
):
    reached, release = _gate_after_first_call(monkeypatch, "write_slide")
    two_trays.export(tmp_path / "out", "tiff16")
    assert reached.wait(GATE_TIMEOUT)
    two_trays.cancel_export()
    release.set()
    wait_until(two_trays, lambda: two_trays.state().export and two_trays.state().export.finished)
    first = two_trays.state().export
    assert first.cancelled and first.written == 1
    two_trays.dismiss_export()
    wait_until(two_trays, lambda: two_trays.state().export is None)
    _export_all(two_trays, tmp_path / "out")
    assert len(list((tmp_path / "out").iterdir())) == _exported_count(two_trays)


def test_export_to_missing_folder_gives_banner_and_changes_no_stage(two_trays, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    two_trays.export(blocker / "sub", "tiff16")
    wait_until(two_trays, lambda: two_trays.state().banner is not None)
    assert _exported_count(two_trays) == 0
    assert {t.stage for t in two_trays.state().trays} == {TrayStage.REVIEW}
    assert two_trays.state().export is None or not two_trays.state().export.running


def test_export_empty_batch_reports_zero_written(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "b")
        c.export(tmp_path / "out", "tiff16")
        wait_until(c, lambda: c.state().export and c.state().export.finished)
        assert c.state().export.written == 0 and c.state().banner is None
    finally:
        c.shutdown()


def test_free_space_lists_only_fully_exported_trays_with_sizes(two_trays, tmp_path):
    two_trays.set_status(2, 1, SlideStatus.NEEDS_LOOK)
    wait_until(two_trays, lambda: _slide_status(two_trays, 2, 1) is SlideStatus.NEEDS_LOOK)
    _export_all(two_trays, tmp_path / "out")
    two_trays.request_free_space()
    wait_until(two_trays, lambda: two_trays.state().free_space is not None)
    view = two_trays.state().free_space
    assert [n for n, _ in view.trays] == [1] and view.total_bytes == view.trays[0][1] > 0


def _slide_status(c, tray, position):
    t = next(t for t in c._session.store.snapshot().trays if t.number == tray)
    return next(s.status for s in t.slides if s.position == position)


def test_free_space_requires_confirm_before_deleting(two_trays, tmp_path):
    _export_all(two_trays, tmp_path / "out")
    store = two_trays._session.store
    two_trays.request_free_space()
    wait_until(two_trays, lambda: two_trays.state().free_space is not None)
    assert store.sweep_dir(1, 1).is_dir() and store.sweep_dir(2, 1).is_dir()
    two_trays.confirm_free_space()
    wait_until(two_trays, lambda: two_trays.state().free_space.freed is not None)
    assert not store.sweep_dir(1, 1).exists() and not store.sweep_dir(2, 1).exists()
    assert store.preview_path(1, 1, "after").is_file()
    assert len(list((tmp_path / "out").iterdir())) > 0
    assert two_trays.state().free_space.freed > 0


def test_cancel_free_space_stops_before_next_tray(two_trays, tmp_path, monkeypatch):
    _export_all(two_trays, tmp_path / "out")
    two_trays.request_free_space()
    wait_until(two_trays, lambda: two_trays.state().free_space is not None)
    reached, release = _gate_after_first_call(monkeypatch, "cleanup_raw")
    two_trays.confirm_free_space()
    assert reached.wait(GATE_TIMEOUT)
    two_trays.cancel_free_space()
    release.set()
    wait_until(two_trays, lambda: two_trays.state().free_space.freed is not None)
    removed = [t.raw_removed for t in two_trays._session.store.snapshot().trays]
    assert removed == [True, False]


def test_readonly_refuses_export_and_free_space(sweep600_dir, tmp_path):
    BatchStore.create(tmp_path / "b", name="Test").close()
    c = make_controller(sweep600_dir)
    try:
        c.open_batch(tmp_path / "b", readonly=True)
        wait_until(c, lambda: c.state().batch_name == "Test")
        c.export(tmp_path / "out", "tiff16")
        wait_until(c, lambda: c.state().banner is not None)
        assert "read-only" in c.state().banner.message and c.state().export is None
        c.dismiss_banner()
        c.request_free_space()
        wait_until(c, lambda: c.state().banner is not None)
        assert c.state().free_space is None
    finally:
        c.shutdown()


def test_shutdown_cancels_running_export(two_trays, tmp_path, monkeypatch):
    reached, release = _gate_after_first_call(monkeypatch, "write_slide")
    two_trays.export(tmp_path / "out", "tiff16")
    assert reached.wait(GATE_TIMEOUT)
    store_dir = two_trays._session.store.directory
    closer = threading.Thread(target=two_trays.shutdown)
    closer.start()
    release.set()
    closer.join(GATE_TIMEOUT)
    assert not closer.is_alive()
    with BatchStore.open(store_dir, readonly=True) as store:
        assert sum(s.exported for t in store.snapshot().trays for s in t.slides) == 1


def test_confirm_free_space_without_a_listed_request_is_refused(two_trays, tmp_path):
    _export_all(two_trays, tmp_path / "out")
    two_trays.confirm_free_space()
    wait_until(two_trays, lambda: two_trays.state().banner is not None)
    assert [t.raw_removed for t in two_trays._session.store.snapshot().trays] == [False, False]


def test_free_space_only_removes_the_trays_that_were_listed(two_trays, tmp_path):
    two_trays.set_status(2, 1, SlideStatus.NEEDS_LOOK)
    wait_until(two_trays, lambda: _slide_status(two_trays, 2, 1) is SlideStatus.NEEDS_LOOK)
    _export_all(two_trays, tmp_path / "out")
    two_trays.request_free_space()
    wait_until(two_trays, lambda: two_trays.state().free_space is not None)
    assert [n for n, _ in two_trays.state().free_space.trays] == [1]
    two_trays.set_status(2, 1, SlideStatus.ACCEPTED)  # tray 2 becomes exportable after the list
    wait_until(two_trays, lambda: _slide_status(two_trays, 2, 1) is SlideStatus.ACCEPTED)
    _export_all(two_trays, tmp_path / "out")
    two_trays.confirm_free_space()
    wait_until(two_trays, lambda: two_trays.state().free_space.freed is not None)
    assert [t.raw_removed for t in two_trays._session.store.snapshot().trays] == [True, False]


def test_second_confirm_does_not_overwrite_the_result(two_trays, tmp_path):
    _export_all(two_trays, tmp_path / "out")
    two_trays.request_free_space()
    wait_until(two_trays, lambda: two_trays.state().free_space is not None)
    two_trays.confirm_free_space()
    two_trays.confirm_free_space()
    wait_until(two_trays, lambda: two_trays.state().free_space.freed is not None)
    time.sleep(0.3)
    two_trays.tick()
    assert two_trays.state().free_space.freed > 0


def test_close_batch_waits_for_a_running_export_before_releasing_the_lock(
    two_trays, tmp_path, monkeypatch
):
    reached, release = _gate_after_first_call(monkeypatch, "write_slide")
    directory = two_trays._session.store.directory
    two_trays.export(tmp_path / "out", "tiff16")
    assert reached.wait(GATE_TIMEOUT)
    two_trays.close_batch()
    time.sleep(0.3)
    with pytest.raises(BatchLocked):
        BatchStore.open(directory)  # still held: the export is still writing the manifest
    release.set()
    wait_until(two_trays, lambda: two_trays.state().batch_name is None)
    with BatchStore.open(directory) as reopened:
        assert sum(s.exported for t in reopened.snapshot().trays for s in t.slides) == 1


def test_close_and_shutdown_together_keep_the_lock_until_the_job_ends(
    two_trays, tmp_path, monkeypatch
):
    reached, release = _gate_after_first_call(monkeypatch, "write_slide")
    directory = two_trays._session.store.directory
    two_trays.export(tmp_path / "out", "tiff16")
    assert reached.wait(GATE_TIMEOUT)
    two_trays.close_batch()
    time.sleep(0.3)
    closer = threading.Thread(target=two_trays.shutdown)
    closer.start()
    time.sleep(0.3)
    with pytest.raises(BatchLocked):
        BatchStore.open(directory)  # the export is still writing through the old store
    release.set()
    closer.join(GATE_TIMEOUT)
    assert not closer.is_alive()
    with BatchStore.open(directory) as reopened:
        assert sum(s.exported for t in reopened.snapshot().trays for s in t.slides) == 1


def test_edits_queued_before_shutdown_are_still_saved(two_trays):
    gate = threading.Event()
    directory = two_trays._session.store.directory
    two_trays._command.submit(lambda: gate.wait(30))  # keep the command worker busy
    two_trays.adjust_slide(1, 1, {"brightness_ev": 1.5})
    closer = threading.Thread(target=two_trays.shutdown)
    closer.start()
    time.sleep(0.3)
    gate.set()
    closer.join(GATE_TIMEOUT)
    assert not closer.is_alive()
    with BatchStore.open(directory, readonly=True) as store:
        assert store.snapshot().trays[0].slides[0].settings.brightness_ev == 1.5


def test_an_export_queued_for_a_closed_batch_never_touches_the_next_one(sweep600_dir, tmp_path):
    from controller_helpers import make_controller as make

    c = make(sweep600_dir)
    try:
        open_new(c, tmp_path / "a")
        scan_when_ready(c)
        wait_all_in_review(c, 1)
        c.accept_all()
        gate = threading.Event()
        c._jobs.submit(lambda: gate.wait(30))  # the jobs worker is busy, so the export queues
        c.export(tmp_path / "out", "tiff16")
        time.sleep(0.3)
        c.close_batch()
        threading.Timer(0.5, gate.set).start()
        time.sleep(0.2)
        BatchStore.create(tmp_path / "b", name="B").close()
        c.open_batch(tmp_path / "b")
        wait_until(c, lambda: c.state().batch_name == "B")
        time.sleep(0.5)
        c.tick()
        assert not (tmp_path / "out").exists() or list((tmp_path / "out").iterdir()) == []
    finally:
        c.shutdown()
