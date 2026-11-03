"""Pure selection gestures and session-local undo records, without Qt dependencies."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType

from opticfilm_linux.batch.model import (
    BatchManifest,
    SlideKey,
    SlideSettings,
    SlideStatus,
    TrayStage,
)

SlideSnapshot = tuple[SlideSettings, SlideStatus, bool]


@dataclass(frozen=True)
class Selection:
    keys: frozenset[SlideKey] = frozenset()
    anchor: SlideKey | None = None
    focus: SlideKey | None = None
    trays: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "keys", frozenset(self.keys))
        object.__setattr__(self, "trays", frozenset(self.trays))


@dataclass(frozen=True)
class UndoEntry:
    label: str
    before: Mapping[SlideKey, SlideSnapshot]

    def __post_init__(self) -> None:
        object.__setattr__(self, "before", MappingProxyType(dict(self.before)))


def selectable_keys(manifest: BatchManifest) -> tuple[SlideKey, ...]:
    """Return display order, including blanks and excluding trays still processing."""
    busy = (TrayStage.SCANNING, TrayStage.SCANNED, TrayStage.ENHANCING)
    return tuple(
        (tray.number, slide.position)
        for tray in sorted(manifest.trays, key=lambda tray: tray.number)
        if tray.stage not in busy
        for slide in sorted(tray.slides, key=lambda slide: slide.position)
    )


def click(sel: Selection, key: SlideKey) -> Selection:
    return Selection(frozenset({key}), key, key)


def toggle(sel: Selection, key: SlideKey) -> Selection:
    return Selection(sel.keys ^ {key}, key, key, sel.trays)


def extend(
    sel: Selection,
    key: SlideKey,
    order: Sequence[SlideKey],
    additive: bool = False,
) -> Selection:
    """Select an inclusive anchor range; a vanished anchor starts a new range."""
    if key not in order:
        return replace(sel)
    anchor = sel.anchor if sel.anchor in order else key
    low, high = sorted((order.index(anchor), order.index(key)))
    keys = frozenset(order[low : high + 1])
    return Selection(
        keys | sel.keys if additive else keys, anchor, key, sel.trays if additive else frozenset()
    )


def select_all(order: Sequence[SlideKey]) -> Selection:
    first = order[0] if order else None
    return Selection(frozenset(order), first, first)


def clear() -> Selection:
    return Selection()


def select_tray(
    sel: Selection, n: int, order: Sequence[SlideKey], additive: bool = False
) -> Selection:
    keys = tuple(key for key in order if key[0] == n)
    if not keys:
        return replace(sel) if additive else clear()
    selected = select_all(keys)
    return Selection(
        selected.keys | sel.keys if additive else selected.keys,
        selected.anchor,
        selected.focus,
        sel.trays if additive else frozenset(),
    )


def select_where(order: Sequence[SlideKey], pred: Callable[[SlideKey], bool]) -> Selection:
    return select_all(tuple(key for key in order if pred(key)))


def move_focus(
    sel: Selection, delta: int, order: Sequence[SlideKey], extend: bool = False
) -> Selection:
    """Move in display order, clamping at either end; Shift keeps the anchor."""
    if not order:
        return clear()
    if sel.focus not in order:
        key = order[-1] if delta < 0 else order[0]
    else:
        index = max(0, min(len(order) - 1, order.index(sel.focus) + delta))
        key = order[index]
    if extend:
        # The argument is named for the gesture; the function performs its range.
        return _extend_focus(sel, key, order)
    return click(sel, key)


def _extend_focus(sel: Selection, key: SlideKey, order: Sequence[SlideKey]) -> Selection:
    return extend(sel, key, order)


def prune(sel: Selection, order: Sequence[SlideKey]) -> Selection:
    available = frozenset(order)
    return Selection(
        sel.keys & available,
        sel.anchor if sel.anchor in available else None,
        sel.focus if sel.focus in available else None,
        sel.trays,
    )


def push_undo(stack: tuple[UndoEntry, ...], entry: UndoEntry) -> tuple[UndoEntry, ...]:
    return (*stack[-19:], entry)


def pop_undo(
    stack: tuple[UndoEntry, ...],
) -> tuple[tuple[UndoEntry, ...], UndoEntry | None]:
    return (stack[:-1], stack[-1]) if stack else ((), None)


def filter_matches(tray, slide, name: str) -> bool:
    """Use identical predicates for contact-sheet filters and their counters."""
    if name == "flagged":
        return (
            bool(slide.flags)
            or slide.status is SlideStatus.BLANK
            or slide.position in tray.auto_flagged
        )
    return (
        slide.position in tray.auto_failed
        or tray.stage is TrayStage.FAILED
        or (tray.auto == "failed" and not tray.auto_failed)
    )


def failed_empty_trays(trays) -> frozenset[int]:
    return frozenset(
        tray.number
        for tray in trays
        if not tray.slides and (tray.stage is TrayStage.FAILED or tray.auto == "failed")
    )
