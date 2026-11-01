"""Scan evidence comes from this scan, before temporary driver output disappears."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from opticfilm_linux.app import provenance
from opticfilm_linux.app.provenance import ProvenanceScanner, capture_scan
from opticfilm_linux.model import HolderKind, RawSweep, ScanSettings
from opticfilm_linux.scanner.base import HolderControl, ScannerStatus, StatusReading


def sweep():
    return RawSweep(np.zeros((8, 3, 3), np.uint16), None, 600, HolderKind.SLIDE4)


class MinimalScanner:
    def __init__(self, write=lambda: None):
        self.write = write
        self.calls = []

    def scan_holder(self, settings):
        self.calls.append(settings)
        self.write()
        return sweep()

    def status(self):
        return ScannerStatus.READY

    def eject(self):
        self.calls.append("eject")


def test_capture_uses_scan_time_not_export_time(monkeypatch):
    class Clock:
        @classmethod
        def now(cls, tz):
            assert tz == UTC
            return datetime(2026, 11, 1, 12, tzinfo=tz)

    monkeypatch.setattr(provenance, "datetime", Clock)
    ticks = iter([100.0, 927.0])
    monkeypatch.setattr(provenance.time, "monotonic", lambda: next(ticks))
    scanner = MinimalScanner()
    settings = ScanSettings(dpi=600)
    result = capture_scan(scanner, settings, log_dir=None, driver_dir=None)
    assert scanner.calls == [settings]
    assert result.evidence.digitized_at == "2026-11-01T12:00:00+00:00"
    assert result.evidence.duration_s == 827.0
    assert result.evidence.software.startswith("opticfilm-linux ")
    assert result.evidence.driver_revision is None


def test_diagnostics_are_scan_specific(tmp_path):
    stale = tmp_path / "20261101-120000-tray-f1.diag.json"
    stale.write_text('{"frame":1,"old":true}')
    old_stamp = stale.stat().st_mtime_ns
    (tmp_path / "20261031-120000-tray-f2.diag.json").write_text('{"stale":true}')

    def write():
        # Same name and timestamp, different inode: still this scan's evidence.
        new = tmp_path / "replacement"
        new.write_text('{"frame":1,"gain_codes":[1,2,3]}')
        os.utime(new, ns=(old_stamp, old_stamp))
        new.replace(stale)
        (tmp_path / "20261101-120001-tray-f2.diag.json").write_text('{"frame":2}')
        (tmp_path / "20261101-120001-unrelated-f3.diag.json").write_text('{"frame":3}')

    result = capture_scan(MinimalScanner(write), ScanSettings(), log_dir=tmp_path, driver_dir=None)
    assert [json.loads(x) for x in result.evidence.diagnostics] == [
        {"frame": 1, "gain_codes": [1, 2, 3]},
        {"frame": 2},
        {},
        {},
    ]


def test_missing_diagnostics_are_unknown(tmp_path):
    result = capture_scan(MinimalScanner(), ScanSettings(), log_dir=tmp_path, driver_dir=tmp_path)
    assert result.evidence.diagnostics == ("{}",) * 4
    assert result.evidence.driver_revision is None  # no accidental parent application HEAD
    assert sweep().evidence is None


def test_malformed_and_ambiguous_diagnostics_are_unknown(tmp_path, caplog):
    def write():
        (tmp_path / "20261101-120000-tray-f1.diag.json").write_text("{")
        (tmp_path / "20261101-120000-tray-f2.diag.json").write_text('{"frame":2}')
        (tmp_path / "20261101-120001-tray-f2.diag.json").write_text('{"frame":2}')
        (tmp_path / "20261101-120000-tray-f3.diag.json").write_text('{"frame":4}')
        (tmp_path / "20261101-120000-tray-f4.diag.json").write_text("[]")

    result = capture_scan(MinimalScanner(write), ScanSettings(), log_dir=tmp_path, driver_dir=None)
    assert result.evidence.diagnostics == ("{}",) * 4
    assert "diagnostic" in caplog.text.lower()


def test_driver_revision_is_the_configured_checkout(tmp_path):
    driver = tmp_path / "driver"
    driver.mkdir()
    subprocess.run(["git", "init", "-q", str(driver)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(driver),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "driver",
            "--allow-empty",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(driver), "rev-parse", "HEAD"], text=True
    ).strip()
    result = capture_scan(MinimalScanner(), ScanSettings(), log_dir=None, driver_dir=driver)
    assert result.evidence.driver_revision == revision
    # A non-repository child must not silently report its parent's revision.
    nested = driver / "not-a-checkout"
    nested.mkdir()
    result = capture_scan(MinimalScanner(), ScanSettings(), log_dir=None, driver_dir=nested)
    assert result.evidence.driver_revision is None


def test_wrapper_preserves_optional_capabilities():
    class Capable(MinimalScanner):
        def load_next_strip(self):
            self.calls.append("load")

        def stop(self):
            self.calls.append("stop")

        def status_reading(self):
            return StatusReading(ScannerStatus.READY, True)

    for scanner in [MinimalScanner(), Capable()]:
        wrapped = ProvenanceScanner(scanner, log_dir=None, driver_dir=None)
        assert isinstance(wrapped, HolderControl) == isinstance(scanner, HolderControl)
        assert hasattr(wrapped, "stop") == hasattr(scanner, "stop")
        assert wrapped.status() == scanner.status()
        wrapped.eject()
        assert scanner.calls == ["eject"]
        if isinstance(scanner, Capable):
            wrapped.load_next_strip()
            wrapped.stop()
            assert wrapped.status_reading() == StatusReading(ScannerStatus.READY, True)
        assert wrapped.scan_holder(ScanSettings()).evidence is not None


def test_driver_captures_current_scratch_diagnostics_without_logs(
    monkeypatch, tmp_path, sweep600_dir
):
    from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner

    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    config = DriverConfig(
        driver_dir=Path(__file__).parent / "fake_driver", work_dir=tmp_path / "work"
    )
    scanner = ProvenanceScanner(DriverScanner(config), log_dir=None, driver_dir=config.driver_dir)
    result = scanner.scan_holder(ScanSettings(dpi=600))
    assert [json.loads(x) for x in result.evidence.diagnostics] == [
        {"frame": n} for n in range(1, 5)
    ]
    assert list(config.work_dir.iterdir()) == []


def test_scan_error_is_preserved():
    class Broken(MinimalScanner):
        def scan_holder(self, settings):
            raise RuntimeError("original failure")

    with pytest.raises(RuntimeError, match="original failure"):
        capture_scan(Broken(), ScanSettings(), log_dir=None, driver_dir=None)
