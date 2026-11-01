"""Filesystem evidence used when a raw-cleanup job fails."""

from collections.abc import Collection
from pathlib import Path

from opticfilm_linux.batch.store import BatchStore

RawInventory = dict[Path, int]


def raw_inventory(store: BatchStore, numbers: Collection[int]) -> RawInventory:
    files = {}
    for number in numbers:
        for folder in store.tray_dir(number).glob("sweep-*"):
            paths = (folder,) if folder.is_symlink() else folder.rglob("*")
            for path in paths:
                if path.is_symlink() or path.is_file():
                    files[path] = path.lstat().st_size
    return files


def raw_failure(before: RawInventory, after: RawInventory, *, already_removed: bool = False) -> str:
    if not after and (before or already_removed):
        return "raw data removed; could not save cleanup completion"
    if before == after:
        if already_removed:
            return "raw cleanup incomplete; remaining scans cannot be used"
        return "raw data kept"
    if after:
        return "raw data partly removed; remaining scans cannot be used"
    return "raw data removed; could not save cleanup completion"


def bytes_removed(before: RawInventory, after: RawInventory) -> int:
    return sum(max(0, size - after.get(path, 0)) for path, size in before.items())
