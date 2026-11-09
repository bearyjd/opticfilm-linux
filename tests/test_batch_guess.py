"""guess_batch: a deterministic, English-only proposal of a description and an estimated date."""

import random
import string

import pytest

from opticfilm_linux.batch.guess import BatchGuess, guess_batch
from opticfilm_linux.batch.metadata import MAX_DESCRIPTION, EstimatedDate

THIS_YEAR = 2026
E = EstimatedDate


def guess(text: str) -> BatchGuess:
    return guess_batch(text, this_year=THIS_YEAR)


def years(*ys: int) -> tuple[EstimatedDate, ...]:
    return tuple(E(y) for y in ys)


def months(year: int, *ms: int) -> tuple[EstimatedDate, ...]:
    return tuple(E(year, m) for m in ms)


def has_note(result: BatchGuess, *needles: str) -> bool:
    return any(all(n in note for n in needles) for note in result.notes)


@pytest.mark.parametrize(
    ("text", "year", "month"),
    [
        ("Maine trip 1974", 1974, None),
        ("Christmas 1983 - Dec", 1983, 12),
        ("Dec 1983", 1983, 12),
        ("December 1983", 1983, 12),
        ("Dec. 1983", 1983, 12),
        ("Sept 1979, Cape Cod", 1979, 9),
        ("1979 September", 1979, 9),
        ("wedding, 6/1979", 1979, 6),
        ("wedding 06/1979", 1979, 6),
        ("slides 1979-06", 1979, 6),
        ("slides 1979/06", 1979, 6),
        ("Aug, 1962", 1962, 8),
        ("May 1970 prom", 1970, 5),
        ("circa 1974", 1974, None),
        ("'74 beach", 1974, None),
        ("beach ’74", 1974, None),
        ("1900 farm", 1900, None),
        ("1974 and again 1974", 1974, None),
        ("Kodachrome 1974-07, Grandma's camera", 1974, 7),
        ("June and June 1974", 1974, 6),
        ("it may be 1974", 1974, None),
        ("Aunt Jan took these on a trip, 1974", 1974, None),
        ("Fall '82", 1982, None),
    ],
)
def test_a_single_year_and_maybe_month_is_guessed(text, year, month):
    result = guess(text)
    assert result.date == E(year, month)
    assert result.description == text
    if month is not None:
        assert result.alternatives == ()


def test_the_headline_example_defaults_to_the_year_and_offers_the_summer_months():
    result = guess("Maine trip, summer 1974, Kodachrome, Grandma's camera")
    assert result.date == E(1974)
    assert result.alternatives == months(1974, 6, 7, 8)
    assert result.description == "Maine trip, summer 1974, Kodachrome, Grandma's camera"
    assert len(result.notes) == 1
    assert has_note(result, "summer", "not a month")


@pytest.mark.parametrize(
    ("season", "picks"),
    [
        ("spring", (3, 4, 5)),
        ("Summer", (6, 7, 8)),
        ("FALL", (9, 10, 11)),
        ("autumn", (9, 10, 11)),
    ],
)
def test_a_season_gives_the_year_and_its_months(season, picks):
    result = guess(f"lake {season} 1975")
    assert result.date == E(1975)
    assert result.alternatives == months(1975, *picks)
    assert len(result.notes) == 1
    assert season.lower() in result.notes[0].lower()


def test_winter_offers_dec_jan_feb_of_the_same_year_and_flags_the_ambiguity():
    result = guess("winter 1975")
    assert result.date == E(1975)
    assert result.alternatives == months(1975, 12, 1, 2)
    assert has_note(result, "Winter spans two calendar years")


def test_a_named_month_beats_a_season():
    result = guess("summer 1975, July")
    assert result.date == E(1975, 7)
    assert result.alternatives == ()
    assert result.notes == ()


def test_a_season_without_a_year_gives_no_date_and_says_so():
    result = guess("summer at the lake")
    assert (result.date, result.alternatives) == (None, ())
    assert has_note(result, "summer", "no year")


