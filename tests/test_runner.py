import dataclasses
import errno
import threading
import time

import pytest

from opticfilm_linux.batch.events import SlideUpdated, TrayFailed, TrayStageChanged
from opticfilm_linux.batch.model import (
    BatchDefaults,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.scanner.base import ScanError, ScannerNotReady, ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.sweepstore import save_sweep
from synthetic import make_sweep

TIMEOUT = 90


@pytest.fixture(scope="module")
def sweep_dir(tmp_path_factory):
    directory = tmp_path_factory.mktemp("sweep600")
    save_sweep(make_sweep(dpi=600, dust=12), directory)
    return directory


def _make(tmp_path, sweep_dir, statuses=(ScannerStatus.READY,), before_scan=None, defaults=None):
    store = BatchStore.create(
        tmp_path / "batch", name="T", defaults=defaults or BatchDefaults(dpi=600)
    )
    runner = BatchRunner(store, FakeScanner(sweep_dir, statuses, before_scan))
    return store, runner


def _wait_for(predicate, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _tray(store, number=1) -> TrayRecord:
    return next(t for t in store.snapshot().trays if t.number == number)


def _drain(runner):
    events = []
    while not runner.events.empty():
        events.append(runner.events.get_nowait())
    return events


def test_scan_when_not_ready_raises_and_creates_no_tray(tmp_path, sweep_dir):
    store, runner = _make(tmp_path, sweep_dir, statuses=(ScannerStatus.NOT_READY,))
    with pytest.raises(ScannerNotReady):
        runner.scan_tray()
    assert store.snapshot().trays == ()
    runner.close()


def test_scan_tray_ends_in_review_with_four_slides(tmp_path, sweep_dir):
    store, runner = _make(tmp_path, sweep_dir)
    assert runner.scan_tray() == 1
    assert runner.wait_idle(TIMEOUT)
    tray = _tray(store)
    assert tray.stage is TrayStage.REVIEW and len(tray.slides) == 4
    assert store.sweep_dir(1, 1).is_dir() and not store.sweep_tmp_dir(1, 1).exists()
    runner.close()


def test_events_follow_the_stage_order(tmp_path, sweep_dir):
    store, runner = _make(tmp_path, sweep_dir)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    events = _drain(runner)
    stages = [e.stage for e in events if isinstance(e, TrayStageChanged) and e.number == 1]
    assert stages == [
        TrayStage.SCANNING,
        TrayStage.SCANNED,
        TrayStage.ENHANCING,
        TrayStage.REVIEW,
    ]
    assert [e.position for e in events if isinstance(e, SlideUpdated)] == [1, 2, 3, 4]
    runner.close()


def test_failed_scan_marks_failed_and_leaves_no_sweep(tmp_path, sweep_dir):
    def jam():
        raise ScanError("jam")

    store, runner = _make(tmp_path, sweep_dir, before_scan=jam)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    tray = _tray(store)
    assert tray.stage is TrayStage.FAILED and "jam" in (tray.error or "")
    assert not store.sweep_dir(1, 1).exists() and not store.sweep_tmp_dir(1, 1).exists()
    assert any(isinstance(e, TrayFailed) and "jam" in e.message for e in _drain(runner))
    runner.close()


def test_unexpected_scan_error_fails_the_tray_instead_of_vanishing(tmp_path, sweep_dir):
    def broken():
        raise RuntimeError("driver bug")

    store, runner = _make(tmp_path, sweep_dir, before_scan=broken)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    assert _tray(store).stage is TrayStage.FAILED and "driver bug" in _tray(store).error
    runner.close()


def test_enhancement_runs_while_the_next_tray_scans(tmp_path, sweep_dir):
    gate = threading.Event()
    calls = []

    def hold_second_scan():
        calls.append(1)
        if len(calls) >= 2:
            assert gate.wait(TIMEOUT)

    store, runner = _make(tmp_path, sweep_dir, before_scan=hold_second_scan)
    runner.scan_tray()
    assert runner.wait_scans_idle(TIMEOUT)
    runner.scan_tray()
    assert _wait_for(lambda: _tray(store, 1).stage is TrayStage.REVIEW)
    assert _tray(store, 2).stage is TrayStage.SCANNING
    gate.set()
    assert runner.wait_idle(TIMEOUT)
    assert _tray(store, 2).stage is TrayStage.REVIEW
    runner.close()


def test_scan_tray_returns_without_waiting_for_the_scan(tmp_path, sweep_dir):
    gate = threading.Event()
    store, runner = _make(tmp_path, sweep_dir, before_scan=lambda: gate.wait(TIMEOUT))
    runner.scan_tray()
    assert _tray(store).stage is TrayStage.SCANNING
    gate.set()
    assert runner.wait_idle(TIMEOUT)
    runner.close()


def _scanned_tray_store(tmp_path, stage):
    store = BatchStore.create(tmp_path / "batch", name="T", defaults=BatchDefaults(dpi=600))
    save_sweep(make_sweep(dpi=600), store.sweep_dir(1, 1))
    tray = TrayRecord(number=1, stage=stage)
    store.update(lambda m: dataclasses.replace(m, trays=(tray,)))
    return store


def test_resume_enhances_a_scanned_tray(tmp_path, sweep_dir):
    store = _scanned_tray_store(tmp_path, TrayStage.SCANNED)
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    runner.resume()
    assert runner.wait_idle(TIMEOUT)
    assert _tray(store).stage is TrayStage.REVIEW
    runner.close()


def test_resume_fails_an_interrupted_scan_and_cleans_up(tmp_path, sweep_dir):
    store = _scanned_tray_store(tmp_path, TrayStage.SCANNING)
    tmp = store.sweep_tmp_dir(1, 2)
    tmp.mkdir(parents=True)
    (tmp / "rgb.npy").write_bytes(b"half")
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    runner.resume()
    assert runner.wait_idle(TIMEOUT)
    assert _tray(store).stage is TrayStage.FAILED and not tmp.exists()
    runner.close()


def test_rescan_keeps_old_sweep_and_resets_slides(tmp_path, sweep_dir):
    store, runner = _make(tmp_path, sweep_dir)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)

    def edit(manifest):
        tray = manifest.trays[0]
        first = dataclasses.replace(
            tray.slides[0], settings=SlideSettings(rotation=90), owner_decided=True
        )
        tray = dataclasses.replace(tray, slides=(first,) + tray.slides[1:])
        return dataclasses.replace(manifest, trays=(tray,))

    store.update(edit)
    assert runner.rescan_tray(1) == 1
    assert runner.wait_idle(TIMEOUT)
    tray = _tray(store)
    assert tray.sweep_version == 2 and tray.stage is TrayStage.REVIEW
    assert store.sweep_dir(1, 1).is_dir() and store.sweep_dir(1, 2).is_dir()
    first = tray.slides[0]
    assert first.settings == SlideSettings() and not first.owner_decided
    assert first.status is SlideStatus.ACCEPTED
    runner.close()


def test_rescan_of_a_tray_that_is_scanning_raises(tmp_path, sweep_dir):
    gate = threading.Event()
    store, runner = _make(tmp_path, sweep_dir, before_scan=lambda: gate.wait(TIMEOUT))
    runner.scan_tray()
    with pytest.raises(BatchError, match="scanning"):
        runner.rescan_tray(1)
    gate.set()
    assert runner.wait_idle(TIMEOUT)
    runner.close()


def test_rescan_of_an_unknown_tray_raises(tmp_path, sweep_dir):
    _store, runner = _make(tmp_path, sweep_dir)
    with pytest.raises(BatchError, match="no tray 4"):
        runner.rescan_tray(4)
    runner.close()


def test_rescan_when_not_ready_leaves_the_tray_untouched(tmp_path, sweep_dir):
    store, runner = _make(tmp_path, sweep_dir)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    before = _tray(store)
    not_ready = BatchRunner(store, FakeScanner(sweep_dir, (ScannerStatus.BUSY,)))
    with pytest.raises(ScannerNotReady):
        not_ready.rescan_tray(1)
    assert _tray(store) == before
    not_ready.close()
    runner.close()


def test_scan_without_infrared_still_reaches_review(tmp_path, sweep_dir):
    defaults = BatchDefaults(dpi=600, infrared=False)
    store, runner = _make(tmp_path, sweep_dir, defaults=defaults)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    assert _tray(store).stage is TrayStage.REVIEW
    runner.close()


def test_close_waits_for_running_jobs_including_queued_enhancement(tmp_path, sweep_dir):
    store, runner = _make(tmp_path, sweep_dir)
    runner.scan_tray()
    runner.close()
    assert _tray(store).stage is TrayStage.REVIEW


def test_a_second_scan_while_one_is_pending_is_refused(tmp_path, sweep_dir):
    gate = threading.Event()
    store, runner = _make(tmp_path, sweep_dir, before_scan=lambda: gate.wait(TIMEOUT))
    runner.scan_tray()
    with pytest.raises(BatchError, match="already"):
        runner.scan_tray()
    assert len(store.snapshot().trays) == 1
    gate.set()
    assert runner.wait_idle(TIMEOUT)
    runner.close()


def test_a_rescan_while_another_scan_is_pending_is_refused(tmp_path, sweep_dir):
    gate = threading.Event()
    calls = []

    def hold_second():
        calls.append(1)
        if len(calls) >= 2:
            gate.wait(TIMEOUT)

    store, runner = _make(tmp_path, sweep_dir, before_scan=hold_second)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    runner.scan_tray()  # tray 2 now scanning (held)
    with pytest.raises(BatchError, match="already"):
        runner.rescan_tray(1)
    gate.set()
    assert runner.wait_idle(TIMEOUT)
    runner.close()


def test_wait_scans_idle_waits_for_the_scan_only(tmp_path, sweep_dir):
    gate = threading.Event()
    store, runner = _make(tmp_path, sweep_dir, before_scan=lambda: gate.wait(TIMEOUT))
    runner.scan_tray()
    assert runner.wait_scans_idle(0.05) is False
    gate.set()
    assert runner.wait_scans_idle(TIMEOUT) is True
    assert _tray(store).stage is not TrayStage.SCANNING
    assert runner.wait_idle(TIMEOUT)
    runner.close()


def test_changing_the_mode_of_a_scanning_tray_is_refused_and_the_scan_survives(tmp_path, sweep_dir):
    gate = threading.Event()
    store, runner = _make(tmp_path, sweep_dir, before_scan=lambda: gate.wait(TIMEOUT))
    runner.scan_tray()
    with pytest.raises(BatchError, match="review"):
        runner.set_tray_mode(1, "negative")
    gate.set()
    assert runner.wait_idle(TIMEOUT)
    assert _tray(store).stage is TrayStage.REVIEW
    runner.close()


def test_disk_full_while_recording_a_scan_fails_the_tray_visibly(tmp_path, sweep_dir, monkeypatch):
    real = BatchStore.update_tray

    def flaky(self, number, **changes):
        if changes.get("stage") is TrayStage.SCANNED:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, number, **changes)

    monkeypatch.setattr(BatchStore, "update_tray", flaky)
    store, runner = _make(tmp_path, sweep_dir)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    tray = _tray(store)
    assert tray.stage is TrayStage.FAILED and "No space" in (tray.error or "")
    assert any(isinstance(e, TrayFailed) and "No space" in e.message for e in _drain(runner))
    runner.close()


def test_a_job_crash_is_reported_even_if_the_manifest_cannot_be_written(
    tmp_path, sweep_dir, monkeypatch
):
    def broken(self, *args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(BatchStore, "update_tray", broken)
    monkeypatch.setattr(BatchStore, "update_tray_if", broken)
    store, runner = _make(tmp_path, sweep_dir)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    assert any(isinstance(e, TrayFailed) and "No space" in e.message for e in _drain(runner))
    runner.close()


def test_a_crashing_enhancement_fails_the_tray(tmp_path, sweep_dir, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("opticfilm_linux.batch.runner.enhance_tray", boom)
    store, runner = _make(tmp_path, sweep_dir)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    assert _tray(store).stage is TrayStage.FAILED and "boom" in _tray(store).error
    runner.close()
