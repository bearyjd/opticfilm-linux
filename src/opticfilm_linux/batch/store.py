"""The on-disk batch: an atomically written manifest plus the tray folder layout.

Every change to the manifest goes through ``BatchStore.update``, which holds one lock, writes a
temporary file, flushes it to disk and renames it into place, so a reader or a crash never sees
a partial manifest.
"""

import dataclasses
import fcntl
import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from opticfilm_linux.batch.manifest import from_json, to_json
from opticfilm_linux.batch.model import BatchDefaults, BatchManifest, TrayRecord, TrayStage
from opticfilm_linux.scanner.interrupted import INTERRUPTED_TEXT

MANIFEST_FILE = "batch.json"
LOCK_FILE = "batch.lock"
INTERRUPTED_SCAN = INTERRUPTED_TEXT  # the flag of a tray whose scan was cut off


class BatchError(ValueError):
    """A batch operation was refused or the batch is missing."""


class BatchLocked(BatchError):
    """Another program or window holds this batch open for writing."""


class BatchStore:
    """One batch folder. A writer holds an exclusive lock on the folder for as long as it is open,
    so two programs can never both change the manifest; readers open it ``readonly`` and always
    see the latest manifest on disk."""

    def __init__(self, directory: Path, manifest: BatchManifest, lock_file=None) -> None:
        self.directory = directory
        self._manifest = manifest
        self._lock_file = lock_file
        self._lock = threading.Lock()

    @property
    def readonly(self) -> bool:
        return self._lock_file is None

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
        lock_file = _acquire_lock(directory)
        manifest = BatchManifest(
            name=name, album=album, date=date, defaults=defaults or BatchDefaults()
        )
        store = cls(directory, manifest, lock_file)
        try:
            store._write(manifest)
        except BaseException:
            store.close()
            raise
        return store

    @classmethod
    def open(cls, directory: Path, readonly: bool = False) -> "BatchStore":
        if not (directory / MANIFEST_FILE).is_file():
            raise BatchError(f"batch not found in {directory}")
        lock_file = None if readonly else _acquire_lock(directory)
        try:
            return cls(directory, _read_manifest(directory), lock_file)
        except BaseException:
            _release_lock(lock_file)
            raise

    def close(self) -> None:
        """Release the writer lock (a no-op for read-only stores)."""
        _release_lock(self._lock_file)
        self._lock_file = None

    def __enter__(self) -> "BatchStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def snapshot(self) -> BatchManifest:
        if self.readonly:
            return _read_manifest(self.directory)
        with self._lock:
            return self._manifest

    def update(self, change: Callable[[BatchManifest], BatchManifest]) -> BatchManifest:
        if self.readonly:
            raise BatchError("this batch is open read-only")
        with self._lock:
            new = change(self._manifest)
            if new != self._manifest:
                self._write(new)
                self._manifest = new
            return new

    def update_tray_if(
        self, number: int, condition: Callable[[TrayRecord], bool], **changes
    ) -> bool:
        """Change one tray only if ``condition`` holds for it *now* (checked under the lock)."""
        applied = False

        def change(manifest: BatchManifest) -> BatchManifest:
            nonlocal applied
            tray = next((t for t in manifest.trays if t.number == number), None)
            if tray is None:
                raise BatchError(f"no tray {number} in this batch")
            if not condition(tray):
                return manifest
            applied = True
            trays = tuple(
                dataclasses.replace(t, **changes) if t.number == number else t
                for t in manifest.trays
            )
            return dataclasses.replace(manifest, trays=trays)

        self.update(change)
        return applied

    def update_tray(self, number: int, **changes) -> BatchManifest:
        """Change fields of one tray (``dataclasses.replace`` semantics)."""
        self.update_tray_if(number, lambda _tray: True, **changes)
        return self.snapshot()

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


def _read_manifest(directory: Path) -> BatchManifest:
    try:
        return from_json((directory / MANIFEST_FILE).read_text())
    except FileNotFoundError as exc:
        raise BatchError(f"batch not found in {directory}") from exc


def _acquire_lock(directory: Path):
    handle = open(directory / LOCK_FILE, "w")  # noqa: SIM115 - held until the store closes
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise BatchLocked(
            f"this batch is in use by another program or window; close it first ({directory})"
        ) from exc
    return handle


def _release_lock(handle) -> None:
    if handle is not None:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def is_interrupted(tray: TrayRecord) -> bool:
    """A tray whose scan was cut off: whatever it holds is partial, and only a rescan clears it."""
    return tray.stage is TrayStage.FAILED and (tray.error or "").startswith(INTERRUPTED_SCAN)


def _recovered(tray: TrayRecord) -> TrayRecord:
    if tray.stage is TrayStage.SCANNING:
        return dataclasses.replace(tray, stage=TrayStage.FAILED, error=INTERRUPTED_SCAN)
    if tray.stage is TrayStage.ENHANCING:
        return dataclasses.replace(tray, stage=TrayStage.SCANNED)
    return tray
