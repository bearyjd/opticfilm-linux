"""Pure physical position allocation, independent of review and export order."""

from dataclasses import replace
from pathlib import Path

from opticfilm_linux.batch.model import BatchManifest, TrayLocation, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchError, BatchStore


def allocate_location(manifest: BatchManifest, capacity: int) -> TrayLocation | None:
    if type(capacity) is not int or capacity not in (4, 6):
        raise ValueError("capacity must be 4 or 6")
    if not manifest.collection:
        return None
    ends = [
        t.location.start + t.location.capacity - 1
        for t in manifest.trays
        if t.location is not None
        and (t.location.collection, t.location.box, t.location.section)
        == (manifest.collection, manifest.box, manifest.section)
    ]
    start = 1 + max(ends, default=0)
    if start > manifest.section_size:
        raise BatchError("This section is full; choose Next section or Finish box before scanning.")
    return TrayLocation(manifest.collection, manifest.box, manifest.section, start, capacity)


def slide_number(location: TrayLocation, position: int) -> int:
    if type(position) is not int or not 1 <= position <= location.capacity:
        raise ValueError("position must be within the holder capacity")
    return location.start + position - 1


def section_directory(root: Path, location: TrayLocation) -> Path:
    return root / location.collection / f"box-{location.box}" / location.section


def location_is_active(manifest: BatchManifest, tray: TrayRecord) -> bool:
    location = tray.location
    return location is None or (location.collection, location.box, location.section) == (
        manifest.collection,
        manifest.box,
        manifest.section,
    )


def advance_section(store: BatchStore, *, next_box: bool) -> None:
    """Close the current section atomically; every subsequent scan starts a new tray."""

    def change(manifest: BatchManifest) -> BatchManifest:
        if not manifest.collection:
            raise BatchError("This batch has no collection; create a collection batch first.")
        if any(
            t.stage in (TrayStage.SCANNING, TrayStage.SCANNED, TrayStage.ENHANCING)
            for t in manifest.trays
        ):
            raise BatchError(
                "Wait for scanning and enhancement to finish before closing a section."
            )
        numeric = manifest.section.isdigit()
        if next_box:
            return replace(manifest, box=manifest.box + 1, section="01" if numeric else "a")
        if manifest.section in ("f", "99"):
            raise BatchError("This is the last section; choose Finish box to start another box.")
        section = f"{int(manifest.section) + 1:02d}" if numeric else chr(ord(manifest.section) + 1)
        return replace(manifest, section=section)

    store.update(change)
