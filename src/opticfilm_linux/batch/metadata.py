"""Batch description and estimated date, and how they pass down batch -> tray -> slide.

Every date is an estimate with two precisions, a year or a year and month. Each of the two fields
is set or inherited on its own: ``None`` means inherit, ``""`` means explicitly blank at that
level, anything else is the value.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

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


def validate_description(text: str) -> str:
    """The rule for text being written (create, CLI, edits); loading never applies it."""
    if not isinstance(text, str):
        raise ValueError("description must be text")
    text = unicodedata.normalize("NFC", text).strip()
    if len(text) > MAX_DESCRIPTION:
        raise ValueError(f"description must be at most {MAX_DESCRIPTION} characters")
    return text


def checked_meta(date: str | None, description: str) -> tuple[str | None, str]:
    """The strict rules for text being written; a bad value is refused before anything is made."""
    estimate = (date or "").strip()
    when = EstimatedDate.parse(estimate).text() if estimate else None
    return when, validate_description(description)


FIELDS = ("description", "date")


def checked_override(field: str, value: str | None) -> str | None:
    """The strict rule for one tray or slide field being written: ``None`` inherits and
    ``""`` (or only spaces) blanks; anything else must pass the same rules as ``checked_meta``.
    """
    if field not in FIELDS:
        raise ValueError(f"metadata field must be one of {', '.join(FIELDS)}, not {field!r}")
    if value is None:
        return None
    if field == "description":
        return validate_description(value)
    if not isinstance(value, str):
        raise ValueError("date must be text")
    return checked_meta(value, "")[0] or ""


@dataclass(frozen=True)
class MetaOverride:
    """What one tray or slide says about itself; ``None`` inherits, ``""`` blanks.

    Loading is lenient, so the values are not checked here: ``resolve`` copes with odd ones.
    """

    description: str | None = None
    date: str | None = None  # kept as text so an unreadable legacy value never blocks loading


def without_meta(tray: TrayRecord) -> TrayRecord:
    """``tray`` with its own and every slide's metadata at the default: for guards that must
    notice any other change, but never a description or date edit made meanwhile."""
    slides = tuple(replace(s, meta=MetaOverride()) for s in tray.slides)
    return replace(tray, meta=MetaOverride(), slides=slides)


@dataclass(frozen=True)
class ResolvedMeta:
    description: str
    description_level: Level  # the level that said it; a blank is reported at its own level
    date: EstimatedDate | None
    date_level: Level
    legacy_note: str = ""
    description_note: str = ""


def _read_date(text: object) -> tuple[EstimatedDate | None, str]:
    """Read stored date text leniently: never raises, explains what it could not use."""
    if not isinstance(text, str):
        return None, f"unrecognised date: {text!r}, set a year"
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


def _pick(values: tuple[tuple[Level, Any], ...]) -> tuple[str, Level]:
    for level, value in values:
        if value is not None:
            return value, level
    return "", ""


def _description_at(level: Level, value: object) -> tuple[str | None, str]:
    """A stored description made safe to use, and a note when it was not usable as stored."""
    if value is None:
        return None, ""
    if not isinstance(value, str):
        return None, f"ignored a description of the wrong type at {level} level"
    note = (
        f"description longer than {MAX_DESCRIPTION} characters"
        if len(value) > MAX_DESCRIPTION
        else ""
    )
    return unicodedata.normalize("NFC", value), note


def resolve(
    manifest: BatchManifest, tray: TrayRecord, slide: SlideRecord | None = None
) -> ResolvedMeta:
    """Slide beats tray beats batch, for each field on its own."""
    levels: list[tuple[Level, MetaOverride]] = [("tray", tray.meta)]
    if slide is not None:
        levels.insert(0, ("slide", slide.meta))
    stored = [(lv, m.description) for lv, m in levels] + [("batch", manifest.description or None)]
    checked = [(lv, *_description_at(lv, v)) for lv, v in stored]
    description, description_level = _pick(tuple((lv, v) for lv, v, _ in checked))
    notes = [n for _, _, n in checked if n]
    date_text, date_level = _pick(
        (
            *((lv, m.date) for lv, m in levels),
            ("batch", None if manifest.date == "" else manifest.date),
        )
    )
    parsed, note = _read_date(date_text) if date_text != "" else (None, "")
    return ResolvedMeta(description, description_level, parsed, date_level, note, "; ".join(notes))
