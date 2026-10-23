import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from opticfilm_linux.model import HolderKind, ScanSettings
from opticfilm_linux.pipeline.split import split_slides
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner, parse_doctor
from opticfilm_linux.sweepstore import load_sweep

FAKE_DRIVER = Path(__file__).parent / "fake_driver"
PAD = 40


@pytest.fixture
def scanner_for(sweep600_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))

    def build(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return DriverScanner(DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs"))

    return build


def test_scan_holder_stitches_four_frames_into_a_splittable_sweep(scanner_for, sweep600_dir):
    sweep = scanner_for().scan_holder(ScanSettings(dpi=600))
    assert (sweep.dpi, sweep.holder) == (600, HolderKind.SLIDE4)
    original = split_slides(load_sweep(sweep600_dir))
    frame_rows = original[0].rgb.shape[0] + 2 * PAD
    assert sweep.rgb.shape[0] == 4 * frame_rows and sweep.ir.shape == sweep.rgb.shape[:2]
    for k, slide in enumerate(original):
        start = k * frame_rows + PAD
        assert np.array_equal(sweep.rgb[start : start + slide.rgb.shape[0]], slide.rgb)
        assert np.array_equal(sweep.ir[start : start + slide.ir.shape[0]], slide.ir)
    assert len(split_slides(sweep)) == 4


def test_the_command_asks_for_the_four_slide_openings_with_infrared_and_no_inversion(
    scanner_for, tmp_path
):
    log = tmp_path / "argv.json"
    scanner_for(FAKE_DRIVER_ARGV=str(log)).scan_holder(ScanSettings(dpi=3600))
    argv = json.loads(log.read_text())
    assert argv[:1] == ["scan"] and "--ir" in argv and "--positive" not in argv
    assert argv[argv.index("--slides") + 1] == "1-4" and "--frames" not in argv
    assert argv[argv.index("--dark-fallback") + 1] == "default"
    assert argv[argv.index("--dpi") + 1] == "3600" and "--no-cal-data" not in argv


def test_a_nonzero_exit_is_fine_when_the_full_frames_were_written(scanner_for):
    sweep = scanner_for(FAKE_DRIVER_MODE="coverage_fail").scan_holder(ScanSettings(dpi=600))
    assert len(split_slides(sweep)) == 4


def test_a_calibration_refusal_says_to_power_cycle_and_keeps_the_log(scanner_for, tmp_path):
    with pytest.raises(ScanError, match="(?i)power-cycle") as info:
        scanner_for(FAKE_DRIVER_MODE="cal_fail").scan_holder(ScanSettings(dpi=600))
    assert "dark_b" in str(info.value)
    logs = list((tmp_path / "logs").glob("scan-*.log"))
    assert len(logs) == 1 and "dark_b" in logs[0].read_text()


def test_a_missing_driver_is_a_clear_error(tmp_path):
    scanner = DriverScanner(DriverConfig(driver_dir=tmp_path / "nowhere", log_dir=tmp_path))
    with pytest.raises(ScanError, match="driver"):
        scanner.scan_holder(ScanSettings(dpi=600))


def test_status_is_busy_while_a_scan_runs(scanner_for):
    scanner = scanner_for(FAKE_DRIVER_MODE="slow", FAKE_DRIVER_DELAY="1.5")
    thread = threading.Thread(target=scanner.scan_holder, args=(ScanSettings(dpi=600),))
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while scanner.status() is not ScannerStatus.BUSY and time.monotonic() < deadline:
            time.sleep(0.05)
        assert scanner.status() is ScannerStatus.BUSY
    finally:
        thread.join(30)
    assert scanner.status() is not ScannerStatus.BUSY


def test_status_comes_from_the_read_only_doctor_report(scanner_for):
    assert scanner_for(FAKE_DRIVER_DOCTOR="idle").status() is ScannerStatus.READY
    assert scanner_for(FAKE_DRIVER_DOCTOR="cold").status() is ScannerStatus.NOT_READY
    assert scanner_for(FAKE_DRIVER_DOCTOR="absent").status() is ScannerStatus.NOT_READY
    # homed but not loaded (right after an eject): must not offer a scan
    assert scanner_for(FAKE_DRIVER_DOCTOR="unloaded").status() is ScannerStatus.NOT_READY


@pytest.mark.parametrize(
    ("text", "status"),
    [
        (
            "  reg 0x01 = 0x22 (idle-homed)\n  status word: 0xdc55\n  present: True\n",
            ScannerStatus.READY,
        ),
        (
            "  reg 0x01 = 0x22 (idle-homed)\n  status word: 0xf855\n  present: True\n",
            ScannerStatus.NOT_READY,
        ),
        ("  reg 0x01 = 0x22 (idle-homed)\n  present: True\n", ScannerStatus.NOT_READY),
        (
            "  reg 0x01 = 0x00 (cold-never-homed)\n  status word: 0x4855\n  present: True\n",
            ScannerStatus.NOT_READY,
        ),
        (
            "  reg 0x01 = 0x22 (idle-homed)\n  status word: 0xdc55\n  present: False\n",
            ScannerStatus.NOT_READY,
        ),
        ("  reg 0x01 = 0x23\n  status word: 0xdc55\n  present: True\n", ScannerStatus.ERROR),
        ("no such device\n", ScannerStatus.NOT_CONNECTED),
        ("", ScannerStatus.NOT_CONNECTED),
    ],
)
def test_parse_doctor(text, status):
    assert parse_doctor(text) is status


def test_a_scan_waits_for_a_status_check_in_progress(scanner_for, tmp_path):
    trace = tmp_path / "trace.txt"
    scanner = scanner_for(
        FAKE_DRIVER_TRACE=str(trace), FAKE_DRIVER_DOCTOR_DELAY="1.0", FAKE_DRIVER_DOCTOR="idle"
    )
    checker = threading.Thread(target=scanner.status)
    checker.start()
    time.sleep(0.4)  # the doctor process is running now
    scanner.scan_holder(ScanSettings(dpi=600))
    checker.join(10)
    events = [line.split() for line in trace.read_text().splitlines()]
    when = {" ".join(e[1:]): float(e[0]) for e in events}
    assert when["scan start"] >= when["doctor end"]  # the driver allows one session at a time


def test_scratch_files_go_to_the_configured_work_dir_and_are_removed(
    sweep600_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    log = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_DRIVER_ARGV", str(log))
    work = tmp_path / "work"
    config = DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs", work_dir=work)
    DriverScanner(config).scan_holder(ScanSettings(dpi=600))
    argv = json.loads(log.read_text())
    assert Path(argv[argv.index("-o") + 1]).is_relative_to(work)
    assert list(work.iterdir()) == []


def test_the_drivers_calibration_sidecars_are_kept_for_diagnosis(scanner_for, tmp_path):
    scanner_for().scan_holder(ScanSettings(dpi=600))
    kept = sorted(p.name for p in (tmp_path / "logs").glob("*.diag.json"))
    assert len(kept) == 4 and all("-f" in name for name in kept)
