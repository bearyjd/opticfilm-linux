"""Saved workflow switches: prefs, the env/pref resolution, live toggles, compatibility."""

import pytest

from controller_helpers import open_new, scan_when_ready, wait_until
from opticfilm_linux.app import prefs as preferences
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.prefs import MemoryPrefs
from opticfilm_linux.app.workflow_settings import resolve_switch, startup_switches
from opticfilm_linux.model import ALL_POSITIONS
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.select import AUTO_EJECT_ENV, LOAD_AND_SCAN_ENV, SKIP_EMPTY_ENV

ENVS = (
    "OPTICFILM_AUTO_EJECT",
    "OPTICFILM_SKIP_EMPTY",
    "OPTICFILM_SKIP_GAPS",
    "OPTICFILM_LOAD_AND_SCAN",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENVS:
        monkeypatch.delenv(name, raising=False)


# --- prefs -----------------------------------------------------------------------------------


@pytest.mark.parametrize("key", preferences.SWITCH_KEYS)
def test_switches_default_off_on_a_fresh_profile(key):
    assert preferences.read_switch(MemoryPrefs(), key) is False


@pytest.mark.parametrize("key", preferences.SWITCH_KEYS)
def test_switch_round_trips(key):
    store = MemoryPrefs()
    preferences.remember_switch(store, key, True)
    assert preferences.read_switch(store, key) is True
    preferences.remember_switch(store, key, False)
    assert preferences.read_switch(store, key) is False


@pytest.mark.parametrize("junk", ["", "maybe", "2", "yes please", "None", "\x00"])
def test_malformed_stored_value_is_off(junk):
    store = MemoryPrefs()
    store.set(preferences.AUTO_EJECT_KEY, junk)
    assert preferences.read_switch(store, preferences.AUTO_EJECT_KEY) is False


def test_unknown_key_is_refused():
    with pytest.raises(ValueError):
        preferences.remember_switch(MemoryPrefs(), "theme", True)


def test_old_prefs_without_the_keys_load_unchanged():
    store = MemoryPrefs()
    preferences.remember_theme(store, "light")
    preferences.remember_export(store, "/tmp/x", "png8", "3600")
    assert [preferences.read_switch(store, k) for k in preferences.SWITCH_KEYS] == [False] * len(
        preferences.SWITCH_KEYS
    )
    assert preferences.read_theme(store) == "light"
    assert preferences.export_defaults(store).format == "png8"


# --- resolution matrix: env (unset/on/off) x pref (on/off) -----------------------------------


@pytest.mark.parametrize(
    ("env", "pref", "expected"),
    [
        (None, False, False),
        (None, True, True),
        (True, False, True),
        (True, True, True),
        (False, False, False),
        (False, True, False),
    ],
)
def test_resolve_switch_matrix(env, pref, expected):
    assert resolve_switch(env, pref) is expected


@pytest.mark.parametrize("env", [None, "1", "0", "junk"])
@pytest.mark.parametrize("pref", [False, True])
def test_startup_switches_apply_the_matrix(monkeypatch, env, pref):
    if env is not None:
        monkeypatch.setenv(AUTO_EJECT_ENV, env)
        monkeypatch.setenv(SKIP_EMPTY_ENV, env)
    store = MemoryPrefs()
    preferences.remember_switch(store, preferences.AUTO_EJECT_KEY, pref)
    preferences.remember_switch(store, preferences.SKIP_EMPTY_KEY, pref)
    result = startup_switches(store)
    expected = {"1": True, "0": False}.get(env, pref)
    assert result["auto_eject"] is expected
    assert result["skip_empty"] is expected
    locked = env in ("1", "0")
    assert result["env_locked"] == (frozenset({"auto_eject", "skip_empty"}) if locked else set())


def test_load_and_scan_is_off_on_a_fresh_profile():
    result = startup_switches(MemoryPrefs())
    assert result["load_and_scan"] is False
    assert "load_and_scan" not in result["env_locked"]


@pytest.mark.parametrize("env", [None, "1", "0", "junk"])
@pytest.mark.parametrize("pref", [False, True])
def test_load_and_scan_env_beats_the_saved_value(monkeypatch, env, pref):
    if env is not None:
        monkeypatch.setenv(LOAD_AND_SCAN_ENV, env)
    store = MemoryPrefs()
    preferences.remember_switch(store, preferences.LOAD_AND_SCAN_KEY, pref)
    result = startup_switches(store)
    assert result["load_and_scan"] is {"1": True, "0": False}.get(env, pref)
    assert ("load_and_scan" in result["env_locked"]) is (env in ("1", "0"))


def test_startup_skip_gaps_stays_env_only(monkeypatch):
    monkeypatch.setenv("OPTICFILM_SKIP_GAPS", "1")
    assert startup_switches(MemoryPrefs())["skip_gaps"] is True
    monkeypatch.delenv("OPTICFILM_SKIP_GAPS")
    assert startup_switches(MemoryPrefs())["skip_gaps"] is False


# --- live toggles on the fake controller -----------------------------------------------------


def _controller(sweep600_dir, tmp_path, **kwargs):
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir), **kwargs)
    open_new(c, tmp_path / "b")
    return c


