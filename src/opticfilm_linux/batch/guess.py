"""A deterministic, English-only proposal of a batch description and estimated date.

The owner types whatever they know ("Maine trip, summer 1974, Kodachrome"). ``guess_batch``
proposes the description (their words) and a date for the existing fields. The date is always
an ``EstimatedDate`` (a year or a year and month); what cannot be said in one is offered as
``alternatives`` the owner can pick, with ``notes`` saying why. No network, no AI, never raises.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from opticfilm_linux.batch.metadata import (
    EARLIEST_YEAR,
    LATEST_YEAR_BACK,
    MAX_DESCRIPTION,
    EstimatedDate,
)

MAX_RANGE_YEARS = 15
MAX_CONFLICT_YEARS = 5
MAX_SCAN_CHARS = 5000

Pick = tuple[int, int | None]

_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SEASONS = {
    "spring": (3, 4, 5),
    "summer": (6, 7, 8),
    "fall": (9, 10, 11),
    "autumn": (9, 10, 11),
    "winter": (12, 1, 2),
}
_DECADE_WORDS = {
    "twenties": 20,
    "thirties": 30,
    "forties": 40,
    "fifties": 50,
    "sixties": 60,
    "seventies": 70,
    "eighties": 80,
    "nineties": 90,
}
_F = re.IGNORECASE | re.ASCII
_QUOTE = "['’]"
_DASH = "[-–—]"
_TO = rf"(?:{_DASH}|\bto\b|through|thru|until)"
_SEP = r"[\s,./–—-]{0,3}(?:of\s+)?"


def _mon(name: str) -> str:
    return (
        rf"(?<![A-Za-z])(?P<{name}>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|"
        r"july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"(?![A-Za-z])\.?"
    )


_YEAR4 = r"[12]\d{3}"
_RE_DECADE4 = re.compile(rf"(?<!\d)(?P<d>[12]\d\d0){_QUOTE}?s(?![A-Za-z\d])", _F)
_RE_DECADE2 = re.compile(rf"(?<![\w'’]){_QUOTE}?(?P<d>\d0){_QUOTE}?s(?![A-Za-z\d])", _F)
_RE_DECADE_WORD = re.compile(rf"\b(?P<d>{'|'.join(_DECADE_WORDS)})\b", _F)
_RE_ISO = re.compile(r"(?<![\d-])(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})(?![\d-])", _F)
_RE_SLASH3 = re.compile(
    r"(?<![\d/.])(?P<a>\d{1,2})(?P<s>[/.])(?P<b>\d{1,2})(?P=s)(?P<y>\d{4})(?![\d/.]\d)", _F
)
_RE_MON_DAY_YEAR = re.compile(
    rf"{_mon('m')}\s*(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(?P<y>\d{{4}})(?!\d)", _F
)
_RE_DAY_MON_YEAR = re.compile(
    rf"(?<!\d)(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_mon('m')}\s*,?\s*(?P<y>\d{{4}})(?!\d)",
    _F,
)
_RE_MON_RANGE = re.compile(
    rf"{_mon('a')}\s*(?:{_DASH}|\bto\b|through|thru)\s*{_mon('b')}{_SEP}(?P<y>\d{{4}})(?!\d)", _F
)
_RE_YEAR_RANGE = re.compile(
    rf"(?<![\d/])(?P<a>{_YEAR4})\s*{_TO}\s*(?P<b>{_YEAR4}|\d{{2}})(?!\d)", _F
)
_RE_BETWEEN = re.compile(rf"\bbetween\s+(?P<a>{_YEAR4})\s+and\s+(?P<b>{_YEAR4})(?!\d)", _F)
_RE_OR = re.compile(rf"(?<!\d)(?P<a>{_YEAR4})\s+or\s+(?P<b>{_YEAR4})(?!\d)", _F)
_RE_SHORT_RANGE = re.compile(
    rf"(?<![\w'’./:-]){_QUOTE}(?P<a>\d{{2}})\s*{_DASH}\s*{_QUOTE}?(?P<b>\d{{2}})(?![\w/:-])(?!\.\d)",
    _F,
)
_RE_MON_YEAR = re.compile(rf"{_mon('m')}{_SEP}(?P<y>\d{{4}})(?!\d)", _F)
_RE_YEAR_MON = re.compile(rf"(?<!\d)(?P<y>\d{{4}}){_SEP}{_mon('m')}", _F)
_RE_NUM_YM = re.compile(r"(?<![\d/])(?P<y>\d{4})[-/](?P<m>\d{1,2})(?![\d/-])", _F)
_RE_NUM_MY = re.compile(r"(?<![\d/.-])(?P<m>\d{1,2})/(?P<y>\d{4})(?![\d/])", _F)
_RE_SHORT_YEAR = re.compile(rf"(?<![\w'’]){_QUOTE}(?P<y>\d{{2}})(?!\d)", _F)
# a year stands alone: not glued to letters or digits, not "v1.1974", not after "555-"
_RE_YEAR = re.compile(
    rf"(?<![A-Za-z\d])(?<![A-Za-z\d]\.)(?<!\b\d{{3}}-)(?P<y>{_YEAR4})(?![A-Za-z\d])", _F
)
_STREET = (
    "st|street|ave|avenue|rd|road|blvd|dr|drive|ln|lane|way|ct|court|apt|unit|suite|hwy|route|rt|"
    "no|number|nr|slides?|trays?|roll|box|carousel|page|p"
)
_RE_NUMBER_BEFORE = re.compile(rf"(?:^|[^A-Za-z])(?:{_STREET})\.?\s*#?\s*$|#\s*$", _F)
_RE_NUMBER_AFTER = re.compile(
    r"\s*(?:st|street|ave|avenue|rd|road|blvd|drive|lane|way|court)\b", _F
)
_RE_SEASON = re.compile(r"\b(?P<s>spring|summer|fall|autumn|winter)\b", _F)


@dataclass(frozen=True)
class BatchGuess:
    """``date`` is the default pick (None when nothing can be said); ``alternatives`` are the
    other picks the owner may choose, in order; ``notes`` say in plain words what was done."""

    description: str
    date: EstimatedDate | None
    alternatives: tuple[EstimatedDate, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass
class _Found:
    singles: list[Pick] = field(default_factory=list)
    groups: list[tuple[str, int, int]] = field(default_factory=list)  # ("range"|"or", first, last)
    month_ranges: list[tuple[int, tuple[int, ...]]] = field(default_factory=list)
    decades: list[int] = field(default_factory=list)
    seasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _blank(pattern: re.Pattern[str], text: str, handler: Callable[[re.Match[str]], bool]) -> str:
    """Replace each match the handler consumed with a space; leave the rest alone."""
    return pattern.sub(lambda m: " " if handler(m) else m.group(0), text)


def _month_no(word: str) -> int | None:
    """1..12 for a month word; a lowercase "may" is the verb, not the month."""
    if word == "may":
        return None
    return _MONTH_NAMES.index(word[:3].capitalize()) + 1


def _note_name(found: _Found, word: str) -> None:
    """Names that are also months ("Aunt May 1974") are read as months; say so."""
    if word.lower() in ("may", "jan"):
        found.notes.append(f'"{word}" was read as a month; edit it if it is a name')


def _short_year(yy: int, hi: int) -> int | None:
    """The year for a two-digit year, or None when 19yy and 20yy both fit."""
    return 1900 + yy if 2000 + yy > hi else None


def _scan_decades(text: str, found: _Found, hi: int) -> str:
    def take(token: str, two: int) -> bool:
        year = _short_year(two, hi)
        if year is None:
            found.notes.append(
                f"{token} could be the {1900 + two}s or the {2000 + two}s, which is ambiguous, "
                "so no decade was guessed"
            )
        else:
            found.decades.append(year)
        return True

    def four(m: re.Match[str]) -> bool:
        found.decades.append(int(m["d"]))
        return True

    text = _blank(_RE_DECADE4, text, four)
    text = _blank(_RE_DECADE2, text, lambda m: take(m.group(0).strip(), int(m["d"])))
    return _blank(
        _RE_DECADE_WORD,
        text,
        lambda m: take(m.group(0), _DECADE_WORDS[m["d"].lower()]),
    )


def _days_in(year: int, month: int) -> int:
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]


def _scan_days(text: str, found: _Found) -> str:
    def add(y: int, month: int | None, day: int, token: str) -> bool:
        if not 1 <= day <= 31:
            return False
        if month is not None and day > _days_in(y, month):
            found.singles.append((y, None))
            found.notes.append(
                f"{token} is not a real date (there is no day {day} in {_MONTH_NAMES[month - 1]} "
                f"{y}), so only the year was guessed"
            )
            return True
        found.singles.append((y, month))
        if month is None:
            found.notes.append(
                f"{token} could be day/month or month/day, so no month was guessed "
                "(the day is dropped; only year and month are kept)"
            )
        else:
            found.notes.append(
                f"the day ({day}) in {token} was dropped; only year and month are kept"
            )
        return True

    def iso(m: re.Match[str]) -> bool:
        month = int(m["m"])
        return 1 <= month <= 12 and add(int(m["y"]), month, int(m["d"]), m.group(0))

    def slash(m: re.Match[str]) -> bool:
        a, b = int(m["a"]), int(m["b"])
        if a > 12 and 1 <= b <= 12:
            return add(int(m["y"]), b, a, m.group(0))
        if b > 12 and 1 <= a <= 12:
            return add(int(m["y"]), a, b, m.group(0))
        if a == b and 1 <= a <= 12:
            return add(int(m["y"]), a, b, m.group(0))
        if 1 <= a <= 12 and 1 <= b <= 12:
            return add(int(m["y"]), None, 1, m.group(0))
        return False

    def named(m: re.Match[str]) -> bool:
        month = _month_no(m["m"])
        if month is not None:
            _note_name(found, m["m"])
        return month is not None and add(int(m["y"]), month, int(m["d"]), m.group(0))

    text = _blank(_RE_ISO, text, iso)
    text = _blank(_RE_SLASH3, text, slash)
    text = _blank(_RE_MON_DAY_YEAR, text, named)
    return _blank(_RE_DAY_MON_YEAR, text, named)


def _scan_ranges(text: str, found: _Found, hi: int) -> str:
    def months(m: re.Match[str]) -> bool:
        first, last = _month_no(m["a"]), _month_no(m["b"])
        if first is None or last is None or first == last:
            return False
        span = (
            tuple(range(first, last + 1))
            if first < last
            else (*range(first, 13), *range(1, last + 1))
        )
        found.month_ranges.append((int(m["y"]), span))
        return True

    def years(kind: str) -> Callable[[re.Match[str]], bool]:
        def take(m: re.Match[str]) -> bool:
            first, last = int(m["a"]), m["b"]
            end = int(last) if len(last) == 4 else first // 100 * 100 + int(last)
            if len(last) == 2 and 1 <= int(last) <= 12:
                return False  # 1974-07 is a year and month
            if end < first and kind == "range":
                found.notes.append(
                    f"{m.group(0)}: the range ends before it starts, so it was not read as a range"
                )
                return False
            if end == first:
                found.singles.append((first, None))
            else:
                found.groups.append((kind, min(first, end), max(first, end)))
            return True

        return take

    def short(m: re.Match[str]) -> bool:
        first, last = _short_year(int(m["a"]), hi), _short_year(int(m["b"]), hi)
        if first is None or last is None or last <= first:
            return False  # "'74-'76" only; bare pairs like "35-40" are slide numbers
        found.groups.append(("range", first, last))
        return True

    text = _blank(_RE_MON_RANGE, text, months)
    text = _blank(_RE_YEAR_RANGE, text, years("range"))
    text = _blank(_RE_BETWEEN, text, years("range"))
    text = _blank(_RE_OR, text, years("or"))
    return _blank(_RE_SHORT_RANGE, text, short)


def _scan_singles(text: str, found: _Found, hi: int) -> str:
    def month_year(m: re.Match[str]) -> bool:
        month = _month_no(m["m"])
        if month is None:
            return False
        _note_name(found, m["m"])
        found.singles.append((int(m["y"]), month))
        return True

    def numeric(m: re.Match[str]) -> bool:
        if not 1 <= int(m["m"]) <= 12:
            if "/" in m.group(0):  # "1974-13" already got the range note
                found.notes.append(f"{m.group(0)} is not a year and month (month {int(m['m'])})")
            return False
        found.singles.append((int(m["y"]), int(m["m"])))
        return True

    def short(m: re.Match[str]) -> bool:
        year = _short_year(int(m["y"]), hi)
        if year is None:
            found.notes.append(
                f"{m.group(0)} could be {1900 + int(m['y'])} or {2000 + int(m['y'])}, "
                "which is ambiguous, so no year was guessed"
            )
        else:
            found.singles.append((year, None))
        return True

    text = _blank(_RE_MON_YEAR, text, month_year)
    text = _blank(_RE_YEAR_MON, text, month_year)
    text = _blank(_RE_NUM_YM, text, numeric)
    text = _blank(_RE_NUM_MY, text, numeric)
    text = _blank(_RE_SHORT_YEAR, text, short)

    def plain(m: re.Match[str]) -> bool:
        before = text[max(0, m.start() - 20) : m.start()]
        if _RE_NUMBER_BEFORE.search(before) or _RE_NUMBER_AFTER.match(text, m.end()):
            found.notes.append("a number in the text was read as a year")
        found.singles.append((int(m["y"]), None))
        return True

    return _blank(_RE_YEAR, text, plain)


def _scan(text: str, hi: int) -> _Found:
    found = _Found()
    text = _scan_decades(text, found, hi)
    text = _scan_days(text, found)
    text = _scan_ranges(text, found, hi)
    text = _scan_singles(text, found, hi)
    found.seasons.extend(m["s"].lower() for m in _RE_SEASON.finditer(text))
    return found


def _years_text(years: list[int]) -> str:
    return ", ".join(str(y) for y in years)


def _from_group(
    group: tuple[str, int, int], hi: int, notes: list[str]
) -> tuple[Pick | None, list[Pick], list[str]]:
    kind, first, last = group
    if kind == "range" and last - first + 1 > MAX_RANGE_YEARS:
        notes.append(f"{first}-{last}: range too wide to guess (more than {MAX_RANGE_YEARS} years)")
        return None, [], notes
    years = sorted({first, last}) if kind == "or" else list(range(first, last + 1))
    good = [y for y in years if EARLIEST_YEAR <= y <= hi]
    if len(good) < len(years):
        dropped = [y for y in years if y not in good]
        notes.append(f"dropped {_years_text(dropped)}: not a plausible year ({EARLIEST_YEAR}-{hi})")
    if not good:
        return None, [], notes
    middle = years[0] if kind == "or" else years[(len(years) - 1) // 2]
    if middle not in good:
        notes.append(
            f"the middle year {middle} of {first}-{last} is not plausible, so no year was chosen; "
            "pick one of the choices"
        )
        return None, [(y, None) for y in good], notes
    default = middle
    what = "years" if kind == "or" else "a range of years"
    notes.append(
        f"{what} {first}{'/' if kind == 'or' else '-'}{last}: the date is one year, so "
        f"{default} was chosen; pick another if you know it"
    )
    return (default, None), [(y, None) for y in good], notes


def _one_year(found: _Found, year: int, notes: list[str]) -> tuple[Pick, list[Pick], list[str]]:
    months = sorted({m for y, m in found.singles if y == year and m is not None})
    ranges = [ms for y, ms in found.month_ranges if y == year]
    if len(months) == 1:
        return (year, months[0]), [], notes
    if months:
        names = ", ".join(_MONTH_NAMES[m - 1] for m in months)
        notes.append(
            f"several months ({names}) were mentioned, so only the year {year} was guessed"
        )
        return (year, None), [(year, m) for m in months], notes
    if ranges:
        span = ranges[0]
        notes.append(
            f"{_MONTH_NAMES[span[0] - 1]}-{_MONTH_NAMES[span[-1] - 1]} is a range of months, so "
            f"only the year {year} was guessed; pick a month if you know it"
        )
        if span[0] > span[-1]:
            late = _MONTH_NAMES[span[0] - 1] + ("" if span[0] == 12 else "-Dec")
            notes.append(f"{late} may belong to {year - 1} rather than {year}")
        return (year, None), [(year, m) for m in span], notes
    if found.seasons:
        return (year, None), _season_picks(found.seasons, year, notes), notes
    return (year, None), [], notes


def _season_picks(seasons: list[str], year: int, notes: list[str]) -> list[Pick]:
    names = list(dict.fromkeys(seasons))
    picks: list[int] = []
    for name in names:
        picks.extend(m for m in _SEASONS[name] if m not in picks)
    label = " and ".join(names)
    note = (
        f"{label} is not a month, so only the year {year} was guessed; pick a month if you know it"
    )
    if "winter" in names:
        note += (
            f". Winter spans two calendar years, so Dec, Jan and Feb of {year} are offered; "
            "the December may belong to the year before"
        )
    notes.append(note)
    return [(year, m) for m in picks]


def _no_year(found: _Found, hi: int, notes: list[str]) -> tuple[None, list[Pick], list[str]]:
    decades = sorted(set(found.decades))
    if len(decades) > 1:
        notes.append(f"more than one decade mentioned ({', '.join(f'{d}s' for d in decades)})")
    elif decades:
        years = [y for y in range(decades[0], decades[0] + 10) if EARLIEST_YEAR <= y <= hi]
        if not years:
            where = "before" if decades[0] < EARLIEST_YEAR else "after"
            limit = EARLIEST_YEAR if where == "before" else hi
            notes.append(f"the {decades[0]}s: no choices, {where} {limit}")
            return None, [], notes
        if len(years) < 10:
            notes.append(f"dropped years outside {EARLIEST_YEAR}-{hi} from the {decades[0]}s")
        notes.append(
            f"the {decades[0]}s is a decade, too broad for one date: pick one year from the choices"
        )
        return None, [(y, None) for y in years], notes
    elif found.seasons:
        notes.append(f"{found.seasons[0]} found but no year, so no date was guessed")
    return None, [], notes


def _decide(found: _Found, hi: int) -> tuple[Pick | None, list[Pick], list[str]]:
    notes = list(found.notes)
    singles = [y for y, _ in found.singles] + [y for y, _ in found.month_ranges]
    if len(found.groups) == 1 and all(
        found.groups[0][1] <= y <= found.groups[0][2] for y in singles
    ):
        return _from_group(found.groups[0], hi, notes)
    years = singles + [y for _, a, b in found.groups for y in (a, b)]
    if not years:
        return _no_year(found, hi, notes)
    good = sorted({y for y in years if EARLIEST_YEAR <= y <= hi})
    bad = sorted({y for y in years if not EARLIEST_YEAR <= y <= hi})
    if bad:
        notes.append(f"{_years_text(bad)}: not a plausible year (must be {EARLIEST_YEAR}-{hi})")
    if len(good) > 1:
        shown = good[:MAX_CONFLICT_YEARS]
        notes.append(
            f"more than one year mentioned ({_years_text(good)}) and they are not a range, "
            "so no date was guessed; pick one of the choices"
            + (
                f" (only the first {MAX_CONFLICT_YEARS} are offered)"
                if len(good) > len(shown)
                else ""
            )
        )
        return None, [(y, None) for y in shown], notes
    if not good:
        return None, [], notes
    return _one_year(found, good[0], notes)


def _estimate(pick: Pick) -> EstimatedDate | None:
    try:
        return EstimatedDate(*pick)
    except ValueError:
        return None


def _description(text: str) -> tuple[str, list[str]]:
    flat = " ".join(unicodedata.normalize("NFC", text).split())
    if len(flat) <= MAX_DESCRIPTION:
        return flat, []
    return flat[:MAX_DESCRIPTION].rstrip(), [f"description cut to {MAX_DESCRIPTION} characters"]


def guess_batch(text: str, *, this_year: int) -> BatchGuess:
    """Propose a description and an estimated date from what the owner typed. Never raises."""
    if not isinstance(text, str):
        return BatchGuess("", None)
    head = text[: MAX_SCAN_CHARS * 4]  # a huge paste must not cost a keystroke
    flat = " ".join(unicodedata.normalize("NFC", head).split())
    if not flat:
        return BatchGuess("", None)
    description, cut = _description(head)
    hi = this_year - LATEST_YEAR_BACK
    default, picks, notes = _decide(_scan(flat[:MAX_SCAN_CHARS], hi), hi)
    date = _estimate(default) if default is not None else None
    alternatives = tuple(e for e in map(_estimate, picks) if e is not None)
    if date is None and not alternatives and not notes:
        notes.append("no year found, so no date was guessed")
    if len(flat) > MAX_SCAN_CHARS or len(text) > len(head):
        notes.append(f"only the first {MAX_SCAN_CHARS} characters were read")
    return BatchGuess(description, date, alternatives, tuple(dict.fromkeys([*notes, *cut])))
