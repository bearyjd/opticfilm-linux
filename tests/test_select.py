import pytest

from opticfilm_linux.scanner.select import next_strip_enabled


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
