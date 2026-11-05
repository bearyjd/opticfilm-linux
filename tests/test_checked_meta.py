import pytest

from opticfilm_linux.batch.metadata import checked_meta


def test_checked_meta_normalises_date_and_description():
    assert checked_meta(" 1978-07 ", "  Beach  ") == ("1978-07", "Beach")


def test_checked_meta_blank_date_is_none():
    assert checked_meta(None, "") == (None, "")
    assert checked_meta("  ", "x") == (None, "x")


def test_checked_meta_refuses_bad_values():
    with pytest.raises(ValueError):
        checked_meta("last year", "")
    with pytest.raises(ValueError):
        checked_meta(None, "x" * 501)
