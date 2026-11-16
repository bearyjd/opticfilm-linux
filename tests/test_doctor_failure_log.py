"""A failed doctor status check leaves its last output in one bounded file, for the owner."""

import os
import time
from pathlib import Path

import pytest

from opticfilm_linux.scanner import driver as driver_module
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.scanner.driver import DOCTOR_TAIL_BYTES, DriverConfig, DriverScanner, _Result

FAKE_DRIVER = Path(__file__).parent / "fake_driver"
IDLE = "reg 0x01 = 0x22 (idle-homed)\npresent: True\nstatus word: 0xdc55\n"
ERROR_REPORT = "reg 0x01 = 0x31 (stuck)\nsomething went wrong\n"


def _scanner(tmp_path, runner) -> DriverScanner:
    scanner = DriverScanner(
        DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs", work_dir=tmp_path / "w")
    )
    scanner._run = runner  # type: ignore[method-assign]
    return scanner


def _failure_log(tmp_path) -> Path:
    return tmp_path / "logs" / "doctor-last-failure.log"


def test_a_failing_doctor_report_is_kept_with_a_timestamp(tmp_path):
    scanner = _scanner(tmp_path, lambda *a, **k: _Result(0, ERROR_REPORT))
    assert scanner.status() is ScannerStatus.ERROR
    text = _failure_log(tmp_path).read_text()
    assert text.startswith("# ") and "something went wrong" in text


def test_a_doctor_that_did_not_answer_is_kept(tmp_path):
    def timed_out(*_a, **_k):
        raise ScanError("the driver did not finish within 20 s. Log: half a report")

    scanner = _scanner(tmp_path, timed_out)
    assert scanner.status() is ScannerStatus.ERROR
    assert "half a report" in _failure_log(tmp_path).read_text()


def test_a_scanner_that_is_not_connected_never_writes(tmp_path):
    scanner = _scanner(tmp_path, lambda *a, **k: _Result(1, "Traceback: no device\n"))
    assert scanner.status() is ScannerStatus.NOT_CONNECTED
    assert not _failure_log(tmp_path).exists()


def test_the_same_failure_output_twice_writes_once(tmp_path, monkeypatch):
    scanner = _scanner(tmp_path, lambda *a, **k: _Result(0, ERROR_REPORT))
    scanner.status()
    first = _failure_log(tmp_path).stat().st_mtime_ns
    writes = []
    monkeypatch.setattr(driver_module.os, "replace", lambda *a: writes.append(a))
    scanner._status_cache = None
    scanner.status()
    assert writes == [] and _failure_log(tmp_path).stat().st_mtime_ns == first


def test_a_different_error_after_an_earlier_one_does_write(tmp_path):
    outputs = iter([ERROR_REPORT, ERROR_REPORT, "reg 0x01 = 0x31\nanother fault\n"])
    scanner = _scanner(tmp_path, lambda *a, **k: _Result(0, next(outputs)))
    for _ in range(3):
        scanner._status_cache = None
        scanner.status()
    assert "another fault" in _failure_log(tmp_path).read_text()


def test_the_temporary_file_is_synced_before_it_replaces_the_log(tmp_path, monkeypatch):
    order = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(driver_module.os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd)))
    monkeypatch.setattr(driver_module.os, "replace", lambda *a: (order.append("replace"), real_replace(*a)))
    _scanner(tmp_path, lambda *a, **k: _Result(0, ERROR_REPORT)).status()
    assert order == ["fsync", "replace"]


def test_a_healthy_doctor_writes_nothing(tmp_path):
    scanner = _scanner(tmp_path, lambda *a, **k: _Result(0, IDLE))
    assert scanner.status() is ScannerStatus.READY
    assert not _failure_log(tmp_path).exists()


def test_the_file_is_replaced_not_grown_and_leaves_no_temporary(tmp_path):
    outputs = iter(["reg 0x01 = 0x31\nfirst failure\n", "reg 0x01 = 0x31\nsecond failure\n"])
    scanner = _scanner(tmp_path, lambda *a, **k: _Result(0, next(outputs)))
    scanner.status()
    scanner._status_cache = None
    scanner.status()
    text = _failure_log(tmp_path).read_text()
    assert "second failure" in text and "first failure" not in text
    assert sorted(p.name for p in (tmp_path / "logs").iterdir()) == ["doctor-last-failure.log"]


def test_a_huge_report_is_trimmed_to_the_doctor_tail(tmp_path):
    big = "reg 0x01 = 0x31\n" + "x" * (DOCTOR_TAIL_BYTES * 2)
    scanner = _scanner(tmp_path, lambda *a, **k: _Result(0, big))
    scanner.status()
    assert _failure_log(tmp_path).stat().st_size <= DOCTOR_TAIL_BYTES + 200


def test_a_log_directory_that_cannot_be_written_never_breaks_the_status(tmp_path, caplog):
    (tmp_path / "logs").write_text("a file, not a directory")
    scanner = _scanner(tmp_path, lambda *a, **k: _Result(0, ERROR_REPORT))
    assert scanner.status() is ScannerStatus.ERROR
    assert "doctor" in caplog.text.lower()


def test_a_held_power_cycle_state_does_not_overwrite_the_evidence(tmp_path):
    scanner = _scanner(tmp_path, lambda *a, **k: _Result(0, IDLE))
    scanner._needs_power_cycle = True  # the first failure left it; doctor itself looks fine
    assert scanner.status() is ScannerStatus.ERROR
    assert not _failure_log(tmp_path).exists()


def test_pruning_the_driver_logs_never_removes_the_evidence(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    evidence = logs / "doctor-last-failure.log"
    evidence.write_text("# kept\n")
    old = time.time() - 10 * driver_module.PROTECT_LOG_S
    for n in range(driver_module.KEEP_LOGS + 3):
        path = logs / f"scan-{n}.log"
        path.write_text("x")
        os.utime(path, (old + n, old + n))
    os.utime(evidence, (old - 100, old - 100))  # the oldest of all
    driver_module._prune_logs(logs)
    assert evidence.exists()


def test_the_real_timeout_path_keeps_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(driver_module, "STATUS_TIMEOUT_S", 1)
    monkeypatch.setattr(driver_module, "STOP_GRACE_S", 1.0)
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR_DELAY", "5")
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "idle")
    scanner = DriverScanner(DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs"))
    assert scanner.status() is ScannerStatus.ERROR
    assert "did not finish" in _failure_log(tmp_path).read_text()


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # the interrupted marker
