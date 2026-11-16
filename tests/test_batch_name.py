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
        ("St. Louis trip 1974", "x", "St. Louis trip 1974"),
        ("Mt. Desert Island, Maine", "x", "Mt. Desert Island"),
        ("Dr. Smith. Dinner party", "x", "Dr. Smith"),
        ("J. Smith wedding", "x", "J. Smith wedding"),
        ("Lake trip.", "x", "Lake trip"),
        ("Lake trip. summer 1974", "x", "Lake trip. summer 1974"),
        ("Christmas 12/25/74", "x", "Christmas 12 25 74"),
        ("Mom/Dad 1974", "x", "Mom Dad 1974"),
        ("a\\b", "x", "a b"),
        (", Maine", "x", "Maine"),
        ("   \t\n ", "", ""),
        ("   \t\n ", "Mimi_Work", "Mimi Work"),
        ("\u202eMaine\u202c trip", "x", "Maine trip"),
        ("Ma\x00ine trip", "x", "Maine trip"),
        ("lone \ud800 surrogate", "x", "lone surrogate"),
        ("", "a/b\\c", "a b c"),
    ],
)
def test_phrases_and_folder_fallback(text, folder, expected):
    assert guess_batch_name(text, folder) == expected


def test_a_5000_char_paste_is_cut_at_a_word_boundary():
    result = guess_batch_name("word " * 1000, "folder")
    assert result == " ".join(["word"] * 12)[:59] and len(result) <= MAX_NAME


def test_a_word_longer_than_the_cap_is_cut_hard():
    assert guess_batch_name("x" * 5000, "") == "x" * MAX_NAME


def test_the_cap_never_splits_a_combining_sequence():
    assert guess_batch_name("a" * 59 + "e\u0301", "") == "a" * 59


def test_the_cap_never_splits_a_zwj_emoji():
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    result = guess_batch_name("a" * 59 + family, "")
    assert result == "a" * 59
    result = guess_batch_name("a" * 58 + family, "")
    assert not result.endswith("\u200d") and len(result) <= MAX_NAME


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
        assert not set(result) & set("/\\\x00\u202e\ud800")


@pytest.mark.parametrize(
    ("text", "folder"),
    [
        ("...", "x"),
        ("..", "x"),
        (".hidden", "x"),
        ("../..", "x"),
        ("..\\..", "x"),
        (". . .", "x"),
        (" .x", "x"),
        ("", ".."),
        ("", "..."),
    ],
)
def test_a_name_never_starts_with_a_dot_so_it_cannot_become_the_parent_folder(text, folder):
    result = guess_batch_name(text, folder)
    assert not result.startswith(".")
    assert result in ("", result.strip())


def test_a_name_that_starts_with_dots_keeps_the_rest():
    assert guess_batch_name(".hidden roll", "x") == "hidden roll"
