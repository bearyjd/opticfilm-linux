"""Holder control on the driver backend: eject and the non-interactive next-strip load."""

import json
import threading
import time
from pathlib import Path

import pytest

from opticfilm_linux.model import ScanSettings
from opticfilm_linux.scanner.base import HolderControl, ScanError, Scanner, ScannerStatus
from opticfilm_linux.scanner.driver import TERMINAL_LOAD_HINT, DriverConfig, DriverScanner
from opticfilm_linux.scanner.fake import FakeScanner

FAKE_DRIVER = Path(__file__).parent / "fake_driver"


@pytest.fixture
def make(sweep600_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))

    def build(**env: str) -> DriverScanner:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return DriverScanner(DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs"))

    return build


def test_both_backends_offer_holder_control_and_stay_scanners(sweep600_dir, make):
    for scanner in (make(), FakeScanner(sweep600_dir)):
        assert isinstance(scanner, HolderControl) and isinstance(scanner, Scanner)
    assert FakeScanner(sweep600_dir).load_next_strip() is None


def test_load_next_strip_runs_the_non_interactive_load(make, tmp_path):
    argv = tmp_path / "argv.json"
    make(FAKE_DRIVER_ARGV=str(argv)).load_next_strip()
    assert json.loads(argv.read_text()) == ["load", "--next-strip"]
    assert any(p.name.startswith("load-") for p in (tmp_path / "logs").iterdir())


def test_load_next_strip_makes_the_next_status_check_ask_again(make, monkeypatch):
    scanner = make(FAKE_DRIVER_DOCTOR="unloaded")
    assert scanner.status() is ScannerStatus.NOT_READY
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "idle")
    scanner.load_next_strip()
    assert scanner.status() is ScannerStatus.READY  # not the cached NOT_READY


def test_a_failed_load_says_how_to_load_and_asks_for_a_power_cycle(make, monkeypatch):
    scanner = make(FAKE_DRIVER_DOCTOR="unloaded", FAKE_DRIVER_LOAD_EXIT="1")
    with pytest.raises(ScanError) as caught:
        scanner.load_next_strip()
    text = str(caught.value)
    assert TERMINAL_LOAD_HINT in text and "no strip found" in text
    assert "of135i load" in TERMINAL_LOAD_HINT
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "idle")
    assert scanner.status() is ScannerStatus.ERROR  # looks loaded, but the driver failed
    monkeypatch.setenv("FAKE_DRIVER_DOCTOR", "cold")  # the power cycle happened
    scanner._status_cache = None
    assert scanner.status() is ScannerStatus.NOT_READY


def test_a_load_is_busy_for_status_and_never_overlaps_a_scan(make, tmp_path):
    trace = tmp_path / "trace.txt"
    scanner = make(FAKE_DRIVER_TRACE=str(trace), FAKE_DRIVER_LOAD_DELAY="1.0")
    loading = threading.Thread(target=scanner.load_next_strip)
    loading.start()
    deadline = time.monotonic() + 10
    while "load start" not in (trace.read_text() if trace.exists() else ""):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert scanner.status() is ScannerStatus.BUSY
    scanner.scan_holder(ScanSettings(dpi=600))  # waits for the load to finish
    loading.join()
    events = [line.split(" ", 1)[1] for line in trace.read_text().splitlines()]
    assert events.index("load end") < events.index("scan start")
