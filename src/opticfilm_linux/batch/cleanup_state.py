"""Shared cleanup state: the one cleanup lock, pending rollbacks, and cleanup exceptions.

Both export (which deletes raw) and cleanup_recovery (which restores it) need the same lock,
pending-reset registry, sweep-folder guard and refusal types. Keeping them here lets each
import this module instead of reaching into the other's private names.
"""

import threading
import weakref
from pathlib import Path

from opticfilm_linux.batch.model import BatchManifest, TrayRecord
from opticfilm_linux.batch.store import BatchError, BatchStore

# A single module-level lock: every cleanup, release, rescan and recovery serializes on it.
CLEANUP_LOCK = threading.Lock()
PENDING_CLEANUP_RESETS: weakref.WeakKeyDictionary[
    BatchStore, dict[int, tuple[BatchManifest, bool]]
] = weakref.WeakKeyDictionary()


class CleanupRefused(BatchError):
    """Raw scans were not removed: the tray is not exported, or a path looked unsafe."""


class CleanupPolicyRefused(CleanupRefused):
    """Cleanup must wait for owner policy, review, or an unfinished application action."""


class CleanupRetryRequired(CleanupRefused):
    """Fresh evidence was superseded by a concurrent change; retry from a new snapshot."""


class ExportVerificationFailed(BatchError):
    """A particular slide failed its durable export verification."""

    def __init__(self, number: int, position: int, reason: Exception):
        self.position = position
        super().__init__(f"tray {number} slide {position}: verification failed: {reason}")


def tray_of(manifest: BatchManifest, number: int) -> TrayRecord:
    return next(t for t in manifest.trays if t.number == number)


def checked_sweep_folders(store: BatchStore, number: int) -> list[Path]:
    root = store.directory.resolve()
    tray_dir = store.tray_dir(number)
    if tray_dir.is_symlink() or not tray_dir.resolve().is_relative_to(root):
        raise CleanupRefused(f"tray folder {tray_dir} is a symlink or outside the batch")
    folders = sorted(tray_dir.glob("sweep-*"))
    for path in folders:
        if path.is_symlink():
            raise CleanupRefused(f"{path} is a symlink; refusing to delete through it")
        if not path.is_dir() or not path.resolve().is_relative_to(root):
            raise CleanupRefused(f"{path} is not a sweep folder inside the batch")
    return folders


def checked_sweep_versions(tray: TrayRecord, folders: tuple[Path, ...]) -> None:
    if any(
        not p.name.removeprefix("sweep-").isdigit()
        or int(p.name.removeprefix("sweep-")) > tray.sweep_version
        for p in folders
    ):
        raise CleanupRefused("raw sweep versions changed; re-export before freeing raw")
    if tray.raw_removed and tray.cleanup_sweeps:
        if any(int(p.name.removeprefix("sweep-")) not in tray.cleanup_sweeps for p in folders):
            raise CleanupRefused("unverified raw sweep appeared during interrupted removal")
