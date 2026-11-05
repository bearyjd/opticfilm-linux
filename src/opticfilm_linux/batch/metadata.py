"""Batch description and estimated date, and how they pass down batch -> tray -> slide.

Every date is an estimate with two precisions, a year or a year and month. Each of the two fields
is set or inherited on its own: ``None`` means inherit, ``""`` means explicitly blank at that
level, anything else is the value.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from opticfilm_linux.batch.model import BatchManifest, SlideRecord, TrayRecord

MAX_DESCRIPTION = 500
EARLIEST_YEAR = 1900
LATEST_YEAR_BACK = 2  # the latest accepted year is this year minus 2, as heirloom guards it

Level = Literal["batch", "tray", "slide", ""]

_DATE = re.compile(r"([0-9]{4})(?:-([0-9]{2}))?", re.ASCII)
_LEGACY_DAY = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", re.ASCII)


def latest_year() -> int:
    return date.today().year - LATEST_YEAR_BACK


@dataclass(frozen=True)
class EstimatedDate:
    year: int
    month: int | None = None

    def __post_init__(self) -> None:
        if type(self.year) is not int or not EARLIEST_YEAR <= self.year <= latest_year():
            raise ValueError(f"year must be between {EARLIEST_YEAR} and {latest_year()}")
        if self.month is not None and (type(self.month) is not int or not 1 <= self.month <= 12):
            raise ValueError("month must be between 1 and 12")

    @classmethod
    def parse(cls, text: str) -> EstimatedDate:
        match = _DATE.fullmatch(text) if isinstance(text, str) else None
        if match is None:
            raise ValueError(f"a date is a year (1978) or a year and month (1978-07), got {text!r}")
        month = int(match.group(2)) if match.group(2) else None
        return cls(int(match.group(1)), month)

    def text(self) -> str:
        return f"{self.year}" if self.month is None else f"{self.year}-{self.month:02d}"


def _check_text(name: str, value: object) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")


@dataclass(frozen=True)
class MetaOverride:
    """What one tray or slide says about itself; ``None`` inherits, ``""`` blanks."""

    description: str | None = None
    date: str | None = None  # kept as text so an unreadable legacy value never blocks loading

    def __post_init__(self) -> None:
        _check_text("description", self.description)
        _check_text("date", self.date)
        if self.description is not None and len(self.description) > MAX_DESCRIPTION:
            raise ValueError(f"description must be at most {MAX_DESCRIPTION} characters")


@dataclass(frozen=True)
class ResolvedMeta:
    description: str
    description_level: Level  # the level that said it; a blank is reported at its own level
    date: EstimatedDate | None
    date_level: Level
    legacy_note: str = ""


def _read_date(text: str) -> tuple[EstimatedDate | None, str]:
    """Read stored date text leniently: never raises, explains what it could not use."""
    try:
        return EstimatedDate.parse(text), ""
    except ValueError:
        pass
    legacy = _LEGACY_DAY.fullmatch(text)
    if legacy is not None:
        try:
            date(int(legacy.group(1)), int(legacy.group(2)), int(legacy.group(3)))
            return EstimatedDate(int(legacy.group(1)), int(legacy.group(2))), (
                f"day dropped from {text}"
            )
        except ValueError:
            pass
    return None, f"unrecognised date: {text}, set a year"


def _pick(values: tuple[tuple[Level, str | None], ...]) -> tuple[str, Level]:
    for level, value in values:
        if value is not None:
            return value, level
    return "", ""


def resolve(
    manifest: BatchManifest, tray: TrayRecord, slide: SlideRecord | None = None
) -> ResolvedMeta:
    """Slide beats tray beats batch, for each field on its own."""
    levels = [("tray", tray.meta)]
    if slide is not None:
        levels.insert(0, ("slide", slide.meta))
    batch_description = unicodedata.normalize("NFC", manifest.description) or None
    description, description_level = _pick(
        (*((lv, m.description) for lv, m in levels), ("batch", batch_description))
    )
    date_text, date_level = _pick(
        (*((lv, m.date) for lv, m in levels), ("batch", manifest.date or None))
    )
    parsed, note = _read_date(date_text) if date_text else (None, "")
    return ResolvedMeta(description, description_level, parsed, date_level, note)
