"""A switch flipped while a scan runs takes effect only from the next scan (plan B4).

Auto-eject is snapshotted when the scan is started and the eject needs the snapshot AND the
current setting: turning it off mid-scan also stops the eject of the running scan (the safe
direction), turning it on mid-scan does not eject that scan.
"""

import pytest

from controller_helpers import open_new, wait_all_in_review, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.workflow_settings import locked_text
from opticfilm_linux.scanner import interrupted
from test_app_auto_eject import EjectRecorder, _banner, _settle


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("OPTICFILM_AUTO_EJECT", "OPTICFILM_SKIP_EMPTY", "OPTICFILM_SKIP_GAPS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def build(sweep600_dir, tmp_path):
    made = []

    def make(flip_during_first_scan=None, **kwargs):
        calls = []

        def before_scan():
            if not calls and flip_during_first_scan is not None:
                c.set_auto_eject(flip_during_first_scan)
            calls.append(1)

        scanner = EjectRecorder(sweep600_dir, before_scan=before_scan)
        c = Controller(lambda _dpi: scanner, **kwargs)
        made.append(c)
        open_new(c, tmp_path / "b")
        return c, scanner

    yield make
    interrupted.clear()
    for c in made:
        c.shutdown()


def _scan(c, count=1):
    wait_until(c, lambda: c.state().can_scan)
    c.scan()
    wait_all_in_review(c, count)
    _settle(c)


def test_turning_auto_eject_on_mid_scan_spares_that_scan_but_not_the_next(build):
    c, scanner = build(flip_during_first_scan=True, auto_eject=False)
    _scan(c)
    assert c.auto_eject_enabled is True
    assert scanner.ejects == 0
    _scan(c, 2)
    wait_until(c, lambda: scanner.ejects == 1)


def test_turning_auto_eject_off_mid_scan_also_stops_that_eject(build):
    c, scanner = build(flip_during_first_scan=False, auto_eject=True)
    _scan(c)
    assert c.auto_eject_enabled is False
    assert scanner.ejects == 0 and _banner(c) == ""


def test_an_unchanged_setting_still_ejects(build):
    c, scanner = build(auto_eject=True)
    _scan(c)
    wait_until(c, lambda: scanner.ejects == 1)


def test_turned_on_mid_scan_with_a_cut_off_pending_never_ejects(build):
    c, scanner = build(flip_during_first_scan=True, auto_eject=False)
    wait_until(c, lambda: c.state().can_scan)
    c.scan()
    wait_until(c, lambda: c.auto_eject_enabled)  # the flip happened inside the scan
    interrupted.begin("scan")
    _settle(c)
    assert scanner.ejects == 0


def test_a_rescan_takes_its_own_snapshot(build):
    c, scanner = build(auto_eject=False)
    _scan(c)
    c.set_auto_eject(True)
    c.rescan(1)
    wait_until(c, lambda: scanner.ejects == 1)


# --- env locks and the discarded openings choice -------------------------------------------


def test_locked_skip_empty_is_refused_and_says_why(build):
    c, _scanner = build(skip_empty=False, env_locked=frozenset({"skip_empty"}))
    assert c.set_skip_empty(True) is False
    assert c.skip_empty_enabled is False
    wait_until(c, lambda: c.state().banner is not None)
    assert "OPTICFILM_SKIP_EMPTY" in c.state().banner.message
    assert locked_text("skip_empty") == c.state().banner.message


def test_discarding_a_non_default_openings_choice_is_said(build):
    c, _scanner = build(skip_empty=True)
    c.set_scan_openings((1, 2))
    wait_until(c, lambda: c.state().scan_openings == (1, 2))
    assert c.set_skip_empty(False) is True
    wait_until(c, lambda: c.state().banner is not None)
    assert "openings" in c.state().banner.message.lower()


def test_no_banner_when_the_choice_was_already_all_four(build):
    c, _scanner = build(skip_empty=True)
    assert c.set_skip_empty(False) is True
    _settle(c, 0.1)
    assert c.state().banner is None
