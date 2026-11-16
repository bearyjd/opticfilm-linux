"""A proposed batch Name: the first phrase of what the owner knows, else the folder's name.

Pure and deterministic; never raises. The result is only a guess for an editable field.
"""

from __future__ import annotations

import re
import unicodedata

MAX_NAME = 60

# A phrase ends at a comma, semicolon, newline, spaced dash, or a sentence-ending period.
_BOUNDARY = re.compile(r"[,;\r\n.]|\s[-–—]\s")
_FOLDER_SEPARATORS = re.compile(r"[_\-/\\]+")
_ABBREVIATIONS = frozenset(
    "st mt dr mr mrs ms jr sr ft ave rd co vs etc no".split()
)
_ZWJ = "‍"


def _is_abbreviation(before: str) -> bool:
    words = before.split()
    word = words[-1].lower().strip("(\"'") if words else ""
    return word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha())


def _ends_sentence(text: str, at: int) -> bool:
    """Whether the period at ``at`` ends a phrase: end of text, or space then a capital."""
    rest = text[at + 1 :]
    if not rest.strip():
        return True
    return (
        rest[0].isspace()
        and rest.lstrip()[0].isupper()
        and not _is_abbreviation(text[:at])
    )


def _first_phrase(text: str) -> str:
    # Leading separators (", Maine") are skipped so a stray comma does not hide the phrase.
    start = 0
    for found in _BOUNDARY.finditer(text):
        if found.group() == "." and not _ends_sentence(text, found.start()):
            continue
        if text[start : found.start()].strip():
            return text[start : found.start()]
        start = found.end()
    return text[start:]


def _clean(text: str) -> str:
    """Whitespace collapsed; control, format (except ZWJ), surrogate and bidi characters dropped."""
    kept = "".join(
        ch
        for ch in text.replace("/", " ").replace("\\", " ")
        if ch.isspace() or ch == _ZWJ or unicodedata.category(ch) not in ("Cc", "Cf", "Cs")
    )
    return " ".join(kept.split())


def _is_joiner(ch: str) -> bool:
    return ch == _ZWJ or unicodedata.category(ch) in ("Mn", "Mc", "Me")


def _cap(text: str) -> str:
    """At most MAX_NAME characters: cut at a space when there is one, never inside a sequence."""
    if len(text) <= MAX_NAME:
        return text
    cut = MAX_NAME
    if not text[cut].isspace():
        space = text.rfind(" ", 0, cut)
        cut = space if space > 0 else cut
    while cut > 0 and _is_joiner(text[cut]):
        cut -= 1
    head = text[:cut]
    while head and _is_joiner(head[-1]):
        head = head[:-1]
    return head.strip()


def guess_batch_name(text: str, folder_name: str) -> str:
    """The first phrase of ``text`` as typed, else ``folder_name`` read as words, else ''."""
    phrase = _cap(_clean(_first_phrase(text)))
    if phrase:
        return phrase
    return _cap(_clean(_FOLDER_SEPARATORS.sub(" ", folder_name)))
