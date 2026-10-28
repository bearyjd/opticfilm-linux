"""Per-slot default orientation: composing orientations, building and parsing slot defaults.

An orientation is ``(rotation, mirror)``: turn clockwise by ``rotation``, THEN flip left-right if
``mirror``. Slot defaults sit between the batch's orientation and each slide's own.
"""

import argparse
from collections.abc import Iterable, Mapping

from opticfilm_linux.batch.model import ROTATIONS, BatchDefaults, SlotOrientation

Orientation = tuple[int, bool]
IDENTITY: Orientation = (0, False)
SLOT_ROTATE_FORMAT = "SLOT=DEGREES[,SLOT=DEGREES...], e.g. 2=180,3=90"


def compose(first: Orientation, then: Orientation) -> Orientation:
    """``first`` followed by ``then`` (applied to the image as shown), in the same normal form.

    A turn made after a flip runs the other way underneath, hence the sign.
    """
    sign = -1 if first[1] else 1
    return (first[0] + sign * then[0]) % 360, first[1] != then[1]


def slot_orientation(defaults: BatchDefaults, position: int) -> Orientation:
    slot = next((s for s in defaults.slots if s.position == position), None)
    return IDENTITY if slot is None else (slot.rotation, slot.mirror)


def slot_orientations(
    rotations: Mapping[int, int], mirrored: Iterable[int]
) -> tuple[SlotOrientation, ...]:
    """The canonical (ordered, no do-nothing entries) slots for these choices."""
    mirrors = set(mirrored)
    positions = sorted(set(rotations) | mirrors)
    choices = ((p, rotations.get(p, 0), p in mirrors) for p in positions)
    return tuple(SlotOrientation(p, r, m) for p, r, m in choices if (r, m) != IDENTITY)


def _slot_number(text: str) -> int:
    try:
        number = int(text.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"slot numbers must be whole numbers, got {text!r}"
        ) from exc
    if number < 1:
        raise argparse.ArgumentTypeError(f"slot numbers start at 1, got {number}")
    return number


def _once(numbers: list[int]) -> None:
    repeated = next((n for i, n in enumerate(numbers) if n in numbers[:i]), None)
    if repeated is not None:
        raise argparse.ArgumentTypeError(f"slot {repeated} is given twice")


def parse_slot_rotations(text: str) -> dict[int, int]:
    """``"2=180,3=90"`` -> ``{2: 180, 3: 90}``; an argparse ``type=``."""
    pairs: list[tuple[int, int]] = []
    for item in text.split(","):
        slot, sep, degrees = item.partition("=")
        if not sep or not slot.strip().isdigit():
            raise argparse.ArgumentTypeError(f"expected {SLOT_ROTATE_FORMAT}, got {text!r}")
        rotation = degrees.strip()
        if not rotation.isdigit() or int(rotation) not in ROTATIONS:
            raise argparse.ArgumentTypeError(
                f"slot {slot.strip()}: degrees must be 0, 90, 180 or 270, got {rotation!r}"
            )
        pairs.append((_slot_number(slot), int(rotation)))
    _once([slot for slot, _ in pairs])
    return dict(pairs)


def parse_slot_positions(text: str) -> tuple[int, ...]:
    """``"1,3"`` -> ``(1, 3)``; an argparse ``type=``."""
    numbers = [_slot_number(item) for item in text.split(",")]
    _once(numbers)
    return tuple(numbers)
