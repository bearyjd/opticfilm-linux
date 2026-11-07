"""``env_switch``: a switch that is unset, on or off. Junk counts as unset (and is logged)."""

import logging

import pytest

from opticfilm_linux.scanner.select import auto_eject_enabled, env_switch

NAME = "OPTICFILM_TEST_SWITCH"


@pytest.mark.parametrize("value", ["1", "true", "YES", " on ", "On"])
def test_on_values(monkeypatch, value):
    monkeypatch.setenv(NAME, value)
    assert env_switch(NAME) is True


@pytest.mark.parametrize("value", ["0", "false", "NO", " off ", "Off"])
def test_off_values(monkeypatch, value):
    monkeypatch.setenv(NAME, value)
    assert env_switch(NAME) is False


@pytest.mark.parametrize("value", [None, "", "   "])
def test_unset_or_blank_is_none(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(NAME, raising=False)
    else:
        monkeypatch.setenv(NAME, value)
    assert env_switch(NAME) is None


def test_junk_is_unset_with_a_log_line(monkeypatch, caplog):
    monkeypatch.setenv(NAME, "maybe")
    with caplog.at_level(logging.WARNING):
        assert env_switch(NAME) is None
    assert NAME in caplog.text


def test_legacy_bool_reader_is_unchanged_for_junk(monkeypatch):
    monkeypatch.setenv("OPTICFILM_AUTO_EJECT", "maybe")
    assert auto_eject_enabled() is False