@pytest.mark.parametrize(
    "text", ["June-August 1974", "Jun to Aug 1974", "June – August of 1974", "jun through aug 1974"]
)
def test_a_month_range_in_one_year_defaults_to_the_year(text):
    result = guess(text)
    assert result.date == E(1974)
    assert result.alternatives == months(1974, 6, 7, 8)
    assert has_note(result, "range of months")


@pytest.mark.parametrize(
    ("text", "picks"),
    [("Nov-Feb 1974", (11, 12, 1, 2)), ("Dec-Feb 1974", (12, 1, 2)), ("Dec to Jan 1974", (12, 1))],
)
def test_a_month_range_over_new_year_gives_the_year_and_every_month_of_the_span(text, picks):
    result = guess(text)
    assert result.date == E(1974)  # never a stray single month
    assert result.alternatives == months(1974, *picks)
    assert has_note(result, "1973")


def test_the_same_month_twice_is_just_that_month():
    assert guess("Aug-Aug 1974").date == E(1974, 8)


def test_several_months_in_one_year_give_the_year_and_the_months():
    result = guess("Aug 1974 and Sep 1974")
    assert result.date == E(1974)
    assert result.alternatives == months(1974, 8, 9)
    assert has_note(result, "several months")


@pytest.mark.parametrize(
    ("text", "default", "span"),
    [
        ("1974-1976", 1975, (1974, 1975, 1976)),
        ("1974 - 1976 trips", 1975, (1974, 1975, 1976)),
        ("1974 to 1976", 1975, (1974, 1975, 1976)),
        ("1974-1975", 1974, (1974, 1975)),
        ("between 1974 and 1976", 1975, (1974, 1975, 1976)),
        ("1974-76", 1975, (1974, 1975, 1976)),
        ("'74-'76", 1975, (1974, 1975, 1976)),
        ("'74-76", 1975, (1974, 1975, 1976)),
        ("1974 or 1975", 1974, (1974, 1975)),
        ("1976 or 1974", 1974, (1974, 1976)),
        ("1970-1980", 1975, tuple(range(1970, 1981))),
    ],
)
def test_a_year_range_defaults_to_the_middle_year_and_offers_every_year(text, default, span):
    result = guess(text)
    assert result.date == E(default)
    assert result.alternatives == years(*span)
    assert has_note(result, "chosen")


def test_a_range_of_exactly_15_years_is_allowed_and_16_is_too_wide():
    ok = guess("1960-1974")
    assert ok.date == E(1967)
    assert len(ok.alternatives) == 15
    wide = guess("1960-1975")
    assert (wide.date, wide.alternatives) == (None, ())
    assert has_note(wide, "range too wide to guess")


def test_a_range_past_the_bounds_drops_those_years_with_a_note():
    result = guess("2020-2026")
    assert result.alternatives == years(2020, 2021, 2022, 2023, 2024)
    assert result.date == E(2023)  # the middle of the range as typed
    assert has_note(result, "dropped 2025, 2026")
    old = guess("1895-1902")  # the middle, 1898, is cut by the bounds
    assert old.alternatives == years(1900, 1901, 1902)
    assert old.date is None
    assert has_note(old, "dropped 1895")
    assert has_note(old, "middle year 1898")
    assert guess("2030-2032").date is None
    edge = guess("2023-2025")
    assert edge.date == E(2024)
    assert edge.alternatives == years(2023, 2024)
    cut = guess("2024-2030")
    assert cut.date is None
    assert cut.alternatives == years(2024)
    assert has_note(cut, "middle year 2027")


@pytest.mark.parametrize(
    "text",
    [
        "Slides 30-36",
        "tray 35-40",
        "trays 50-60 Kodachrome",
        "Box 12-14",
        "slides 76-74",
        "74-76",
        "Carousel 5 slides 70-79",
    ],
)
def test_two_bare_numbers_are_never_a_year_range(text):
    result = guess(text)
    assert (result.date, result.alternatives) == (None, ())
    assert has_note(result, "no year")


def test_a_slide_range_does_not_hide_the_real_year():
    result = guess("Roll 35-40, Maine 1974")
    assert result.date == E(1974)
    assert result.alternatives == ()


