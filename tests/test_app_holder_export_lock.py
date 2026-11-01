"""Holder moves overlap background jobs; batch changes still wait for holder moves."""

import threading

import numpy as np
import pytest
import tifffile

from controller_helpers import open_new, scan_when_ready, wait_all_in_review, wait_until
from opticfilm_linux.batch import export as export_module
from opticfilm_linux.batch.model import SlideStatus
from opticfilm_linux.scanner import holder_latch
from opticfilm_linux.scanner.base import ScannerStatus
from test_app_controller_holder import build  # noqa: F401 - the fixture
from test_app_state import _build

WAIT_HOLDER = "Wait for the eject/load to finish"


@pytest.mark.parametrize("jobs_running", [False, True])
@pytest.mark.parametrize("scanner", [ScannerStatus.READY, ScannerStatus.INTERRUPTED])
def test_holder_buttons_remain_available_while_a_job_runs(scanner, jobs_running):
    state = _build(scanner=scanner, holder_control=True, next_strip=True, jobs_running=jobs_running)
    assert state.can_eject is (scanner is ScannerStatus.READY)
    assert state.can_load_next is False  # READY is not NOT_READY
    assert state.can_confirm_scanner is (scanner is ScannerStatus.INTERRUPTED and not jobs_running)


def test_load_next_remains_available_while_a_job_runs():
    kwargs = {"scanner": ScannerStatus.NOT_READY, "holder_control": True, "next_strip": True}
    assert _build(**kwargs).can_load_next
    assert _build(**kwargs, jobs_running=True).can_load_next


def test_controls_remain_available_during_a_running_job(build, tmp_path):  # noqa: F811
    c, scanner = build(next_strip=True)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject and c.state().can_load_next)
    gate = threading.Event()
    c._run(lambda: gate.wait(30), "export stand-in", pool=c._jobs)
    try:
        assert c._job_futures
        c._rebuild()
        wait_until(c, lambda: c.state().can_eject and c.state().can_load_next, timeout=3)
    finally:
        gate.set()
    wait_until(c, lambda: c.state().can_eject and c.state().can_load_next)
    assert scanner.calls == []


def test_eject_button_press_ejects_while_a_job_runs(build, tmp_path):  # noqa: F811
    c, scanner = build()
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    gate = threading.Event()
    c._run(lambda: gate.wait(30), "export stand-in", pool=c._jobs)
    try:
        assert c._job_futures
        c._rebuild()
        session = c._session
        session.poller.take_eject_press = lambda: True
        c._act_on_eject_button(session.poller)
        session.poller.take_eject_press = lambda: False
        wait_until(c, lambda: scanner.calls == ["eject"], timeout=3)
        assert c.state().banner is None
    finally:
        gate.set()


@pytest.mark.parametrize("action", ["close", "open", "new"])
def test_batch_change_is_refused_while_a_holder_move_runs(build, tmp_path, action):  # noqa: F811
    gate = threading.Event()
    c, scanner = build(gate=gate)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    c.eject_holder()
    wait_until(c, lambda: scanner.calls == ["eject"])
    change = {
        "close": lambda: c.close_batch(),
        "open": lambda: c.open_batch(tmp_path / "b"),
        "new": lambda: c.new_batch(tmp_path / "n", "n", "", None, 600),
    }[action]
    change()
    wait_until(c, lambda: c.state().banner is not None)
    assert c.state().banner.message == WAIT_HOLDER
    assert c.state().batch_name is not None  # still the same open batch
    gate.set()
    wait_until(c, lambda: not c._holder_busy)
    c.dismiss_banner()
    c.close_batch()
    wait_until(c, lambda: c.state().batch_name is None)
    assert scanner.calls == ["eject"]


def test_close_cancels_a_holder_move_that_has_not_started(build, tmp_path):  # noqa: F811
    c, scanner = build()
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    hold = threading.Event()
    started = threading.Event()
    c._holder_pool.submit(lambda: (started.set(), hold.wait(30)))  # a first move in progress
    assert started.wait(10)
    c.eject_holder()  # queued behind it, not started
    assert c._holder_busy
    c.close_batch()
    wait_until(c, lambda: c.state().batch_name is None)
    assert not c._holder_busy
    hold.set()
    c._holder_pool.submit(lambda: None).result(10)  # anything queued has had its turn
    assert scanner.calls == [] and c.state().banner is None


def _prepare_exported_batch(build_controller, tmp_path, holder_release):
    c, scanner = build_controller(status=ScannerStatus.READY, gate=holder_release)
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    wait_all_in_review(c, 1)
    c.accept_all()
    wait_until(c, lambda: all(s.status is SlideStatus.ACCEPTED for s in c.state().slides))
    baseline = tmp_path / "baseline"
    c.export(baseline, "tiff16")
    wait_until(c, lambda: c.state().export is not None and c.state().export.finished)
    expected = {p.name: tifffile.imread(p) for p in baseline.iterdir()}
    assert expected
    scanner._statuses = (ScannerStatus.NOT_READY,)
    wait_until(c, lambda: c.state().can_load_next)
    return c, scanner, expected


