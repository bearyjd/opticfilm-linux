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


def raw_failure(
    store: BatchStore, numbers: Collection[int], before: RawInventory, after: RawInventory
) -> str:
    trays = {t.number: t for t in store.snapshot().trays}
    outcomes = []
    for number in sorted(set(numbers)):
        directory = store.tray_dir(number)
        prior = {p: size for p, size in before.items() if p.is_relative_to(directory)}
        current = {p: size for p, size in after.items() if p.is_relative_to(directory)}
        removed = number in trays and trays[number].raw_removed
        folders = any(directory.glob("sweep-*"))
        outcomes.append((number, _tray_failure(prior, current, removed, folders)))
    if len(outcomes) == 1:
        return outcomes[0][1]
    summary = "; ".join(f"Tray {number}: {outcome}" for number, outcome in outcomes)
    if before != after and after:
        return "some raw data removed; remaining raw files are still on disk. " + summary
    return summary


def _tray_failure(before: RawInventory, after: RawInventory, removed: bool, folders: bool) -> str:
    if not folders:
        if not before:
            return (
                "raw data removed before this job; cleanup completion saved"
                if removed
                else "no raw files were present before cleanup; sweep folders are absent"
            )
        if removed:
            return "raw data removed; cleanup completion saved"
        return (
            "raw data removed; could not save cleanup completion; "
            "manifest still marks the raw scans as available but sweep folders are absent"
        )
    if removed:
        outcome = (
            "raw cleanup incomplete; removal pending; raw still present; "
            "manifest marks the raw scans as freed but sweep folders remain"
        )
        if after:
            if before != after:
                outcome += "; raw data partly removed"
            outcome += "; remaining scans cannot be used"
        return outcome
    if not after:
        return "raw cleanup incomplete; sweep folders remain without raw files"
    if before != after:
        return "raw data partly removed; remaining scans cannot be used"
    return "raw data kept"


def bytes_removed(before: RawInventory, after: RawInventory) -> int:
    return sum(max(0, size - after.get(path, 0)) for path, size in before.items())
