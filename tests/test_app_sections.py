import threading
from dataclasses import replace

import pytest

from controller_helpers import make_controller, wait_all_in_review, wait_until
from opticfilm_linux.batch.model import (
    SlideRecord,
    SlideStatus,
    TrayLocation,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.scanner.fake import FakeScanner


def _new(c, path, **kwargs):
    c.new_batch(path, "Family", "", None, 600, collection="family", **kwargs)
    wait_until(c, lambda: c.state().batch_name == "Family" and c.state().collection == "family")


def test_finish_section_allocates_fresh_tray_after_reopen(tmp_path, sweep600_dir):
    c = make_controller(sweep600_dir)
    try:
        _new(c, tmp_path / "b", section_size=10)
        wait_until(c, lambda: c.state().can_scan)
        c.scan()
        wait_all_in_review(c, 1)
        before = c._session.store.snapshot().trays[0].location
        wait_until(c, lambda: c.state().can_finish_box)
        c.finish_box(False)
        wait_until(c, lambda: c.state().section == "b")
        assert c.state().selected is None
        c.close_batch()
        wait_until(c, lambda: c.state().batch_name is None)
        c.open_batch(tmp_path / "b")
        wait_until(c, lambda: c.state().can_scan)
        assert c._session.store.snapshot().trays[0].location == before
        c.rescan(1)
        wait_until(c, lambda: c.state().banner is not None)
        assert "closed section" in c.state().banner.message
        assert c._session.store.snapshot().trays[0].sweep_version == 1
        c.scan()
        wait_all_in_review(c, 2)
        assert c._session.store.snapshot().trays[1].location == TrayLocation("family", 1, "b", 1, 4)
        wait_until(c, lambda: c.state().can_finish_box)
        c.finish_box()
        wait_until(c, lambda: c.state().box == 2)
        assert c.state().section == "a"
    finally:
        c.shutdown()


@pytest.mark.parametrize("busy", ["scan", "move", "job", "enhancement"])
def test_finish_refused_during_scan_move_or_job(tmp_path, sweep600_dir, busy):
    c = make_controller(sweep600_dir)
    gate = threading.Event()
    try:
        _new(c, tmp_path / "b")
        wait_until(c, lambda: c.state().can_scan)
        before = c._session.store.snapshot()
        if busy == "scan":
            c._set(_scans_queued=1)
        elif busy == "move":
            c._set(_holder_busy=True)
        elif busy == "job":
            c._run(lambda: gate.wait(20), "stand-in", pool=c._jobs)
        else:
            c._set(_updating=frozenset({(1, 1)}))
        c.finish_box(False)
        wait_until(c, lambda: c.state().banner is not None)
        assert c._session.store.snapshot() == before
        assert not c.state().can_finish_box
    finally:
        gate.set()
        c._set(_scans_queued=0, _holder_busy=False, _updating=frozenset())
        c.shutdown()


def test_finish_readonly_and_closed_runner_rescan_refused(tmp_path, sweep600_dir):
    store = BatchStore.create(tmp_path / "b", "Family", collection="family", section="b")
    tray = TrayRecord(1, TrayStage.REVIEW, location=TrayLocation("family", 1, "a", 1, 4))
    store.update(lambda m: replace(m, trays=(tray,)))
    store.close()
    store = BatchStore.open(tmp_path / "b")
    runner = BatchRunner(store, FakeScanner(sweep600_dir))
    with pytest.raises(BatchError, match="closed section"):
        runner.rescan_tray(1)
    runner.close()
    store.close()
    c = make_controller(sweep600_dir)
    try:
        c.open_batch(tmp_path / "b", readonly=True)
        wait_until(c, lambda: c.state().readonly)
        before = c._session.store.snapshot()
        c.finish_box()
        wait_until(c, lambda: c.state().banner is not None)
        assert c._session.store.snapshot() == before
        assert not c.state().can_finish_box
    finally:
        c.shutdown()


def test_create_replaces_batch_with_atomic_location_settings(tmp_path, sweep600_dir):
    c = make_controller(sweep600_dir)
    try:
        _new(c, tmp_path / "a")
        c.new_batch(
            tmp_path / "b",
            "Second",
            "",
            None,
            600,
            collection="second",
            box=3,
            section="02",
            section_size=10,
        )
        wait_until(c, lambda: c.state().batch_name == "Second")
        assert (c.state().collection, c.state().box, c.state().section, c.state().section_size) == (
            "second",
            3,
            "02",
            10,
        )
        with BatchStore.open(tmp_path / "a", readonly=True) as old:
            assert old.snapshot().collection == "family"
    finally:
        c.shutdown()


def test_queued_transition_does_not_retarget_new_batch(tmp_path, sweep600_dir):
    c = make_controller(sweep600_dir)
    gate, started = threading.Event(), threading.Event()
    try:
        _new(c, tmp_path / "a")
        c._command.submit(lambda: (started.set(), gate.wait(20)))
        assert started.wait(10)
        c.new_batch(tmp_path / "b", "Second", "", None, 600, collection="second")
        c.finish_box(False)  # belongs to the original session, queued after its replacement
        gate.set()
        wait_until(c, lambda: c.state().batch_name == "Second")
        c._command.submit(lambda: None).result(10)
        c.tick()
        assert (c.state().collection, c.state().box, c.state().section) == ("second", 1, "a")
        with BatchStore.open(tmp_path / "a", readonly=True) as old:
            assert old.snapshot().section == "a"
    finally:
        gate.set()
        c.shutdown()


@pytest.mark.parametrize("source", ["orientation", "reopened_stale"])
def test_finish_refused_while_reenhancement_runs(tmp_path, sweep600_dir, monkeypatch, source):
    entered, release = threading.Event(), threading.Event()

    def blocked_preview(store, number, position):
        entered.set()
        assert release.wait(20)
        tray = store.snapshot().trays[0]
        store.update_tray(
            number, slides=tuple(replace(s, preview_stale=False) for s in tray.slides)
        )

    monkeypatch.setattr("opticfilm_linux.batch.runner.reenhance_slide", blocked_preview)
    path = tmp_path / "b"
    with BatchStore.create(path, "Family", collection="family") as store:
        slide = SlideRecord(1, SlideStatus.ACCEPTED, preview_stale=source == "reopened_stale")
        tray = TrayRecord(
            1, TrayStage.REVIEW, slides=(slide,), location=TrayLocation("family", 1, "a", 1, 4)
        )
        store.update(lambda m: replace(m, trays=(tray,)))
    c = make_controller(sweep600_dir)
    try:
        c.open_batch(path)
        wait_until(c, lambda: c.state().batch_name == "Family")
        if source == "orientation":
            c.set_batch_orientation(90, False)
        assert entered.wait(10)
        c._command.submit(lambda: None).result(10)  # Future is registered before reading state
        c._rebuild()
        before = c._session.store.snapshot()
        assert not c.state().can_finish_box
        c.finish_box(False)
        wait_until(c, lambda: c.state().banner is not None)
        assert c._session.store.snapshot() == before
        release.set()
        wait_until(c, lambda: c.state().can_finish_box)
        c.finish_box(False)
        wait_until(c, lambda: c.state().section == "b")
    finally:
        release.set()
        c.shutdown()


@pytest.mark.parametrize("next_box", [False, True])
def test_transition_write_keeps_ui_responsive(tmp_path, sweep600_dir, monkeypatch, next_box):
    c = make_controller(sweep600_dir)
    entered, release, snapshot_started, responsive = (threading.Event() for _ in range(4))
    reader = None
    try:
        path = tmp_path / "b"
        _new(c, path)
        c._refresher.submit(lambda: None).result(10)
        store = c._session.store
        write, snapshot = store._write, store.snapshot

        def blocked_write(manifest):
            entered.set()
            assert release.wait(20)
            write(manifest)

        def blocked_snapshot():
            snapshot_started.set()
            return snapshot()

        def read_ui():
            c.tick()
            if snapshot_started.wait(5) and c.state().batch_name == "Family":
                responsive.set()

        monkeypatch.setattr(store, "_write", blocked_write)
        c.finish_box(next_box)
        assert entered.wait(10)
        monkeypatch.setattr(store, "snapshot", blocked_snapshot)
        reader = threading.Thread(target=read_ui, daemon=True)
        reader.start()
        assert responsive.wait(2), "manifest persistence blocked the UI controller lock"
        c.eject_holder()
        assert not c._holder_busy
        assert "Wait for the scan" in c._banner.message
        c.close_batch()
        release.set()
        wait_until(c, lambda: c.state().batch_name is None)
        with BatchStore.open(path, readonly=True) as saved:
            manifest = saved.snapshot()
            assert (manifest.box, manifest.section) == ((2, "a") if next_box else (1, "b"))
        assert c._scans_queued == 0
    finally:
        release.set()
        if reader is not None:
            reader.join(10)
        c.shutdown()


def test_transition_write_failure_releases_reservation(tmp_path, sweep600_dir, monkeypatch):
    c = make_controller(sweep600_dir)
    try:
        _new(c, tmp_path / "b")
        store = c._session.store
        write = store._write

        def failed_write(_manifest):
            raise OSError("disk full")

        monkeypatch.setattr(store, "_write", failed_write)
        c.finish_box(False)
        wait_until(c, lambda: c.state().banner is not None)
        assert "disk full" in c.state().banner.message
        assert c._scans_queued == 0
        assert store.snapshot().section == "a"
        monkeypatch.setattr(store, "_write", write)
        c.finish_box(False)
        wait_until(c, lambda: c.state().section == "b")
        assert c._scans_queued == 0
    finally:
        c.shutdown()
