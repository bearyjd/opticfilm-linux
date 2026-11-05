import pytest

from opticfilm_linux.scanner.select import (
    next_strip_enabled,
    skip_empty_enabled,
    skip_gaps_enabled,
)


@pytest.mark.parametrize(
    "value,enabled",
    [
        (None, True),
        ("", True),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("unexpected", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("FALSE", False),
        ("No", False),
        ("OFF", False),
        (" 0 ", True),
        (" false ", True),
        (" no ", True),
        (" off ", True),
    ],
)
def test_next_strip_is_on_unless_explicitly_disabled(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv("OPTICFILM_NEXT_STRIP", raising=False)
    else:
        monkeypatch.setenv("OPTICFILM_NEXT_STRIP", value)
    assert next_strip_enabled() is enabled


@pytest.mark.parametrize(
    "value,enabled",
    [
        (None, False),
        ("", False),
        ("0", False),
        ("off", False),
        ("unexpected", False),
        ("2", False),
        ("1", True),
        ("true", True),
        ("YES", True),
        (" on ", True),
    ],
)
@pytest.mark.parametrize(
    "name,reader",
    [("OPTICFILM_SKIP_EMPTY", skip_empty_enabled), ("OPTICFILM_SKIP_GAPS", skip_gaps_enabled)],
)
def test_skipping_switches_are_off_unless_explicitly_on(monkeypatch, name, reader, value, enabled):
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    assert reader() is enabled
