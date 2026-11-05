from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from opticfilm_linux.batch.metadata import (
    MAX_DESCRIPTION,
    EstimatedDate,
    MetaOverride,
    resolve,
)
from opticfilm_linux.batch.model import BatchManifest, SlideRecord, TrayRecord, TrayStage

LAST_YEAR = date.today().year - 2


def _tree(batch=None, tray=None, slide=None):
    slide_rec = SlideRecord(position=1, meta=slide or MetaOverride())
    tray_rec = TrayRecord(
        number=1, stage=TrayStage.REVIEW, slides=(slide_rec,), meta=tray or MetaOverride()
    )
    manifest = BatchManifest(name="b", trays=(tray_rec,), **(batch or {}))
    return manifest, tray_rec, slide_rec


@pytest.mark.parametrize(
    ("text", "year", "month"), [("1978", 1978, None), ("1978-07", 1978, 7), ("1900-01", 1900, 1)]
)
def test_parse_year_and_month(text, year, month):
    parsed = EstimatedDate.parse(text)
    assert (parsed.year, parsed.month) == (year, month)
    assert parsed.text() == text


@pytest.mark.parametrize(
    "text",
    ["", "78", "1978-7", "1978-13", "1978-00", "1978-07-14", "abc", "1899", " 1978", "１９７８"],
)
def test_parse_refuses_bad_text(text):
    with pytest.raises(ValueError):
        EstimatedDate.parse(text)


@pytest.mark.parametrize(
    ("year", "month"),
    [
        (1899, None),
        (LAST_YEAR + 1, None),
        (1978, 0),
        (1978, 13),
        ("1978", None),
        (1978, "7"),
        (True, None),
    ],
)
def test_constructor_refuses_out_of_range(year, month):
    with pytest.raises(ValueError):
        EstimatedDate(year, month)


def test_latest_year_is_accepted_and_frozen():
    est = EstimatedDate(LAST_YEAR)
    with pytest.raises(FrozenInstanceError):
        est.year = 1980  # type: ignore[misc]


def test_override_checks_types_and_is_frozen():
    assert MetaOverride() == MetaOverride(None, None)
    with pytest.raises(ValueError):
        MetaOverride(description=5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        MetaOverride(date=1978)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        MetaOverride(description="x" * (MAX_DESCRIPTION + 1))
    MetaOverride(description="x" * MAX_DESCRIPTION)


def test_nothing_set_resolves_empty():
    got = resolve(*_tree())
    assert (got.description, got.description_level, got.date, got.date_level) == ("", "", None, "")
    assert got.legacy_note == ""


def test_batch_values_inherited():
    got = resolve(*_tree(batch={"description": "Lake", "date": "1978-07"}))
    assert (got.description, got.description_level) == ("Lake", "batch")
    assert got.date == EstimatedDate(1978, 7)
    assert got.date_level == "batch"


def test_precedence_slide_over_tray_over_batch_per_field():
    batch = {"description": "B", "date": "1970"}
    got = resolve(*_tree(batch=batch, tray=MetaOverride(description="T", date="1971")))
    assert (got.description, got.description_level) == ("T", "tray")
    assert (got.date.year, got.date_level) == (1971, "tray")
    got = resolve(
        *_tree(batch=batch, tray=MetaOverride(description="T"), slide=MetaOverride(date="1972"))
    )
    assert (got.description, got.description_level) == ("T", "tray")
    assert (got.date.year, got.date_level) == (1972, "slide")
    got = resolve(
        *_tree(batch=batch, tray=MetaOverride(date="1971"), slide=MetaOverride(description="S"))
    )
    assert (got.description, got.description_level) == ("S", "slide")
    assert (got.date.year, got.date_level) == (1971, "tray")


def test_blank_blocks_inheritance():
    batch = {"description": "B", "date": "1970"}
    got = resolve(*_tree(batch=batch, slide=MetaOverride(description="", date="")))
    assert (got.description, got.description_level) == ("", "slide")
    assert (got.date, got.date_level) == (None, "slide")
    got = resolve(*_tree(batch=batch, tray=MetaOverride(description="")))
    assert (got.description, got.description_level) == ("", "tray")
    assert got.date.year == 1970


def test_slide_none_inherits_tray_blank():
    got = resolve(*_tree(batch={"description": "B"}, tray=MetaOverride(description="")))
    assert got.description == ""


def test_resolve_without_slide_uses_tray_level():
    manifest, tray, _ = _tree(batch={"date": "1970"}, tray=MetaOverride(date="1971"))
    assert resolve(manifest, tray).date.year == 1971


def test_legacy_day_date_resolves_to_month_with_note():
    manifest, tray, slide = _tree(batch={"date": "1978-07-14"})
    got = resolve(manifest, tray, slide)
    assert got.date == EstimatedDate(1978, 7)
    assert "day" in got.legacy_note
    assert manifest.date == "1978-07-14"  # stored text kept


@pytest.mark.parametrize("text", ["summer 78", "1978-02-31", "1850-01-01", "1978-13", "99999"])
def test_garbage_date_resolves_to_none_with_note(text):
    got = resolve(*_tree(batch={"date": text}))
    assert got.date is None
    assert text in got.legacy_note
    assert got.date_level == "batch"


def test_garbage_in_override_never_raises():
    got = resolve(*_tree(slide=MetaOverride(date="whenever")))
    assert got.date is None and "whenever" in got.legacy_note


def test_clean_dates_have_no_note():
    assert resolve(*_tree(batch={"date": "1978"})).legacy_note == ""


def test_replace_makes_new_objects():
    meta = MetaOverride(description="a")
    assert replace(meta, date="1980") is not meta and meta.date is None
