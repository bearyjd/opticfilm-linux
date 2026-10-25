import json
from pathlib import Path

import pytest

from opticfilm_linux.cli import main

FAKE_DRIVER = Path(__file__).parent / "fake_driver"


@pytest.fixture
def driver_env(monkeypatch, sweep600_dir, tmp_path):
    monkeypatch.setenv("OPTICFILM_DRIVER_DIR", str(FAKE_DRIVER))
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("OPTICFILM_NEXT_STRIP", raising=False)
    log = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_DRIVER_ARGV", str(log))
    monkeypatch.setenv("FAKE_DRIVER_TRACE", str(tmp_path / "trace.log"))
    return log


def test_batch_eject_asks_the_driver_to_eject(driver_env, capsys):
    assert main(["batch", "eject"]) == 0
    trace = driver_env.parent / "trace.log"
    assert trace.read_text().split()[1:] == ["eject"]  # first field is a timestamp
    assert "holder ejected" in capsys.readouterr().out


def test_batch_load_is_refused_until_next_strip_is_switched_on(driver_env, capsys):
    assert main(["batch", "load"]) == 2
    assert "OPTICFILM_NEXT_STRIP" in capsys.readouterr().err
    assert not driver_env.exists()  # the driver was never started


def test_batch_load_runs_next_strip_when_switched_on(driver_env, monkeypatch, capsys):
    monkeypatch.setenv("OPTICFILM_NEXT_STRIP", "1")
    assert main(["batch", "load"]) == 0
    assert json.loads(driver_env.read_text()) == ["load", "--next-strip"]
    assert "holder loaded" in capsys.readouterr().out


def test_batch_load_failure_is_reported_not_raised(driver_env, monkeypatch, capsys):
    monkeypatch.setenv("OPTICFILM_NEXT_STRIP", "1")
    monkeypatch.setenv("FAKE_DRIVER_LOAD_EXIT", "1")
    assert main(["batch", "load"]) == 2
    assert "error:" in capsys.readouterr().err


def test_batch_eject_says_what_is_missing_without_the_driver(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPTICFILM_DRIVER_DIR", str(tmp_path / "nowhere"))
    assert main(["batch", "eject"]) == 2
    assert "OPTICFILM_DRIVER_DIR" in capsys.readouterr().err


def test_batch_eject_and_load_work_on_a_fake_scanner(driver_env, sweep600_dir, monkeypatch, capsys):
    monkeypatch.setenv("OPTICFILM_NEXT_STRIP", "1")
    assert main(["batch", "eject", "--fake", str(sweep600_dir)]) == 0
    assert main(["batch", "load", "--fake", str(sweep600_dir)]) == 0
    assert not driver_env.exists()
    assert not (driver_env.parent / "trace.log").exists()  # the driver was never started


def test_batch_load_on_a_fake_scanner_is_still_gated(driver_env, sweep600_dir, capsys):
    assert main(["batch", "load", "--fake", str(sweep600_dir)]) == 2
    assert "OPTICFILM_NEXT_STRIP" in capsys.readouterr().err


def test_batch_eject_ctrl_c_exits_130_without_a_traceback(monkeypatch, capsys):
    class Interrupted:
        def eject(self) -> None:
            raise KeyboardInterrupt

        def load_next_strip(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr("opticfilm_linux.batch.cli.real_scanner", Interrupted)
    assert main(["batch", "eject"]) == 130
    assert "interrupted" in capsys.readouterr().err


def test_batch_eject_refuses_a_scanner_that_cannot_move_the_holder(monkeypatch, capsys):
    monkeypatch.setattr("opticfilm_linux.batch.cli.real_scanner", object)
    assert main(["batch", "eject"]) == 2
    assert "cannot move the holder" in capsys.readouterr().err
