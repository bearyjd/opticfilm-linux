import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from opticfilm_linux.model import ScanSettings
from opticfilm_linux.scanner import driver as driver_module
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner

FAKE_DRIVER = Path(__file__).parent / "fake_driver"


@pytest.fixture
def make(sweep600_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))

    def build(**config):
        base = {"driver_dir": FAKE_DRIVER, "log_dir": tmp_path / "logs", "work_dir": tmp_path / "w"}
        return DriverScanner(DriverConfig(**{**base, **config}))

    return build


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie is dead for our purposes
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def test_a_timeout_kills_the_driver_and_its_children_and_keeps_the_partial_log(
    make, tmp_path, monkeypatch
):
    pids_file = tmp_path / "pids.json"
    monkeypatch.setenv("FAKE_DRIVER_MODE", "hang")
    monkeypatch.setenv("FAKE_DRIVER_PIDS", str(pids_file))
    monkeypatch.setattr(driver_module, "STOP_GRACE_S", 1.0)
    with pytest.raises(ScanError, match="did not finish"):
        make(timeout_s=2).scan_holder(ScanSettings(dpi=600))
    parent, child = json.loads(pids_file.read_text())
    deadline = time.monotonic() + 5
    while (_alive(parent) or _alive(child)) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(parent) and not _alive(child)
    logs = list((tmp_path / "logs").glob("scan-*.log"))
    assert len(logs) == 1 and "scanning frame 1" in logs[0].read_text()


def test_output_that_is_not_text_does_not_lose_a_finished_scan(make, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_MODE", "noisy")
    sweep = make().scan_holder(ScanSettings(dpi=600))
    assert sweep.rgb.shape[0] > 0


def test_a_huge_driver_log_goes_to_a_file_and_the_error_message_stays_small(
    make, monkeypatch, tmp_path
):
    monkeypatch.setenv("FAKE_DRIVER_MODE", "noisy")
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(tmp_path / "missing-sweep"))  # frames never appear
    with pytest.raises(ScanError) as info:
        make().scan_holder(ScanSettings(dpi=600))
    assert len(str(info.value)) < 5000
    assert max(p.stat().st_size for p in (tmp_path / "logs").glob("scan-*.log")) > 5_000_000


def test_old_logs_are_pruned(make, monkeypatch, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    for i in range(60):
        (logs / f"scan-2020010{i % 10}-{i:06d}.log").write_text("old")
    make().scan_holder(ScanSettings(dpi=600))
    assert len(list(logs.glob("scan-*.log"))) <= driver_module.KEEP_LOGS


def test_the_import_probe_cannot_hang_the_app(tmp_path, monkeypatch):
    sleeper = tmp_path / "slow-python"
    sleeper.write_text("#!/bin/sh\nsleep 30\n")
    sleeper.chmod(0o755)
    monkeypatch.setattr(driver_module, "PROBE_TIMEOUT_S", 0.5)
    started = time.monotonic()
    assert DriverConfig(python=str(sleeper)).available() is False
    assert time.monotonic() - started < 5


def test_not_enough_disk_space_for_the_sweep_is_a_clear_error(make, monkeypatch):
    monkeypatch.setattr(
        shutil, "disk_usage", lambda _p: shutil._ntuple_diskusage(10**12, 10**12 - 1000, 1000)
    )
    with pytest.raises(ScanError, match="disk space"):
        make().scan_holder(ScanSettings(dpi=600))


def test_the_default_work_dir_is_not_the_system_temp_dir(sweep600_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    scanner = DriverScanner(DriverConfig(driver_dir=FAKE_DRIVER, work_dir=None))
    scanner.scan_holder(ScanSettings(dpi=600))
    assert (tmp_path / "cache" / "opticfilm-linux" / "work").is_dir()


def test_a_failing_doctor_never_reads_as_ready(make, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "idle")
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR_EXIT", "1")
    assert make().status() is ScannerStatus.ERROR


def test_after_a_failed_scan_the_status_is_error_until_the_scanner_is_power_cycled(
    make, monkeypatch
):
    scanner = make()
    monkeypatch.setenv("FAKE_DRIVER_MODE", "cal_fail")
    with pytest.raises(ScanError):
        scanner.scan_holder(ScanSettings(dpi=600))
    monkeypatch.setenv(
        "FAKE_DRIVER_DOCTOR", "idle"
    )  # looks loaded, but the driver said power-cycle
    assert scanner.status() is ScannerStatus.ERROR
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "cold")  # the power cycle happened
    scanner._status_cache = None
    assert scanner.status() is ScannerStatus.NOT_READY
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "idle")
    scanner._status_cache = None
    assert scanner.status() is ScannerStatus.READY


def test_eject_makes_the_next_status_check_ask_the_scanner_again(make, monkeypatch):
    scanner = make()
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "idle")
    assert scanner.status() is ScannerStatus.READY
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "unloaded")
    scanner.eject()
    assert scanner.status() is ScannerStatus.NOT_READY  # not the cached READY


def test_a_failed_eject_drops_the_cached_status_and_asks_for_a_power_cycle(make, monkeypatch):
    scanner = make()
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "idle")
    assert scanner.status() is ScannerStatus.READY
    monkeypatch.setenv("FAKE_DRIVER_EJECT_EXIT", "1")
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "unloaded")
    with pytest.raises(ScanError):
        scanner.eject()
    assert scanner.status() is ScannerStatus.ERROR  # not the cached READY; needs a power cycle


def test_the_interpreter_is_not_confused_by_a_driver_path_with_spaces(
    sweep600_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    spaced = tmp_path / "my driver"
    shutil.copytree(FAKE_DRIVER, spaced)
    scanner = DriverScanner(DriverConfig(driver_dir=spaced, python=sys.executable))
    assert scanner.scan_holder(ScanSettings(dpi=600)).rgb.shape[0] > 0
