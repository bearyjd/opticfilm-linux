"""Holder controls are off while a job runs; a batch cannot change under a running holder move."""

import threading

import pytest

from controller_helpers import open_new, wait_until
from opticfilm_linux.scanner.base import ScannerStatus
from test_app_controller_holder import build  # noqa: F401 - the fixture
from test_app_state import _build

WAIT_HOLDER = "Wait for the eject/load to finish"
WAIT_EXPORT = "Wait for the export to finish, then try again"


@pytest.mark.parametrize("jobs_running", [False, True])
@pytest.mark.parametrize("scanner", [ScannerStatus.READY, ScannerStatus.INTERRUPTED])
def test_holder_buttons_are_off_while_a_job_runs(scanner, jobs_running):
    state = _build(scanner=scanner, holder_control=True, next_strip=True, jobs_running=jobs_running)
    assert state.can_eject is (scanner is ScannerStatus.READY and not jobs_running)
    assert state.can_load_next is False  # READY is not NOT_READY
    assert state.can_confirm_scanner is (scanner is ScannerStatus.INTERRUPTED and not jobs_running)


def test_load_next_is_off_while_a_job_runs():
    kwargs = {"scanner": ScannerStatus.NOT_READY, "holder_control": True, "next_strip": True}
    assert _build(**kwargs).can_load_next
    assert not _build(**kwargs, jobs_running=True).can_load_next


def test_controls_follow_a_running_job_and_come_back(build, tmp_path):  # noqa: F811
    c, scanner = build(next_strip=True)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject and c.state().can_load_next)
    gate = threading.Event()
    c._run(lambda: gate.wait(30), "export stand-in", pool=c._jobs)
    wait_until(c, lambda: not c.state().can_eject and not c.state().can_load_next)
    gate.set()
    wait_until(c, lambda: c.state().can_eject and c.state().can_load_next)
    assert scanner.calls == []


def test_eject_button_press_is_dropped_while_a_job_runs(build, tmp_path):  # noqa: F811
    c, scanner = build()
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    gate = threading.Event()
    c._run(lambda: gate.wait(30), "export stand-in", pool=c._jobs)
    wait_until(c, lambda: not c.state().can_eject)
    session = c._session
    session.poller.take_eject_press = lambda: True
    c._act_on_eject_button(session.poller)
    gate.set()
    assert scanner.calls == [] and c.state().banner is None


@pytest.mark.parametrize("action", ["close", "open", "new"])
def test_batch_change_is_refused_while_a_holder_move_runs(build, tmp_path, action):  # noqa: F811
    gate = threading.Event()
    c, scanner = build(gate=gate)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    c.eject_holder()
    wait_until(c, lambda: scanner.calls == ["eject"])
    change = {
        "close": lambda: c.close_batch(),
        "open": lambda: c.open_batch(tmp_path / "b"),
        "new": lambda: c.new_batch(tmp_path / "n", "n", "", None, 600),
    }[action]
    change()
    wait_until(c, lambda: c.state().banner is not None)
    assert c.state().banner.message == WAIT_HOLDER
    assert c.state().batch_name is not None  # still the same open batch
    gate.set()
    wait_until(c, lambda: not c._holder_busy)
    c.dismiss_banner()
    c.close_batch()
    wait_until(c, lambda: c.state().batch_name is None)
    assert scanner.calls == ["eject"]