@pytest.mark.parametrize("text", ["1974-13", "1974-20", "1980-79", "1976-1974"])
def test_a_range_that_ends_before_it_starts_is_not_swapped(text):
    result = guess(text)
    assert has_note(result, "ends before it starts")
    assert not any(a.year < 1900 or a.year == 1913 for a in result.alternatives)
    assert result.date is None or result.date.year == int(text[:4])


def test_a_reversed_year_pair_is_a_conflict_not_a_range():
    result = guess("1976-1974")
    assert result.date is None
    assert result.alternatives == years(1974, 1976)


@pytest.mark.parametrize("text", ["1974-13", "1974-20", "1980-79", "1999-00"])
def test_the_start_year_survives_a_refused_range(text):
    assert guess(text).date == E(int(text[:4]))


@pytest.mark.parametrize(
    ("text", "decade"),
    [
        ("1970s family", 1970),
        ("the 70s", 1970),
        ("'70s slides", 1970),
        ("1970's trips", 1970),
        ("the Seventies", 1970),
        ("1900s farm", 1900),
        ("the 50s", 1950),
    ],
)
def test_a_decade_has_no_default_and_offers_its_ten_years(text, decade):
    result = guess(text)
    assert result.date is None
    assert result.alternatives == years(*range(decade, decade + 10))
    assert has_note(result, "decade", "pick one")


def test_a_decade_is_cut_at_the_latest_year():
    result = guess("2020s")
    assert result.date is None
    assert result.alternatives == years(2020, 2021, 2022, 2023, 2024)
    assert has_note(result, "dropped")


def test_an_ambiguous_short_decade_gives_nothing():
    result = guess("the 10s")
    assert (result.date, result.alternatives) == (None, ())
    assert has_note(result, "ambiguous")


def test_a_specific_year_beats_a_decade_word():
    assert guess("1970s, mostly 1974").date == E(1974)


@pytest.mark.parametrize(
    ("text", "offered"),
    [
        ("1974 and 1983", (1974, 1983)),
        ("1983, 1974 and 1990", (1974, 1983, 1990)),
        ("Dec 1983 - Jan 1984 and 1990", (1983, 1984, 1990)),
        ("1974-1976 and 1983", (1974, 1976, 1983)),
    ],
)
def test_unrelated_years_are_a_conflict_with_the_years_as_choices(text, offered):
    result = guess(text)
    assert result.date is None
    assert result.alternatives == years(*offered)
    assert has_note(result, "more than one year")


def test_a_conflict_offers_at_most_five_years():
    result = guess("1950 1951 1952 1953 1954 1955 1956")
    assert result.date is None
    assert result.alternatives == years(1950, 1951, 1952, 1953, 1954)
    assert has_note(result, "only the first 5")


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("Christmas Dec 25, 1983", "day"),
        ("25 December 1983", "day"),
        ("25th of Dec 1983", "day"),
        ("1983-12-25", "day"),
        ("12/25/1983", "day"),
        ("25/12/1983", "day"),
        ("12/12/1983", "day"),
    ],
)
def test_a_day_level_date_keeps_year_and_month_and_notes_the_day(text, needle):
    result = guess(text)
    assert result.date == E(1983, 12)
    assert result.alternatives == ()
    assert has_note(result, needle, "dropped")


def test_a_day_that_could_be_a_month_gives_the_year_only():
    result = guess("party 6/5/1979")
    assert result.date == E(1979)
    assert has_note(result, "day", "month")


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("no idea when", "no year"),
        ("Kodachrome, Grandma's camera", "no year"),
        ("year 3000", "no year"),
        ("1850 farm", "1850"),
        ("2026 trip", "2026"),
        ("2025 trip", "2025"),
        ("1899", "1899"),
        ("'05 trip", "ambiguous"),
        ("'00 trip", "ambiguous"),
    ],
)
def test_refusals_give_no_date_and_say_why(text, needle):
    result = guess(text)
    assert (result.date, result.alternatives) == (None, ())
    assert result.description == " ".join(text.split())
    assert has_note(result, needle), result.notes


