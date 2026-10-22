"""Immutable records describing a batch of trays and the slides in them."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

ROTATIONS = (0, 90, 180, 270)
BRIGHTNESS_RANGE = (-2.0, 2.0)

Mode = Literal["positive", "negative"]


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


@dataclass(frozen=True)
class SlideSettings:
    """Per-slide overrides on top of the batch defaults; ``None`` means use the default."""

    rotation: int = 0
    brightness_ev: float = 0.0
    color_strength: float | None = None
    grain_strength: float | None = None
    sharpen_amount: float | None = None

    def __post_init__(self) -> None:
        if self.rotation not in ROTATIONS:
            raise ValueError(f"rotation must be one of {ROTATIONS}, got {self.rotation!r}")
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
class BatchDefaults:
    dpi: int = 3600
    infrared: bool = True
    mode: Mode = "positive"
    skip_blank: bool = True
    remove_dust: bool = True
    color_strength: float = 1.0
    grain_strength: float = 0.3
    sharpen_amount: float = 0.5


@dataclass(frozen=True)
class BatchManifest:
    name: str
    album: str = ""
    date: str | None = None
    defaults: BatchDefaults = field(default_factory=BatchDefaults)
    trays: tuple[TrayRecord, ...] = ()
    version: int = 1
