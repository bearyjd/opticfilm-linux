"""Automatic eject (OPTICFILM_AUTO_EJECT) on the fake community driver: the real command path,
with its interrupted marker, latch and power-cycle handling. No scanner is ever touched."""

import json
import os
import signal
import time
from pathlib import Path

import pytest

from controller_helpers import open_new, wait_all_in_review, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.messages import EJECT_DID_NOT_RUN, holder_failed_text
from opticfilm_linux.batch.model import TrayStage
from opticfilm_linux.scanner import driver as driver_module
from opticfilm_linux.scanner import interrupted
from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner
from opticfilm_linux.scanner.holder_latch import HolderLatch

FAKE_DRIVER = Path(__file__).parent / "fake_driver"


@pytest.fixture
def build(sweep600_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    monkeypatch.setenv("FAKE_DRIVER_TRACE", str(tmp_path / "trace.log"))
    for name in ("OPTICFILM_AUTO_EJECT", "OPTICFILM_DRIVER_CLEAN", "FAKE_DRIVER_EJECT_MODE"):
        monkeypatch.delenv(name, raising=False)
    made: list[Controller] = []

    def make(auto_eject=True, **config):
        settings = DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs", **config)
        c = Controller(lambda _dpi: DriverScanner(settings), auto_eject=auto_eject)
        made.append(c)
        open_new(c, tmp_path / "b")
        return c

    yield make
    for c in made:
        c.shutdown()


def _trace(tmp_path) -> list[str]:
    path = tmp_path / "trace.log"
    lines = path.read_text().splitlines() if path.exists() else []
    return [line.split(maxsplit=1)[1] for line in lines]


def _ejects(tmp_path) -> int:
    return _trace(tmp_path).count("eject")


def _settle(c, seconds=0.5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        c.tick()
        time.sleep(0.02)
    wait_until(c, lambda: not c._holder_busy)


def _scan(c) -> None:
    wait_until(c, lambda: c.state().can_scan)
    c.scan()


def _failed(c) -> bool:
    return bool(c.state().trays) and c.state().trays[0].stage is TrayStage.FAILED


def _banner(c) -> str:
    banner = c.state().banner
    return banner.message if banner is not None else ""


def test_switched_off_the_driver_is_never_asked_to_eject(build, tmp_path):
    c = build(auto_eject=False)
    _scan(c)
    wait_all_in_review(c, 1)
    _settle(c)
    assert _ejects(tmp_path) == 0


def test_one_separate_eject_after_the_scan_command_ended(build, tmp_path, monkeypatch):
    argv = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_DRIVER_ARGV", str(argv))
    c = build()
    _scan(c)
    wait_until(c, lambda: _ejects(tmp_path) == 1 and not c._holder_busy)
    assert "--eject" not in json.loads(argv.read_text())  # never the driver's scan --eject
    wait_all_in_review(c, 1)
    _settle(c)
    trace = _trace(tmp_path)
    assert trace.index("scan end") < trace.index("eject") and _ejects(tmp_path) == 1
    assert not interrupted.is_pending()  # the eject's mark was begun and cleared
    assert c._holder_latch is HolderLatch.RELEASED and not _failed(c)


@pytest.mark.parametrize("mode", ["cal_fail", "missing_frame"], ids=["driver-error", "missing"])
def test_a_failed_driver_scan_never_ejects(build, tmp_path, monkeypatch, mode):
    monkeypatch.setenv("FAKE_DRIVER_MODE", mode)
    c = build()
    _scan(c)
    wait_until(c, lambda: _failed(c))
    _settle(c)
    assert _ejects(tmp_path) == 0


def test_a_scan_that_timed_out_never_ejects(build, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_MODE", "hang")
    c = build(timeout_s=1)  # the driver is stopped: ScanInterrupted
    _scan(c)
    wait_until(c, lambda: _failed(c))
    _settle(c)
    assert _ejects(tmp_path) == 0


def test_a_driver_stopped_by_a_signal_never_ejects(build, tmp_path, monkeypatch):
    """The driver exits 130 after Ctrl-C: ``result.stopped``, a partial payload."""
    pids = tmp_path / "pids.json"
    monkeypatch.setenv("FAKE_DRIVER_MODE", "hang")
    monkeypatch.setenv("FAKE_DRIVER_PIDS", str(pids))
    c = build()
    _scan(c)
    wait_until(c, pids.exists)
    parent, child = json.loads(pids.read_text())
    os.kill(parent, signal.SIGINT)
    os.kill(child, signal.SIGKILL)
    wait_until(c, lambda: _failed(c))
    _settle(c)
    assert _ejects(tmp_path) == 0


def test_an_interrupted_marker_left_after_the_scan_stops_the_eject(build, tmp_path):
    c = build()
    _scan(c)
    runner = c._session.runner
    wait_until(c, runner.scan_pending)
    assert runner.wait_scans_idle(60)  # no tick from here until the mark is there
    interrupted.begin("scan")
    try:
        _settle(c)
        assert _ejects(tmp_path) == 0 and "Automatic eject skipped" in _banner(c)
    finally:
        interrupted.clear()


def test_a_sensor_skip_says_the_eject_did_not_run(build, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_EJECT_MODE", "skipped")
    c = build()
    _scan(c)
    wait_until(c, lambda: _ejects(tmp_path) == 1 and not c._holder_busy)
    wait_all_in_review(c, 1)
    assert _banner(c) == EJECT_DID_NOT_RUN and c._holder_latch is HolderLatch.UNKNOWN


def test_a_failed_eject_keeps_the_tray_and_asks_for_a_power_cycle(build, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_EJECT_MODE", "fail")
    c = build()
    _scan(c)
    wait_until(c, lambda: _ejects(tmp_path) == 1 and not c._holder_busy)
    wait_all_in_review(c, 1)
    assert _banner(c) == holder_failed_text("eject")
    assert c._holder_latch is HolderLatch.UNKNOWN and not _failed(c)


def test_a_hung_eject_is_marked_while_it_runs_and_ends_as_a_failed_eject(
    build, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_DRIVER_EJECT_MODE", "hang")
    monkeypatch.setattr(driver_module, "HOLDER_TIMEOUT_S", 2)
    c = build()
    _scan(c)
    wait_until(c, lambda: _ejects(tmp_path) == 1)
    assert interrupted.is_pending()  # begun before the driver moved anything
    wait_until(c, lambda: not c._holder_busy and _banner(c) == holder_failed_text("eject"))
    assert c._holder_latch is HolderLatch.UNKNOWN and interrupted.is_pending()
    wait_until(c, lambda: c.state().trays[0].stage is TrayStage.REVIEW)