def test_a_plausible_year_wins_over_an_implausible_number_with_a_note():
    result = guess("roll 1234, summer 1974")
    assert result.date == E(1974)
    assert has_note(result, "1234")


def test_two_digit_year_only_when_one_century_fits():
    assert guess("'74").date == E(1974)
    assert guess("'99 trip").date == E(1999)
    assert guess("'24").date is None  # 1924 and 2024 both fit
    assert guess("'25").date == E(1925)
    assert guess("'74").description == "'74"


def test_year_bounds_follow_this_year():
    assert guess_batch("2018", this_year=2020).date == E(2018)
    assert guess_batch("2019", this_year=2020).date is None


@pytest.mark.parametrize("text", ["", "   ", "\n\t \n"])
def test_blank_text_is_an_empty_guess(text):
    assert guess(text) == BatchGuess(description="", date=None, alternatives=(), notes=())


def test_whitespace_is_normalised_and_date_words_are_kept():
    assert guess("  Maine\n trip,\t summer   1974  ").description == "Maine trip, summer 1974"


def test_a_long_description_is_cut_to_500_with_a_note():
    result = guess("x " * 400 + "1974")
    assert len(result.description) <= MAX_DESCRIPTION
    assert has_note(result, "500")


def test_exactly_500_characters_is_not_cut():
    result = guess("a" * 499 + "b")
    assert len(result.description) == 500
    assert not has_note(result, "500")


def test_the_result_is_frozen_and_repeatable():
    result = guess("Maine summer 1974")
    with pytest.raises(AttributeError):
        result.description = "x"  # type: ignore[misc]
    assert result == guess("Maine summer 1974")


ODD = [
    "\x00\x01\x02",
    "‮1974‬",
    "１９７４",
    "١٩٧٤",
    "\U0001f600" * 1000,
    "1974" * 5000,
    "-" * 100000,
    "/" * 5000 + "1/1/1974",
    "Dec " * 20000 + "1974",
    "́" * 3000,
    "1/1/1/1/1/1/1/1",
    "'" * 3000 + "74",
    "9" * 100000,
    "1974-" * 3000,
    "1 " * 50000,
    "19-" * 20000,
    "1970s " * 5000,
    "1974 to " * 5000,
    "\ud800 lone surrogate",
    "June-August " * 3000 + "1974",
]


@pytest.mark.parametrize("text", ODD)
def test_odd_strings_never_raise(text):
    result = guess(text)
    assert isinstance(result, BatchGuess)
    assert len(result.description) <= MAX_DESCRIPTION


def test_random_strings_never_raise_and_stay_valid():
    rng = random.Random(1974)
    alphabet = string.printable + "éß漢字\U0001f600́'’/-–—.,"
    words = ["Dec", "May", "summer", "winter", "1974", "74", "'74", "1970s", "6/1979", "to", "or"]
    words += ["1983-12-25", "June-August", "74-76", "1960-1990", "between", "and"]
    for _ in range(600):
        pieces = [
            rng.choice(words) if rng.random() < 0.5 else rng.choice(alphabet)
            for _ in range(rng.randrange(0, 40))
        ]
        result = guess(rng.choice([" ", "", "-", "/"]).join(pieces))
        assert len(result.description) <= MAX_DESCRIPTION
        assert all(isinstance(n, str) and n for n in result.notes)
        assert len(result.alternatives) <= 15
        if result.date is not None:
            assert E.parse(result.date.text()) == result.date


def test_lowercase_may_is_not_a_month_but_may_is():
    assert guess("may 1974").date == E(1974)
    assert guess("1974 may").date == E(1974)
    assert guess("May 1974").date == E(1974, 5)
    assert guess("MAY 1974").date == E(1974, 5)


@pytest.mark.parametrize(
    ("text", "month", "word"), [("Aunt May 1974", 5, "May"), ("Aunt Jan 1974", 1, "Jan")]
)
def test_a_name_that_is_also_a_month_is_read_as_a_month_and_says_so(text, month, word):
    result = guess(text)
    assert result.date == E(1974, month)
    assert has_note(result, f'"{word}" was read as a month')


