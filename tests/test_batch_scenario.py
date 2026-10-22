"""The design's success scenario, end to end, on the fake scanner."""

import dataclasses
import shutil
import time

import pytest
import tifffile

from opticfilm_linux.batch.enhance import RawRemoved
from opticfilm_linux.batch.export import cleanup_raw, export_batch
from opticfilm_linux.batch.model import BatchDefaults, SlideSettings, TrayRecord, TrayStage
from opticfilm_linux.batch.review import accept_all
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.base import ScannerNotReady, ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.sweepstore import save_sweep
from synthetic import make_sweep

TIMEOUT = 240


def _scan_when_ready(runner: BatchRunner) -> int:
    for _ in range(1000):
        try:
            return runner.scan_tray()
        except ScannerNotReady:
            time.sleep(0.01)
    raise AssertionError("the scanner never became ready")


def _stage(store, number):
    return next(t for t in store.snapshot().trays if t.number == number).stage


def test_five_trays_with_crash_resume_edit_and_export(tmp_path):
    sweep_dir = tmp_path / "sweep"
    save_sweep(make_sweep(dpi=600, dust=12), sweep_dir)
    root = tmp_path / "batch"
    store = BatchStore.create(root, name="Trips", defaults=BatchDefaults(dpi=600))
    scanner = FakeScanner(sweep_dir, statuses=(ScannerStatus.NOT_READY, ScannerStatus.READY))
    runner = BatchRunner(store, scanner)

    # 1. five trays: the caller presses Scan whenever the scanner reports ready
    for _ in range(5):
        _scan_when_ready(runner)
    assert runner.wait_idle(TIMEOUT)
    runner.close()
    trays = store.snapshot().trays
    assert [t.stage for t in trays] == [TrayStage.REVIEW] * 5
    assert all(store.preview_path(t.number, 1, "after").is_file() for t in trays)

    # 2. a crash left tray 6 mid-scan and tray 7 mid-enhancement; reopen and resume
    shutil.copytree(store.sweep_dir(1, 1), store.sweep_dir(7, 1))
    half = store.sweep_tmp_dir(6, 1)
    half.mkdir(parents=True)
    (half / "rgb.npy").write_bytes(b"half a sweep")

    def crashed(manifest):
        extra = (
            TrayRecord(number=6, stage=TrayStage.SCANNING),
            TrayRecord(number=7, stage=TrayStage.ENHANCING),
        )
        return dataclasses.replace(manifest, trays=manifest.trays + extra)

    store.update(crashed)
    store = BatchStore.open(root)
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    runner.resume()
    assert runner.wait_idle(TIMEOUT)
    assert _stage(store, 6) is TrayStage.FAILED and not half.exists()
    assert _stage(store, 7) is TrayStage.REVIEW

    # 3. edit one slide, accept everything, export twice
    runner.edit_slide(1, 1, SlideSettings(brightness_ev=1.0))
    assert runner.wait_idle(TIMEOUT)
    accept_all(store)
    out = tmp_path / "out"
    first = export_batch(store, out)
    assert len(first.written) == 24  # trays 1-5 and 7, four slides each; tray 6 failed
    assert export_batch(store, out).written == ()
    edited, plain = tifffile.imread(first.written[0]), tifffile.imread(first.written[4])
    assert edited.mean() > plain.mean()

    # 4. raw cleanup of an exported tray, then edits are refused
    assert cleanup_raw(store, 1) > 0
    with pytest.raises(RawRemoved):
        runner.edit_slide(1, 1, SlideSettings(rotation=90))
    runner.close()
    assert BatchStore.open(root).snapshot() == store.snapshot()
