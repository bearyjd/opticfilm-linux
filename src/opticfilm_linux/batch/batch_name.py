"""A proposed batch Name: the first phrase of what the owner knows, else the folder's name.

Pure and deterministic; never raises. The result is only a guess for an editable field.
"""

from __future__ import annotations

import re

MAX_NAME = 60

_PHRASE_END = re.compile(r"[,.;\r\n]|\s[-–—]\s")
_FOLDER_SEPARATORS = re.compile(r"[_-]+")


def _tidy(text: str) -> str:
    return " ".join(text.split())[:MAX_NAME].strip()


def guess_batch_name(text: str, folder_name: str) -> str:
    """The first phrase of ``text`` as typed, else ``folder_name`` read as words, else ''."""
    phrase = _tidy(_first_phrase(text))
    if phrase:
        return phrase
    return _tidy(_FOLDER_SEPARATORS.sub(" ", folder_name))


def _first_phrase(text: str) -> str:
    # Leading separators ("; Maine trip") are skipped so a stray comma does not hide the phrase.
    for piece in _PHRASE_END.split(text):
        if piece.strip():
            return piece
    return ""