def test_an_ordinary_month_has_no_notes():
    assert guess("Dec 1983").notes == ()


@pytest.mark.parametrize(
    "text", ["abc1985def", "v1.1974", "555-123-1985", "(207) 555-1974", "x1985"]
)
def test_a_year_glued_to_other_characters_or_in_a_phone_number_is_not_a_year(text):
    result = guess(text)
    assert (result.date, result.alternatives) == (None, ())


def test_a_house_number_style_year_is_kept_with_a_note():
    result = guess("123 Main St 1985 Lane")
    assert result.date == E(1985)
    assert has_note(result, "a number in the text was read as a year")
    assert not has_note(guess("Maine trip 1985"), "a number in the text")


@pytest.mark.parametrize(
    ("text", "date", "needle"),
    [
        ("1974/75", E(1974), "1974/75"),
        ("1.1.1974", E(1974, 1), "dropped"),
        ("31.12.1974", E(1974, 12), "dropped"),
        ("2/30/1974", E(1974), "not a real date"),
        ("Feb 30, 1974", E(1974), "not a real date"),
        ("1974-02-30", E(1974), "not a real date"),
        ("2/29/1975", E(1975), "not a real date"),
        ("2/29/1976", E(1976, 2), "dropped"),
        ("13/1974", E(1974), "13/1974"),
    ],
)
def test_pieces_that_cannot_be_used_say_so(text, date, needle):
    result = guess(text)
    assert result.date == date
    assert has_note(result, needle)


def test_a_decade_before_1900_has_no_choices_and_says_so():
    result = guess("1800s")
    assert (result.date, result.alternatives) == (None, ())
    assert has_note(result, "no choices", "before 1900")


def test_only_the_first_5000_characters_are_read():
    late = guess("x " * 2600 + "1974")
    assert late.date is None
    assert has_note(late, "only the first 5000 characters were read")
    early = guess("1974 " + "x " * 5000)
    assert early.date == E(1974)
    assert has_note(early, "only the first 5000 characters were read")
    assert not has_note(guess("1974 trip"), "5000")


def test_a_megabyte_paste_is_fast():
    import time

    started = time.perf_counter()
    result = guess("Dec 1983 trip " * 80000)
    assert time.perf_counter() - started < 0.25
    assert result.date == E(1983, 12)
    assert len(result.description) <= MAX_DESCRIPTION


@pytest.mark.parametrize("bad", [None, 5, b"1974", ["1974"]])
def test_non_text_gives_an_empty_guess(bad):
    assert guess(bad) == BatchGuess("", None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    ["June 3-5 1974", "June 3 - 5, 1974", "3-5 June 1974", "Jun 3rd-5th, 1974", "June 3 to 5 1974"],
)
def test_a_day_range_inside_one_month_keeps_the_month(text):
    result = guess(text)
    assert result.date == E(1974, 6)
    assert result.alternatives == ()
    assert has_note(result, "days", "dropped")


def test_a_day_range_that_is_not_real_falls_back_to_the_year():
    assert guess("Feb 28-31 1975").date != E(1975, 2)


@pytest.mark.parametrize(
    ("text", "default", "span"),
    [
        ("Dec 1999 - Feb 2000", (2000, 1), ((1999, 12), (2000, 1), (2000, 2))),
        ("December 1999 to February 2000", (2000, 1), ((1999, 12), (2000, 1), (2000, 2))),
        ("Nov 1999 - Feb 2000", (1999, 12), ((1999, 11), (1999, 12), (2000, 1), (2000, 2))),
        ("Dec 1999 - Jan 2000", (1999, 12), ((1999, 12), (2000, 1))),
    ],
)
def test_a_month_range_across_years_defaults_to_the_middle_month(text, default, span):
    result = guess(text)
    assert result.date == E(*default)
    assert result.alternatives == tuple(E(*p) for p in span)
    assert has_note(result, "range of months", "chosen")


