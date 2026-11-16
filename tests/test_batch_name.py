"""guess_batch_name: the Name a new batch is offered from the owner's notes or its folder."""

import random
import string

import pytest

from opticfilm_linux.batch.batch_name import MAX_NAME, guess_batch_name


@pytest.mark.parametrize(
    ("text", "folder", "expected"),
    [
        ("Maine trip, summer 1974, Kodachrome", "x", "Maine trip"),
        ("Maine trip. Summer 1974", "x", "Maine trip"),
        ("Maine trip; summer 1974", "x", "Maine trip"),
        ("Christmas 1983 - Dec", "x", "Christmas 1983"),
        ("Christmas 1983 – Dec", "x", "Christmas 1983"),
        ("Lake trip\nsecond line", "x", "Lake trip"),
        ("  Lake   \t trip  ", "x", "Lake trip"),
        ("Black-and-white prints", "x", "Black-and-white prints"),
        ("maine TRIP", "x", "maine TRIP"),
        (", leading comma, then words", "x", "leading comma"),
        ("", "Mimi_Foundry-Work", "Mimi Foundry Work"),
        ("  , ; ", "  Mimi__Foundry - Work ", "Mimi Foundry Work"),
        ("", "", ""),
        ("  ", " _- ", ""),
    ],
)
def test_phrases_and_folder_fallback(text, folder, expected):
    assert guess_batch_name(text, folder) == expected


def test_a_5000_char_paste_is_capped():
    result = guess_batch_name("word " * 1000, "folder")
    assert 0 < len(result) <= MAX_NAME
    assert result == result.strip()


def test_a_long_folder_name_is_capped():
    assert len(guess_batch_name("", "a_" * 500)) <= MAX_NAME


def test_random_strings_never_raise():
    rng = random.Random(1974)
    alphabet = string.printable + "éß漢字\U0001f600́'’/-–—.,;\ud800"
    for _ in range(600):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 80)))
        folder = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 80)))
        result = guess_batch_name(text, folder)
        assert isinstance(result, str) and len(result) <= MAX_NAME
        assert result == result.strip()
