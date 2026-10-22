"""The on-disk batch: an atomically written manifest plus the tray folder layout.

Every change to the manifest goes through ``BatchStore.update``, which holds one lock, writes a
temporary file, flushes it to disk and renames it into place, so a reader or a crash never sees
a partial manifest.
"""

import dataclasses
import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from opticfilm_linux.batch.manifest import from_json, to_json
from opticfilm_linux.batch.model import BatchDefaults, BatchManifest, TrayRecord, TrayStage

MANIFEST_FILE = "batch.json"
INTERRUPTED_SCAN = "scan was interrupted"


class BatchError(ValueError):
    """A batch operation was refused or the batch is missing."""


class BatchStore:
    def __init__(self, directory: Path, manifest: BatchManifest) -> None:
        self.directory = directory
        self._manifest = manifest
        self._lock = threading.Lock()

    @classmethod
    def create(
        cls,
        directory: Path,
        name: str,
        album: str = "",
        date: str | None = None,
        defaults: BatchDefaults | None = None,
    ) -> "BatchStore":
        if (directory / MANIFEST_FILE).exists():
            raise BatchError(f"a batch already exists in {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        manifest = BatchManifest(
            name=name, album=album, date=date, defaults=defaults or BatchDefaults()
        )
        store = cls(directory, manifest)
        store._write(manifest)
        return store

    @classmethod
    def open(cls, directory: Path) -> "BatchStore":
        path = directory / MANIFEST_FILE
        try:
            text = path.read_text()
        except FileNotFoundError as exc:
            raise BatchError(f"batch not found in {directory}") from exc
        return cls(directory, from_json(text))

    def snapshot(self) -> BatchManifest:
        with self._lock:
            return self._manifest

    def update(self, change: Callable[[BatchManifest], BatchManifest]) -> BatchManifest:
        with self._lock:
            new = change(self._manifest)
            self._write(new)
            self._manifest = new
            return new

    def update_tray(self, number: int, **changes) -> BatchManifest:
        """Change fields of one tray (``dataclasses.replace`` semantics)."""

        def change(manifest: BatchManifest) -> BatchManifest:
            if not any(tray.number == number for tray in manifest.trays):
                raise BatchError(f"no tray {number} in this batch")
            trays = tuple(
                dataclasses.replace(tray, **changes) if tray.number == number else tray
                for tray in manifest.trays
            )
            return dataclasses.replace(manifest, trays=trays)

        return self.update(change)

    def tray_dir(self, number: int) -> Path:
        return self.directory / "trays" / f"{number:04d}"

    def sweep_dir(self, number: int, version: int) -> Path:
        return self.tray_dir(number) / f"sweep-{version}"

    def sweep_tmp_dir(self, number: int, version: int) -> Path:
        return self.tray_dir(number) / f"sweep-{version}.tmp"

    def preview_path(self, number: int, position: int, kind: Literal["before", "after"]) -> Path:
        return self.tray_dir(number) / "previews" / f"{position:02d}-{kind}.jpg"

    def recover_interrupted(self) -> tuple[int, ...]:
        """Apply the crash-recovery rules; return the trays waiting for enhancement."""

        def recover(manifest: BatchManifest) -> BatchManifest:
            return dataclasses.replace(
                manifest, trays=tuple(_recovered(tray) for tray in manifest.trays)
            )

        manifest = self.update(recover)
        for tray in manifest.trays:
            self._remove_tmp_sweeps(tray.number)
        return tuple(t.number for t in manifest.trays if t.stage is TrayStage.SCANNED)

    def _remove_tmp_sweeps(self, number: int) -> None:
        tray_dir = self.tray_dir(number)
        if not tray_dir.is_dir():
            return
        for path in tray_dir.glob("sweep-*.tmp"):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)

    def _write(self, manifest: BatchManifest) -> None:
        path = self.directory / MANIFEST_FILE
        tmp = path.with_name(MANIFEST_FILE + ".tmp")
        try:
            with open(tmp, "w") as handle:
                handle.write(to_json(manifest))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def _recovered(tray: TrayRecord) -> TrayRecord:
    if tray.stage is TrayStage.SCANNING:
        return dataclasses.replace(tray, stage=TrayStage.FAILED, error=INTERRUPTED_SCAN)
    if tray.stage is TrayStage.ENHANCING:
        return dataclasses.replace(tray, stage=TrayStage.SCANNED)
    return tray
