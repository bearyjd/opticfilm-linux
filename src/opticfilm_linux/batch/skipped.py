"""Holder openings left out of a scan on purpose: which openings to scan, and their records.

A skipped opening keeps its slide record, as ``SlideStatus.SKIPPED``, so its slide number
(section start + holder position) stays reserved and a later rescan lands in its place. The
records are written when the scan starts, so a manifest with skipped openings always holds a
``"skipped"`` slide status, which an older build refuses to load (it would otherwise misread
the tray). That guarantee covers box/section numbering only: the flat layout numbers files in
export order and reserves nothing, as for a blank window.
"""

from opticfilm_linux.batch.model import (
    SlideRecord,
    SlideStatus,
    check_skipped,
)
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.model import ALL_POSITIONS, RawSweep, ScanSettings
from opticfilm_linux.scanner.base import ScanError

ALL_EMPTY = "Every opening is marked empty, so there is nothing to scan. Eject the holder instead."


def scan_positions(skipped: tuple[int, ...]) -> tuple[int, ...]:
    """The openings to scan; refuses a bad list, and an empty one ("all openings empty")."""
    if type(skipped) is tuple and set(skipped) == set(ALL_POSITIONS):
        raise BatchError(ALL_EMPTY)
    try:
        check_skipped(skipped)
    except ValueError as exc:
        raise BatchError(str(exc)) from exc
    return tuple(p for p in ALL_POSITIONS if p not in skipped)


def _skipped_record(position: int, old: SlideRecord | None) -> SlideRecord:
    # On a rescan the old record was already reset to its delivery history (``exports``);
    # nothing else of the earlier scan belongs to an opening that is not scanned now.
    # Its description and date are the owner's and stay.
    if old is None:
        return SlideRecord(position, status=SlideStatus.SKIPPED)
    return SlideRecord(position, status=SlideStatus.SKIPPED, exports=old.exports, meta=old.meta)


def mark_skipped(
    slides: tuple[SlideRecord, ...], skipped: tuple[int, ...]
) -> tuple[SlideRecord, ...]:
    """``slides`` with each skipped opening recorded as SKIPPED, in position order."""
    current = {s.position: s for s in slides}
    marked = {p: _skipped_record(p, current.get(p)) for p in skipped}
    return tuple(sorted({**current, **marked}.values(), key=lambda s: s.position))


def drop_skipped_previews(store: BatchStore, number: int, skipped: tuple[int, ...]) -> None:
    """An opening that is not scanned shows nothing, never the picture of an earlier scan."""
    for position in skipped:
        for path in (
            store.preview_path(number, position, "before"),
            store.preview_path(number, position, "after"),
            store.master_path(number, position),
        ):
            path.unlink(missing_ok=True)


def check_scanned_positions(sweep: RawSweep, settings: ScanSettings) -> None:
    """The scanner must return exactly the openings asked for: a skipped one is never scanned."""
    if sweep.positions != settings.positions:
        raise ScanError(
            f"the scan returned openings {sweep.positions} but openings {settings.positions} "
            "were requested; the tray was not recorded. Scan it again."
        )
