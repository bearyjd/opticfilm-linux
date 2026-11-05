"""Holder openings left out of a scan on purpose: which openings to scan, and their records.

A skipped opening keeps its slide record, as ``SlideStatus.SKIPPED``, so its slide number
(section start + holder position) stays reserved and a later rescan lands in its place.
"""

from opticfilm_linux.batch.model import (
    SlideRecord,
    SlideStatus,
    TrayRecord,
    check_skipped,
)
from opticfilm_linux.batch.store import BatchError
from opticfilm_linux.model import ALL_POSITIONS

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
    if old is None:
        return SlideRecord(position, status=SlideStatus.SKIPPED)
    # Owner settings and the delivery history stay; everything about the old raw goes.
    return SlideRecord(
        position,
        status=SlideStatus.SKIPPED,
        settings=old.settings,
        exports=old.exports,
        meta=old.meta,
    )


def mark_skipped(tray: TrayRecord) -> tuple[SlideRecord, ...]:
    """The tray's slide records with each skipped opening recorded as SKIPPED, by position."""
    current = {s.position: s for s in tray.slides}
    marked = {p: _skipped_record(p, current.get(p)) for p in tray.skipped}
    return tuple(sorted({**current, **marked}.values(), key=lambda s: s.position))