def _assert_export_matches(c, out, expected):
    wait_until(c, lambda: c.state().export is not None and c.state().export.finished)
    result = c.state().export
    assert result.done == result.total == result.written == len(expected)
    assert not result.cancelled and c.state().banner is None
    assert {p.name for p in out.iterdir()} == expected.keys()
    for path in out.iterdir():
        np.testing.assert_array_equal(tifffile.imread(path), expected[path.name])
    assert all(s.exported for s in c._session.store.snapshot().trays[0].slides)


@pytest.mark.parametrize(
    "action, call", [("eject_holder", "eject"), ("load_next_holder", "load_next_strip")]
)
def test_export_result_is_unchanged_by_concurrent_holder_move(
    build,  # noqa: F811
    tmp_path,
    monkeypatch,
    action,
    call,
):
    holder_release = threading.Event()
    c, scanner, expected = _prepare_exported_batch(build, tmp_path, holder_release)
    c.reexport_all_accepted()
    wait_until(c, lambda: c.state().export is None)
    reached, read_raw, raw_loaded, release = (threading.Event() for _ in range(4))
    load_sweep = export_module.load_sweep

    def blocked_load(*args, **kwargs):
        reached.set()
        assert read_raw.wait(10)
        result = load_sweep(*args, **kwargs)
        raw_loaded.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(export_module, "load_sweep", blocked_load)
    out = tmp_path / "concurrent"
    c.export(out, "tiff16")
    try:
        assert reached.wait(10)
        assert not out.exists() or not tuple(out.iterdir())
        getattr(c, action)()
        wait_until(c, lambda: scanner.calls == [call] and c._holder_busy, timeout=3)
        read_raw.set()
        assert raw_loaded.wait(10)  # actual raw I/O overlaps the in-progress holder move
        assert c._holder_busy
        holder_release.set()
        wait_until(c, lambda: not c._holder_busy, timeout=3)
        assert c._job_futures and not c._cancel_export.is_set()
    finally:
        read_raw.set()
        holder_release.set()
        release.set()
    _assert_export_matches(c, out, expected)


def test_holder_latch_confirmation_stays_disabled_during_a_job():
    assert not _build(
        scanner=ScannerStatus.NOT_READY,
        holder_control=True,
        holder_latch=holder_latch.HolderLatch.RELEASED,
        jobs_running=True,
    ).can_confirm_holder_latched


@pytest.mark.parametrize(
    "action, call", [("eject_holder", "eject"), ("load_next_holder", "load_next_strip")]
)
def test_holder_move_during_raw_cleanup(build, tmp_path, monkeypatch, action, call):  # noqa: F811
    holder_release = threading.Event()
    c, scanner = build(status=ScannerStatus.READY, gate=holder_release)
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    wait_all_in_review(c, 1)
    c.accept_all()
    wait_until(c, lambda: all(s.status is SlideStatus.ACCEPTED for s in c.state().slides))
    c.export(tmp_path / "export", "tiff16")
    wait_until(c, lambda: c.state().export is not None and c.state().export.finished)
    scanner._statuses = (ScannerStatus.NOT_READY,)
    wait_until(c, lambda: c.state().can_load_next)
    c.request_free_space()
    wait_until(c, lambda: c.state().free_space is not None)
    candidates = c.state().free_space
    assert len(candidates.trays) == 1 and candidates.total_bytes > 0
    store = c._session.store
    sweep_folders = tuple(store.tray_dir(candidates.trays[0][0]).glob("sweep-*"))
    assert sweep_folders and all(path.exists() for path in sweep_folders)
    reached, release, deleted = (threading.Event() for _ in range(3))
    cleanup_raw = export_module.cleanup_raw

    def blocked_cleanup(*args, **kwargs):
        reached.set()
        assert release.wait(10)
        result = cleanup_raw(*args, **kwargs)
        deleted.set()
        return result

    monkeypatch.setattr(export_module, "cleanup_raw", blocked_cleanup)
    c.confirm_free_space()
    try:
        assert reached.wait(10)
        c._rebuild()
        assert c.state().can_eject and c.state().can_load_next
        getattr(c, action)()
        wait_until(c, lambda: scanner.calls == [call] and c._holder_busy, timeout=3)
        assert c._job_futures and c.state().banner is None
        assert not c._cancel_free.is_set()
        release.set()
        assert deleted.wait(10)
        assert c._holder_busy  # actual raw deletion overlaps the holder move
        assert all(not path.exists() for path in sweep_folders)
    finally:
        release.set()
        holder_release.set()
    wait_until(c, lambda: not c._holder_busy)
    wait_until(c, lambda: c.state().free_space.freed is not None)
    result = c.state().free_space
    assert result.freed == candidates.total_bytes
    assert result.total == 1 and not result.running
    assert all(not path.exists() for path in sweep_folders)
    assert store.snapshot().trays[0].raw_removed
    assert c.state().banner is None
