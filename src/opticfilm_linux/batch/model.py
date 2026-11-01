"""Immutable records describing a batch of trays and the slides in them."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

ROTATIONS = (0, 90, 180, 270)
BRIGHTNESS_RANGE = (-2.0, 2.0)

Mode = Literal["positive", "negative"]
SlideKey = tuple[int, int]  # (tray number, 1-based holder position)


class TrayStage(StrEnum):
    SCANNING = "scanning"
    SCANNED = "scanned"  # raw saved, waiting for enhancement
    ENHANCING = "enhancing"
    REVIEW = "review"
    EXPORTED = "exported"
    FAILED = "failed"


class SlideStatus(StrEnum):
    PENDING = "pending"
    NEEDS_LOOK = "needs_look"
    ACCEPTED = "accepted"
    BLANK = "blank"


class Flag(StrEnum):
    TOO_DARK = "too_dark"
    TOO_BRIGHT = "too_bright"
    CLIPPING = "clipping"
    DUST = "dust"


def _check_orientation(rotation: int, mirror: bool) -> None:
    if type(rotation) is not int or rotation not in ROTATIONS:
        raise ValueError(f"rotation must be one of {ROTATIONS}, got {rotation!r}")
    if type(mirror) is not bool:
        raise ValueError(f"mirror must be true or false, got {mirror!r}")


@dataclass(frozen=True)
class SlideSettings:
    """Per-slide overrides on top of the batch defaults; ``None`` means use the default."""

    rotation: int = 0
    brightness_ev: float = 0.0
    color_strength: float | None = None
    grain_strength: float | None = None
    sharpen_amount: float | None = None
    mirror: bool = False  # flipped left-right after turning, on top of the batch default

    def __post_init__(self) -> None:
        _check_orientation(self.rotation, self.mirror)
        low, high = BRIGHTNESS_RANGE
        if not low <= self.brightness_ev <= high:
            raise ValueError(f"brightness_ev must be between {low} and {high}")
        if self.color_strength is not None and not 0.0 <= self.color_strength <= 1.0:
            raise ValueError("color_strength must be between 0 and 1")
        for name in ("grain_strength", "sharpen_amount"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")


@dataclass(frozen=True)
class SlideRecord:
    position: int  # 1-based holder position
    status: SlideStatus = SlideStatus.PENDING
    flags: tuple[Flag, ...] = ()
    settings: SlideSettings = field(default_factory=SlideSettings)
    owner_decided: bool = False
    exported: bool = False
    preview_stale: bool = False  # settings changed but the preview was not redone yet


@dataclass(frozen=True)
class TrayRecord:
    number: int
    stage: TrayStage
    mode: Mode = "positive"
    sweep_version: int = 1
    slides: tuple[SlideRecord, ...] = ()
    error: str | None = None
    raw_removed: bool = False


@dataclass(frozen=True)
class SlotOrientation:
    """How the slide in one holder slot of every tray is turned, on top of the batch's own.

    Slots are often loaded the same way tray after tray, so this saves fixing each slide by hand.
    It is applied when a slide is rendered, never copied into the slide's own settings.
    """

    position: int  # 1-based holder slot
    rotation: int = 0
    mirror: bool = False

    def __post_init__(self) -> None:
        if type(self.position) is not int or self.position < 1:
            raise ValueError(f"slot numbers start at 1, got {self.position!r}")
        _check_orientation(self.rotation, self.mirror)
        if self.rotation == 0 and not self.mirror:
            raise ValueError(f"slot {self.position} would not change anything; leave it out")


def _check_slots(slots: object) -> None:
    if type(slots) is not tuple or not all(type(s) is SlotOrientation for s in slots):
        raise ValueError("slots must be a tuple of SlotOrientation")
    positions = [s.position for s in slots]
    if any(a >= b for a, b in zip(positions, positions[1:], strict=False)):
        raise ValueError(f"slots must be in order, each slot only once, got {positions}")


@dataclass(frozen=True)
class BatchDefaults:
    dpi: int = 3600
    infrared: bool = True
    mode: Mode = "positive"
    skip_blank: bool = True
    remove_dust: bool = True
    color_strength: float = 1.0
    grain_strength: float = 0.3
    sharpen_amount: float = 0.5
    rotation: int = 0  # how every slide of the batch is turned (see ``options_for``)
    mirror: bool = False  # whether every slide of the batch is flipped left-right
    slots: tuple[SlotOrientation, ...] = ()  # per holder slot, applied after the above

    def __post_init__(self) -> None:
        _check_orientation(self.rotation, self.mirror)
        _check_slots(self.slots)


@dataclass(frozen=True)
class BatchManifest:
    name: str
    album: str = ""
    date: str | None = None
    defaults: BatchDefaults = field(default_factory=BatchDefaults)
    trays: tuple[TrayRecord, ...] = ()
    version: int = 1