def test_auto_eject_toggles_live(sweep600_dir, tmp_path):
    c = _controller(sweep600_dir, tmp_path)
    try:
        assert c.auto_eject_enabled is False
        assert c.set_auto_eject(True) is True
        assert c.auto_eject_enabled is True
        assert c.set_auto_eject(False) is True
        assert c.auto_eject_enabled is False
    finally:
        c.shutdown()


def test_env_locked_switch_refuses_a_change_and_says_why(sweep600_dir, tmp_path):
    c = _controller(sweep600_dir, tmp_path, auto_eject=True, env_locked=frozenset({"auto_eject"}))
    try:
        assert c.set_auto_eject(False) is False
        assert c.auto_eject_enabled is True
        wait_until(c, lambda: c.state().banner is not None)
        assert AUTO_EJECT_ENV in c.state().banner.message
        assert c.set_skip_empty(True) is True  # the other switch is not locked
    finally:
        c.shutdown()


def test_load_and_scan_toggles_live_and_its_env_lock_refuses(sweep600_dir, tmp_path):
    c = _controller(sweep600_dir, tmp_path)
    try:
        assert c.load_and_scan_enabled is False
        assert c.set_load_and_scan(True) is True
        assert c.load_and_scan_enabled is True
        assert c.set_load_and_scan(False) is True
    finally:
        c.shutdown()
    c = _controller(
        sweep600_dir, tmp_path / "x", load_and_scan=False, env_locked=frozenset({"load_and_scan"})
    )
    try:
        assert c.set_load_and_scan(True) is False
        assert c.load_and_scan_enabled is False
        wait_until(c, lambda: c.state().banner is not None)
        assert LOAD_AND_SCAN_ENV in c.state().banner.message
    finally:
        c.shutdown()


def test_skip_empty_toggle_applies_from_the_next_scan(sweep600_dir, tmp_path):
    c = _controller(sweep600_dir, tmp_path, skip_empty=True)
    try:
        c.set_scan_openings((1, 2))
        wait_until(c, lambda: c.state().scan_openings == (1, 2))
        scan_when_ready(c)  # the scan takes (1, 2) now
        assert c.set_skip_empty(False) is True
        assert c.skip_empty_enabled is False
        assert c.scan_openings == ALL_POSITIONS  # a stale choice must never survive
        c.set_scan_openings((1,))
        assert c.scan_openings == ALL_POSITIONS  # refused while off
        assert c.set_skip_empty(True) is True
        assert c.skip_empty_enabled is True
    finally:
        c.shutdown()


def test_turning_skip_empty_off_drops_gaps_and_on_restores_them(sweep600_dir, tmp_path):
    c = _controller(sweep600_dir, tmp_path, skip_empty=True, skip_gaps=True)
    try:
        assert c.skip_gaps_enabled is True
        c.set_skip_empty(False)
        assert c.skip_gaps_enabled is False
        c.set_skip_empty(True)
        assert c.skip_gaps_enabled is True
    finally:
        c.shutdown()


# --- compatibility: the auto-export preference seeds only NEW batches ------------------------


def test_existing_batches_keep_their_stored_auto_export(sweep600_dir, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir))
    try:
        c.new_batch(tmp_path / "old", "Old", "", None, 600)
        wait_until(c, lambda: c.state().batch_name == "Old")
        assert c.state().auto_export is False  # an old manifest: no export keys
        c.new_batch(tmp_path / "on", "On", "", None, 600, export_dir=str(out), auto_export=True)
        wait_until(c, lambda: c.state().batch_name == "On")
        assert c.state().auto_export is True
    finally:
        c.shutdown()
