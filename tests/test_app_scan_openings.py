"""The openings chosen for the next scan go back to all four whenever the holder may differ.

Owner rule QA2: a forgotten choice must never skip a real slide. So the choice is used up by
the Scan click (refused or not), and reset by any holder move, latch change or batch change.
"""

import json
import threading
from pathlib import Path

import pytest

from controller_helpers import open_new, scan_when_ready, wait_all_in_review, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.model import ALL_POSITIONS
from opticfilm_linux.scanner.driver import DriverConfig, DriverScanner
from opticfilm_linux.scanner.fake import FakeScanner

FAKE_DRIVER = Path(__file__).parent / "fake_driver"


@pytest.fixture
def build(sweep600_dir, tmp_path, monkeypatch):
    """``build(driver=True, **switches)``: a controller on the fake driver (argv logged) or on
    the in-process fake scanner."""
    monkeypatch.setenv("FAKE_DRIVER_SWEEP", str(sweep600_dir))
    monkeypatch.setenv("FAKE_DRIVER_ARGV", str(tmp_path / "argv.json"))
    for name in ("OPTICFILM_SKIP_EMPTY", "OPTICFILM_SKIP_GAPS", "OPTICFILM_DRIVER_CLEAN"):
        monkeypatch.delenv(name, raising=False)
    made = []

    def make(driver=True, before_scan=None, **switches):
        if driver:
            config = DriverConfig(driver_dir=FAKE_DRIVER, log_dir=tmp_path / "logs")
            factory = lambda _dpi: DriverScanner(config)  # noqa: E731
        else:
            factory = lambda _dpi: FakeScanner(sweep600_dir, before_scan=before_scan)  # noqa: E731
        c = Controller(factory, **{"skip_empty": True, **switches})
        made.append(c)
        open_new(c, tmp_path / "b")
        return c

    yield make
    for c in made:
        c.shutdown()


def _argv(tmp_path):
    return json.loads((tmp_path / "argv.json").read_text())


def _slides(argv):
    return argv[argv.index("--slides") + 1]


def _choose(c, openings):
    c.set_scan_openings(openings)
    wait_until(c, lambda: c.state().scan_openings == openings)


def test_a_refused_scan_then_eject_and_load_scans_all_four(build, tmp_path):
    c = build()
    wait_until(c, lambda: c.state().can_scan)
    _choose(c, (1, 2))
    c._set(_holder_busy=True)  # the scan is refused: a holder move is under way
    c.scan()
    c._set(_holder_busy=False)
    wait_until(c, lambda: c.state().banner is not None)
    assert c.state().scan_openings == ALL_POSITIONS and c.state().trays == ()
    c.eject_holder()
    wait_until(c, lambda: not c.state().holder_busy and not c.state().can_scan)
    c.load_next_holder()  # the fake driver's status stays "ready": no wait for can_load_next
    wait_until(c, lambda: not c.state().holder_busy and c.state().can_scan)
    scan_when_ready(c)
    wait_all_in_review(c, 1)
    assert _slides(_argv(tmp_path)) == "1-4"


@pytest.mark.parametrize("move", ["eject_holder", "load_next_holder"])
def test_any_holder_move_resets_the_choice(build, move):
    c = build()
    wait_until(c, lambda: c.state().can_eject)
    if move == "load_next_holder":
        c.eject_holder()
        wait_until(c, lambda: not c.state().holder_busy and not c.state().can_scan)
    _choose(c, (1, 2, 3))
    getattr(c, move)()
    wait_until(c, lambda: not c.state().holder_busy)
    wait_until(c, lambda: c.state().scan_openings == ALL_POSITIONS)


def test_confirming_a_latched_holder_resets_the_choice(build):
    from opticfilm_linux.scanner.holder_latch import HolderLatch

    c = build(driver=False)
    _choose(c, (1,))
    c._remember_holder_latch(HolderLatch.RELEASED)
    wait_until(c, lambda: c.state().scan_openings == ALL_POSITIONS)


def test_a_failing_scan_tray_resets_the_choice(build, monkeypatch):
    c = build(driver=False)
    wait_until(c, lambda: c.state().can_scan)
    _choose(c, (1, 2))

    def refuse(skipped=()):
        raise BatchError("disk full")

    monkeypatch.setattr(c._session.runner, "scan_tray", refuse)
    c.scan()
    wait_until(c, lambda: c.state().banner is not None)
    assert c.state().scan_openings == ALL_POSITIONS and c.state().trays == ()


def test_closing_and_opening_a_batch_resets_the_choice(build, tmp_path):
    c = build(driver=False)
    _choose(c, (1, 2))
    c.close_batch()
    wait_until(c, lambda: c.state().batch_name is None)
    assert c.scan_openings == ALL_POSITIONS
    c.open_batch(tmp_path / "b")
    wait_until(c, lambda: c.state().batch_name == "Test")
    _choose(c, (1,))
    c.new_batch(tmp_path / "c", "Other", "", None, 600)
    wait_until(c, lambda: c.state().batch_name == "Other")
    wait_until(c, lambda: c.state().scan_openings == ALL_POSITIONS)


def test_a_change_while_a_scan_is_pending_is_refused(build):
    gate = threading.Event()
    c = build(driver=False, before_scan=lambda: gate.wait(30))
    try:
        scan_when_ready(c)
        wait_until(c, lambda: c.state().scan_pending)
        c.set_scan_openings((1, 2))
        wait_until(c, lambda: c.state().banner is not None)
        assert "wait for the scan to start" in c.state().banner.message.lower()
        assert c.scan_openings == ALL_POSITIONS
    finally:
        gate.set()
    wait_all_in_review(c, 1)


def test_a_change_after_the_click_cannot_alter_that_scan(build, tmp_path):
    """The choice is taken at the click: the queued job never reads it again."""
    c = build()
    wait_until(c, lambda: c.state().can_scan)
    _choose(c, (1, 2))
    hold = threading.Event()
    c._command.submit(hold.wait, 30)  # the scan job queues behind this one
    c.scan()
    c._scans_queued = 0  # hide the queued scan so the change below is not refused
    c._scan_openings = (1, 2, 3)
    hold.set()
    wait_all_in_review(c, 1)
    assert _slides(_argv(tmp_path)) == "1-2"


def test_a_rescan_always_scans_all_four_openings(build, tmp_path):
    c = build()
    wait_until(c, lambda: c.state().can_scan)
    _choose(c, (1, 2))
    scan_when_ready(c)
    wait_all_in_review(c, 1)
    _choose(c, (1, 2))
    c.rescan(1)
    wait_until(c, lambda: (tmp_path / "b" / "trays" / "0001" / "sweep-2").is_dir())
    wait_all_in_review(c, 1)
    assert _slides(_argv(tmp_path)) == "1-4"
    assert BatchStore.open(tmp_path / "b", readonly=True).snapshot().trays[0].skipped == ()


@pytest.mark.parametrize("bad", [(1.9,), (True,), ("1",), (None,), [1, 2], (1, 2.0)])
def test_anything_but_whole_numbers_is_refused(build, bad):
    c = build(driver=False)
    c.set_scan_openings(bad)
    wait_until(c, lambda: c.state().banner is not None)
    assert c.scan_openings == ALL_POSITIONS


def test_the_switch_is_checked_before_the_values(build):
    c = build(driver=False, skip_empty=False)
    c.set_scan_openings((None, "x"))
    wait_until(c, lambda: c.state().banner is not None)
    assert "OPTICFILM_SKIP_EMPTY" in c.state().banner.message