def test_a_month_range_across_years_is_capped_at_15_months():
    ok = guess("Jan 1999 - Mar 2000")  # exactly 15 months
    assert ok.date == E(1999, 8)
    assert len(ok.alternatives) == 15
    wide = guess("Jan 1999 - Apr 2000")  # 16 months: the year-range handling
    assert wide.date == E(1999)
    assert wide.alternatives == years(1999, 2000)
    assert not any(a.month for a in wide.alternatives)


@pytest.mark.parametrize("text", ["'74 to '76", "'74 through '76", "'74 - '76", "’74 to ’76"])
def test_a_short_year_range_with_words_is_a_year_range(text):
    result = guess(text)
    assert result.date == E(1975)
    assert result.alternatives == years(1974, 1975, 1976)
    assert has_note(result, "chosen")


@pytest.mark.parametrize("text", ["'76 to '74", "'76-'74"])
def test_a_reversed_short_year_range_is_a_conflict_like_the_four_digit_form(text):
    result = guess(text)
    assert result.date is None
    assert result.alternatives == years(1974, 1976)
    assert has_note(result, "ends before it starts")


def test_a_capital_c_initial_is_not_taken_as_circa():
    result = guess("Aunt C. 1975")
    assert result.date == E(1975)
    assert not has_note(result, "approximate")
    assert has_note(guess("c. 1975"), "approximate")


@pytest.mark.parametrize("text", ["rock'n 74", "rock'n'74", "rock'n 74 to 76"])
def test_a_quote_inside_a_word_is_not_a_short_year(text):
    assert guess(text).date is None


def test_a_season_note_is_kept_beside_a_year_range_note():
    result = guess("Winter 1983-84")
    assert result.date == E(1983)
    assert result.alternatives == years(1983, 1984)
    assert has_note(result, "chosen")
    assert has_note(result, "winter", "not a month")
    assert has_note(result, "two calendar years")
    assert has_note(guess("summer 1974-76"), "summer", "not a month")


@pytest.mark.parametrize(
    "text", ["1975ish", "c. 1975", "circa 1975", "about 1975", "~1975", "~ 1975", "ca. 1975"]
)
def test_an_approximate_year_is_the_year_with_a_note(text):
    result = guess(text)
    assert result.date == E(1975)
    assert result.alternatives == ()
    assert has_note(result, "approximate")


@pytest.mark.parametrize("text", ["1974-13-01", "1974-00-05"])
def test_an_invalid_month_is_not_used_and_says_so(text):
    result = guess(text)
    assert result.date == E(1974)
    assert result.alternatives == ()
    assert has_note(result, "month", "not valid")
    assert not has_note(result, "ends before it starts")


@pytest.mark.parametrize("text", ["1999-00", "1974-00"])
def test_a_dash_zero_zero_suffix_is_not_called_a_bad_month(text):
    result = guess(text)
    assert result.date == E(int(text[:4]))
    assert result.alternatives == ()
    assert has_note(result, "-00", "not read as a month or a year range")
    assert not has_note(result, "not valid")


@pytest.mark.parametrize("text", ["Feb 1-29 1975", "Apr 31-33 1975", "Apr 5-31 1975"])
def test_an_impossible_day_range_says_so_and_keeps_the_year(text):
    result = guess(text)
    assert result.date == E(1975)
    assert result.alternatives == ()
    assert has_note(result, "day range", "not valid")


@pytest.mark.parametrize("text", ["'99 to '03", "'99 - '01", "'99-'03"])
def test_a_short_range_across_the_century_is_a_year_range(text):
    result = guess(text)
    first, last = 1999, 2000 + int(text[-2:])
    assert result.alternatives == years(*range(first, last + 1))
    assert result.date == E((first + last) // 2)
    assert has_note(result, "chosen")


def test_a_short_range_across_the_century_keeps_the_width_cap():
    result = guess("'99 to '20")  # 22 years
    assert result.date is None
    assert has_note(result, "too wide")


@pytest.mark.parametrize("text", ["'05 to '07", "'10 to '12"])
def test_a_short_range_with_both_ends_ambiguous_is_still_ambiguous(text):
    result = guess(text)
    assert result.date is None
    assert has_note(result, "ambiguous")
