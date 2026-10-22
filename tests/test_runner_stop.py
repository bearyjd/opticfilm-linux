import threading

import pytest

from opticfilm_linux.batch import runner as runner_module
from opticfilm_linux.batch.model import BatchDefaults, TrayStage
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.sweepstore import save_sweep
from synthetic import make_sweep

TIMEOUT = 120


@pytest.fixture
def sweep_dir(tmp_path):
    save_sweep(make_sweep(dpi=600), tmp_path / "sweep")
    return tmp_path / "sweep"


def test_stop_drops_queued_enhancement_and_resume_requeues(tmp_path, sweep_dir, monkeypatch):
    gate = threading.Event()
    real = runner_module.enhance_tray

    def slow(store, number, on_progress=None):
        gate.wait(TIMEOUT)
        real(store, number, on_progress=on_progress)

    monkeypatch.setattr(runner_module, "enhance_tray", slow)
    store = BatchStore.create(tmp_path / "b", name="T", defaults=BatchDefaults(dpi=600))
    runner = BatchRunner(store, FakeScanner(sweep_dir))
    runner.scan_tray()
    assert runner.wait_scans_idle(TIMEOUT)
    runner.scan_tray()
    assert runner.wait_scans_idle(TIMEOUT)
    stopper = threading.Thread(target=runner.stop)
    stopper.start()
    gate.set()
    stopper.join(TIMEOUT)
    assert not stopper.is_alive()
    stages = [t.stage for t in store.snapshot().trays]
    assert stages[0] is TrayStage.REVIEW and stages[1] is TrayStage.SCANNED
    store.close()

    monkeypatch.setattr(runner_module, "enhance_tray", real)
    with BatchStore.open(tmp_path / "b") as reopened:
        again = BatchRunner(reopened, FakeScanner(sweep_dir))
        again.resume()
        assert again.wait_idle(TIMEOUT)
        again.close()
        assert [t.stage for t in reopened.snapshot().trays] == [TrayStage.REVIEW] * 2


def test_stop_is_idempotent_and_safe_after_close(tmp_path, sweep_dir):
    with BatchStore.create(tmp_path / "b", name="T", defaults=BatchDefaults(dpi=600)) as store:
        runner = BatchRunner(store, FakeScanner(sweep_dir))
        runner.close()
        runner.stop()
        runner.stop()


def test_scan_pending_true_while_scanning(tmp_path, sweep_dir):
    gate = threading.Event()
    scanner = FakeScanner(sweep_dir, before_scan=lambda: gate.wait(TIMEOUT))
    with BatchStore.create(tmp_path / "b", name="T", defaults=BatchDefaults(dpi=600)) as store:
        runner = BatchRunner(store, scanner)
        assert not runner.scan_pending()
        runner.scan_tray()
        assert runner.scan_pending()
        gate.set()
        assert runner.wait_idle(TIMEOUT)
        assert not runner.scan_pending()
        runner.close()
