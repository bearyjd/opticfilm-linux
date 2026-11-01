"""Capture scan time and the driver's existing evidence around one scanner call."""

import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opticfilm_linux import __version__
from opticfilm_linux.model import HolderKind, RawSweep, ScanSettings
from opticfilm_linux.provenance import ScanEvidence, diagnostics_from_files
from opticfilm_linux.scanner.base import Scanner


def _snapshot(directory: Path | None) -> dict[Path, tuple[int, ...]]:
    result = {}
    if directory is not None:
        for path in directory.glob("*.diag.json"):
            try:
                stat = path.stat()
                result[path] = (
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                )
            except OSError:
                continue
    return result


def _driver_revision(directory: Path | None) -> str | None:
    # Git otherwise searches parents and can report the app's HEAD for an unversioned driver.
    if directory is None or not (directory / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and len(revision) in (40, 64) else None


def capture_scan(
    scanner: Scanner, settings: ScanSettings, *, log_dir: Path | None, driver_dir: Path | None
) -> RawSweep:
    before = _snapshot(log_dir)
    revision = _driver_revision(driver_dir)
    started = datetime.now(UTC)
    tick = time.monotonic()
    sweep = scanner.scan_holder(settings)
    elapsed = time.monotonic() - tick
    # The real backend captures its exact scratch sidecars before cleanup; replay backends
    # may instead write newly-created/changed retained logs during their scan call.
    diagnostics = getattr(scanner, "scan_diagnostics", None)
    if diagnostics is None:
        changed = [
            path for path, identity in _snapshot(log_dir).items() if before.get(path) != identity
        ]
        frames = 4 if sweep.holder == HolderKind.SLIDE4 else 6
        diagnostics = diagnostics_from_files(changed, frames)
    return replace(
        sweep,
        evidence=ScanEvidence(
            started.isoformat(), elapsed, f"opticfilm-linux {__version__}", revision, diagnostics
        ),
    )


class ProvenanceScanner:
    def __init__(self, scanner: Scanner, *, log_dir: Path | None, driver_dir: Path | None):
        self._scanner = scanner
        self._log_dir = log_dir
        self._driver_dir = driver_dir
        self.status = scanner.status
        self.eject = scanner.eject
        # Python 3.12+ runtime protocols inspect attributes statically. Bind only capabilities
        # the backend actually exposes, so wrapping cannot create or hide HolderControl.
        for name in ("load_next_strip", "status_reading", "stop"):
            if hasattr(scanner, name):
                setattr(self, name, getattr(scanner, name))

    def scan_holder(self, settings: ScanSettings) -> RawSweep:
        return capture_scan(
            self._scanner, settings, log_dir=self._log_dir, driver_dir=self._driver_dir
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._scanner, name)
