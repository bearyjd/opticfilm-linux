"""An interrupted scan leaves a flagged tray that is never enhanced or exported, and the
terminal refuses every scanner command until the owner confirms the scanner was reset."""

from pathlib import Path

import pytest

from opticfilm_linux.batch.enhance import enhance_tray
from opticfilm_linux.batch.export import export_batch
from opticfilm_linux.batch.model import BatchDefaults, TrayStage
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import INTERRUPTED_SCAN, BatchError, BatchStore, is_interrupted
from opticfilm_linux.cli import main
from opticfilm_linux.scanner import interrupted
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.scanner.driver import POWER_CYCLE_HINT
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.interrupted import INTERRUPTED_TEXT, ScanInterrupted

FAKE_DRIVER = Path(__file__).parent / "fake_driver"


def test_the_tray_flag_tells_the_owner_what_to_do():
    assert (
        INTERRUPTED_SCAN == INTERRUPTED_TEXT == "interrupted: needs power-cycle + reload + rescan"
    )


def _store(tmp_path: Path) -> BatchStore:
    return BatchStore.create(tmp_path / "b", "T", defaults=BatchDefaults(dpi=600))


def _interrupt():
    raise KeyboardInterrupt


def _scan_interrupted_by(tmp_path, sweep600_dir, before_scan) -> BatchStore:
    store = _store(tmp_path)
    runner = BatchRunner(store, FakeScanner(sweep600_dir, before_scan=before_scan))
    runner.scan_tray()
    assert runner.wait_idle(60)
    runner.close()
    return store


@pytest.mark.parametrize(
    "before_scan",
    [_interrupt, lambda: (_ for _ in ()).throw(ScanInterrupted("the driver was stopped"))],
    ids=["keyboard-interrupt", "driver-stopped"],
)
def test_an_interrupted_scan_leaves_a_flagged_tray_and_no_payload(
    tmp_path, sweep600_dir, before_scan
):
    store = _scan_interrupted_by(tmp_path, sweep600_dir, before_scan)
    tray = store.snapshot().trays[0]
    assert tray.stage is TrayStage.FAILED
    assert tray.error.startswith(INTERRUPTED_TEXT) and is_interrupted(tray)
    assert list(store.tray_dir(1).glob("sweep-*")) == []
    store.close()


def test_a_partly_written_sweep_from_an_interrupted_scan_is_removed(
    tmp_path, sweep600_dir, monkeypatch
):
    from opticfilm_linux.batch import runner as runner_module

    def half_written(_sweep, directory):
        directory.mkdir(parents=True)
        (directory / "rgb.npy").write_bytes(b"half a sweep")
        raise KeyboardInterrupt

    monkeypatch.setattr(runner_module, "save_sweep", half_written)
    store = _scan_interrupted_by(tmp_path, sweep600_dir, None)
    assert store.snapshot().trays[0].error == INTERRUPTED_TEXT
    assert list(store.tray_dir(1).glob("sweep-*")) == []
    store.close()


def test_an_interrupted_tray_is_never_enhanced_or_exported(tmp_path, sweep600_dir):
    store = _scan_interrupted_by(tmp_path, sweep600_dir, _interrupt)
    with pytest.raises(BatchError, match="rescan"):
        enhance_tray(store, 1)
    runner = BatchRunner(store, FakeScanner(sweep600_dir))
    with pytest.raises(BatchError):  # a mode change would re-enhance the tray
        runner.set_tray_mode(1, "negative")
    runner.resume()  # crash recovery queues no enhancement for it either
    assert runner.wait_idle(60)
    runner.close()
    tray = store.snapshot().trays[0]
    assert tray.stage is TrayStage.FAILED and tray.error == INTERRUPTED_TEXT
    result = export_batch(store, tmp_path / "out", "png8")
    assert result.written == ()
    store.close()


def test_a_rescan_clears_the_tray_flag(tmp_path, sweep600_dir):
    store = _scan_interrupted_by(tmp_path, sweep600_dir, _interrupt)
    runner = BatchRunner(store, FakeScanner(sweep600_dir))
    runner.rescan_tray(1)
    assert runner.wait_idle(60)
    runner.close()
    tray = store.snapshot().trays[0]
    assert tray.stage is TrayStage.REVIEW and tray.error is None
    store.close()


def test_the_runner_refuses_to_scan_an_interrupted_scanner_with_the_way_out(tmp_path, sweep600_dir):
    store = _store(tmp_path)
    runner = BatchRunner(store, FakeScanner(sweep600_dir, (ScannerStatus.INTERRUPTED,)))
    with pytest.raises(ScanError, match="confirm-scanner"):
        runner.scan_tray()
    assert store.snapshot().trays == ()
    runner.close()
    store.close()


# -- the terminal ---------------------------------------------------------------------------


@pytest.fixture
def driver_env(monkeypatch, sweep600_dir, tmp_path):
    monkeypatch.setenv("OPTICFILM_DRIVER_DIR", str(FAKE_DRIVER))
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "idle")
    monkeypatch.setenv("OPTICFILM_NEXT_STRIP", "1")
    trace = tmp_path / "trace.log"
    monkeypatch.setenv("FAKE_DRIVER_TRACE", str(trace))
    return trace


@pytest.mark.parametrize("command", ["eject", "load"])
def test_holder_moves_are_refused_after_an_interruption(driver_env, capsys, command):
    interrupted.begin("scan")
    assert main(["batch", command]) == 2
    err = capsys.readouterr().err
    assert "confirm-scanner" in err
    assert POWER_CYCLE_HINT not in err  # a refusal, not a failure
    assert not driver_env.exists()  # no driver command of any kind


def test_batch_scan_is_refused_after_an_interruption(driver_env, tmp_path, capsys):
    main(["batch", "new", str(tmp_path / "b"), "--name", "T", "--dpi", "600"])
    interrupted.begin("eject")
    assert main(["batch", "scan", str(tmp_path / "b")]) == 2
    assert "confirm-scanner" in capsys.readouterr().err
    assert not driver_env.exists()
    with BatchStore.open(tmp_path / "b", readonly=True) as store:
        assert store.snapshot().trays == ()


def test_confirm_needs_an_explicit_yes(driver_env, capsys):
    interrupted.begin("scan")
    assert main(["batch", "confirm-scanner"]) == 2
    out = capsys.readouterr()
    assert "power-cycle" in (out.out + out.err).lower()
    assert interrupted.is_pending()


def test_confirm_with_yes_allows_the_scanner_again(driver_env, capsys):
    interrupted.begin("scan")
    assert main(["batch", "confirm-scanner", "--yes"]) == 0
    assert not interrupted.is_pending()
    assert not driver_env.exists()  # confirming never talks to the scanner
    assert main(["batch", "eject"]) == 0


def test_confirm_with_nothing_pending_changes_nothing(driver_env, capsys):
    assert main(["batch", "confirm-scanner", "--yes"]) == 0
    assert "nothing" in capsys.readouterr().out.lower()


def test_status_shows_the_interrupted_tray(tmp_path, sweep600_dir, capsys):
    _scan_interrupted_by(tmp_path, sweep600_dir, _interrupt).close()
    assert main(["batch", "status", str(tmp_path / "b")]) == 0
    assert INTERRUPTED_TEXT in capsys.readouterr().out
