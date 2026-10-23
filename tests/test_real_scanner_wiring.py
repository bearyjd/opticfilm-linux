from pathlib import Path

import pytest

from opticfilm_linux.app.qt.app import scanner_factory
from opticfilm_linux.cli import main
from opticfilm_linux.scanner.base import ScanError
from opticfilm_linux.scanner.driver import DriverScanner
from opticfilm_linux.scanner.select import real_scanner

FAKE_DRIVER = Path(__file__).parent / "fake_driver"


@pytest.fixture
def fake_driver_env(monkeypatch, sweep600_dir, tmp_path):
    monkeypatch.setenv("OPTICFILM_DRIVER_DIR", str(FAKE_DRIVER))
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def test_real_scanner_is_the_driver_backend_when_the_driver_is_there(fake_driver_env):
    assert isinstance(real_scanner(), DriverScanner)


def test_real_scanner_explains_a_missing_driver(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTICFILM_DRIVER_DIR", str(tmp_path / "nowhere"))
    with pytest.raises(ScanError, match="OPTICFILM_DRIVER_DIR"):
        real_scanner()


def test_ofscan_scan_runs_on_the_real_scanner(fake_driver_env, tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["scan", "--dpi", "600", "--out", str(out)]) == 0
    assert "4 slides written" in capsys.readouterr().out
    assert len(list(out.glob("*.tif"))) == 4


def test_ofscan_scan_says_what_is_missing_without_the_driver(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPTICFILM_DRIVER_DIR", str(tmp_path / "nowhere"))
    assert main(["scan", "--dpi", "600", "--out", str(tmp_path / "out")]) == 2
    assert "OPTICFILM_DRIVER_DIR" in capsys.readouterr().err


def test_batch_scan_runs_on_the_real_scanner(fake_driver_env, tmp_path, capsys):
    batch = tmp_path / "batch"
    assert main(["batch", "new", str(batch), "--name", "T", "--dpi", "600"]) == 0
    assert main(["batch", "scan", str(batch), "--count", "1"]) == 0
    assert "review" in capsys.readouterr().out


def test_the_app_uses_the_real_scanner_unless_demo_mode(fake_driver_env, sweep600_dir):
    assert isinstance(scanner_factory(None)(600), DriverScanner)
