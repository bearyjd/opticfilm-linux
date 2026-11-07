"""Load & scan (B5a): one click loads the next holder, waits for a fresh READY, then scans.

Fake scanner only. Every safety rule in the build plan has its own refusal test here.
"""

import logging
import threading
import time

import pytest

from controller_helpers import open_new, wait_until
from opticfilm_linux.app import load_and_scan as load_and_scan_module
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.holder import NEXT_STRIP_OFF, WAIT_BATCH, WAIT_HOLDER, WAIT_SCAN
from opticfilm_linux.app.load_and_scan import (
    LOAD_SCAN_OFF,
    NOT_RELEASED,
    READY_TIMEOUT,
    SCANNER_NOT_WAITING,
    STOPPED_ANOMALY,
    STOPPED_CLOSING,
)
from opticfilm_linux.app.messages import holder_failed_text
from opticfilm_linux.scanner.base import ScanError, ScannerStatus, StatusReading
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.holder_latch import HolderLatch

LATCHED = "Holder: latched"
RELEASED = "Holder: released"
UNKNOWN = "Holder: unknown after power-on or a fault; check by hand before scanning"
SHORT = 5.0


class LoadScanner(FakeScanner):
    """Status set by the test; a load turns the scanner READY (unless told not to)."""

    def __init__(self, sweep_dir, **kwargs):
        super().__init__(sweep_dir, **kwargs)
        self.usb_address = (1, 2)
        self.current_status = ScannerStatus.NOT_READY
        self.after_load = ScannerStatus.READY
        self.during_load = None  # called inside the load, before it returns
        self.calls = []
        self.scans = 0
        self.pressed = False
        self.on_ready = None  # called once, by the first status read that answers READY

    def status(self):
        return self.current_status

    def status_reading(self):
        hook = self.on_ready
        if hook is not None and self.current_status is ScannerStatus.READY:
            self.on_ready = None
            hook()
        return StatusReading(self.current_status, self.pressed)

    def load_next_strip(self):
        self.calls.append("load")
        super().load_next_strip()
        if self.during_load is not None:
            self.during_load()
        self.current_status = self.after_load

    def eject(self):
        self.calls.append("eject")
        self.eject_outcome = "ejected"
        self.current_status = ScannerStatus.NOT_READY

    def scan_holder(self, settings):
        self.scans += 1
        return super().scan_holder(settings)


@pytest.fixture
def build(tmp_path, monkeypatch, sweep600_dir):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    made = []

    def make(**kwargs):
        scanner = LoadScanner(sweep600_dir)
        options = {"next_strip": True, "load_and_scan": True, **kwargs}
        controller = Controller(lambda _dpi: scanner, **options)
        made.append(controller)
        return controller, scanner

    yield make
    for controller in made:
        controller.shutdown()


def released(controller, scanner, tmp_path):
    """A batch is open and the app saw an eject finish: the start state Load & scan needs."""
    open_new(controller, tmp_path / "batch")
    controller.load_next_holder()
    wait_until(controller, lambda: controller.state().holder_latch_text == LATCHED, SHORT)
    wait_until(controller, lambda: not controller.state().holder_busy, SHORT)
    controller.eject_holder()
    wait_until(controller, lambda: controller.state().holder_latch_text == RELEASED, SHORT)
    if controller.load_and_scan_enabled:
        wait_until(controller, lambda: controller.state().can_load_and_scan, SHORT)
    scanner.calls.clear()
    scanner.load_calls = 0


def banner_text(controller):
    banner = controller.state().banner
    return banner.message if banner is not None else None


def settle(controller, seconds=0.3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        controller.tick()
        time.sleep(0.01)


def wait_phase(controller, phase, timeout=SHORT):
    wait_until(controller, lambda: controller.state().load_scan_phase == phase, timeout)


# -- the happy path -------------------------------------------------------------------------


def test_one_click_loads_once_waits_for_ready_and_scans_once(build, tmp_path, caplog):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    caplog.set_level(logging.INFO, logger="opticfilm_linux.app.load_and_scan")

    controller.load_and_scan()

    wait_until(controller, lambda: any(t.stage.value == "review" for t in controller.state().trays))
    wait_phase(controller, "idle")
    state = controller.state()
    assert scanner.load_calls == 1
    assert scanner.calls == ["load"]
    assert scanner.scans == 1
    assert state.holder_latch_text == LATCHED
    assert not state.holder_busy
    assert not state.can_load_and_scan  # once per RELEASED state
    assert "seated and latched" in caplog.text  # the click is logged as the confirmation


def test_second_click_after_success_is_refused(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    controller.load_and_scan()
    wait_until(controller, lambda: scanner.scans == 1)
    wait_phase(controller, "idle")
    controller.load_and_scan()
    settle(controller)
    assert scanner.load_calls == 1
    assert scanner.scans == 1


# -- it stops at the first anomaly and never scans after it --------------------------------


def test_load_error_stops_after_one_load_with_the_power_cycle_banner(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    scanner.load_error = ScanError("cassette not taken")

    controller.load_and_scan()

    wait_phase(controller, "failed")
    wait_until(controller, lambda: not controller.state().holder_busy, SHORT)
    state = controller.state()
    assert scanner.load_calls == 1
    assert scanner.scans == 0
    assert state.holder_latch_text == UNKNOWN
    assert state.banner.message == holder_failed_text("load")
    assert "cassette not taken" in state.banner.detail


def test_never_ready_times_out_on_its_own_clock_unlatched_and_unscanned(build, tmp_path):
    ticks = iter(range(0, 10_000))
    controller, scanner = build(load_clock=lambda: float(next(ticks)))  # one second a read
    scanner.after_load = ScannerStatus.NOT_READY
    released(controller, scanner, tmp_path)

    controller.load_and_scan()

    wait_phase(controller, "failed", timeout=20)
    state = controller.state()
    assert scanner.load_calls == 1
    assert scanner.scans == 0
    assert state.holder_latch_text == UNKNOWN
    assert state.banner.message == READY_TIMEOUT  # its own timeout, not "Scanner not responding"
    assert not state.can_scan and not state.can_load_and_scan


def test_a_ready_seen_before_the_load_returned_is_ignored(build, tmp_path):
    controller, scanner = build(load_ready_timeout_s=0.5)

    def ready_mid_load():
        scanner.current_status = ScannerStatus.READY
        poller = controller._session.poller
        deadline = time.monotonic() + SHORT
        while poller.current() is not ScannerStatus.READY and time.monotonic() < deadline:
            poller.poll()
            time.sleep(0.01)
        assert poller.current() is ScannerStatus.READY

    scanner.during_load = ready_mid_load
    scanner.after_load = ScannerStatus.NOT_READY
    released(controller, scanner, tmp_path)

    controller.load_and_scan()

    wait_phase(controller, "failed")
    assert scanner.scans == 0
    assert banner_text(controller) == READY_TIMEOUT


@pytest.mark.parametrize(
    "status", [ScannerStatus.ERROR, ScannerStatus.INTERRUPTED, ScannerStatus.NOT_CONNECTED]
)
def test_a_bad_status_during_the_wait_stops_without_a_scan(build, tmp_path, status):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    scanner.after_load = status

    controller.load_and_scan()

    wait_phase(controller, "failed")
    wait_until(controller, lambda: controller.state().holder_latch_text == UNKNOWN, SHORT)
    assert scanner.scans == 0
    assert scanner.load_calls == 1


def test_close_request_during_the_wait_stops_the_chain(build, tmp_path):
    controller, scanner = build()
    scanner.after_load = ScannerStatus.NOT_READY  # waits until the 30 s bound
    released(controller, scanner, tmp_path)
    controller.load_and_scan()
    wait_phase(controller, "waiting_ready")

    controller.close_batch()  # refused while the holder job runs, but it stops the chain

    wait_phase(controller, "failed")
    wait_until(controller, lambda: not controller.state().holder_busy, SHORT)
    assert banner_text(controller) == STOPPED_CLOSING
    scanner.current_status = ScannerStatus.READY
    settle(controller)
    assert scanner.scans == 0
    controller.close_batch()
    wait_until(controller, lambda: controller.state().batch_name is None, SHORT)
    assert scanner.scans == 0


def test_shutdown_during_the_wait_returns_promptly_without_a_scan(build, tmp_path):
    controller, scanner = build()
    scanner.after_load = ScannerStatus.NOT_READY
    released(controller, scanner, tmp_path)
    controller.load_and_scan()
    wait_phase(controller, "waiting_ready")

    started = time.monotonic()
    controller.shutdown()

    assert time.monotonic() - started < SHORT
    assert scanner.scans == 0


def test_eject_and_front_panel_press_during_the_wait_do_not_move_the_holder(build, tmp_path):
    controller, scanner = build()
    scanner.after_load = ScannerStatus.NOT_READY
    released(controller, scanner, tmp_path)
    controller.load_and_scan()
    wait_phase(controller, "waiting_ready")

    controller.eject_holder()  # the E key
    settle(controller, 0.3)
    assert banner_text(controller) is None  # E is ignored silently
    scanner.pressed = True  # the front-panel button
    settle(controller, 0.5)

    assert scanner.calls == ["load"]
    assert controller.state().holder_busy
    assert banner_text(controller) == WAIT_HOLDER  # the press is answered, not replayed later
    scanner.pressed = False
    scanner.current_status = ScannerStatus.READY
    wait_until(controller, lambda: scanner.scans == 1)
    wait_phase(controller, "idle")
    assert scanner.calls == ["load"]  # no eject after the hand-over either


# -- refusals: each starts nothing ------------------------------------------------------------


def assert_refused(controller, scanner, message):
    controller.load_and_scan()
    settle(controller)
    assert scanner.load_calls == 0
    assert scanner.scans == 0
    assert banner_text(controller) == message


def test_refused_when_the_setting_is_off(build, tmp_path):
    controller, scanner = build(load_and_scan=False)
    released(controller, scanner, tmp_path)
    assert not controller.state().can_load_and_scan
    assert_refused(controller, scanner, LOAD_SCAN_OFF)


def test_setting_is_off_by_default(sweep600_dir):
    controller = Controller(lambda _dpi: FakeScanner(sweep600_dir))
    try:
        assert controller.load_and_scan_enabled is False
    finally:
        controller.shutdown()


def test_setting_can_be_switched_on_and_off(build, tmp_path):
    controller, scanner = build(load_and_scan=False)
    released(controller, scanner, tmp_path)
    controller.set_load_and_scan(True)
    wait_until(controller, lambda: controller.state().can_load_and_scan, SHORT)
    controller.set_load_and_scan(False)
    wait_until(controller, lambda: not controller.state().can_load_and_scan, SHORT)


def test_refused_from_an_unknown_latch(build, tmp_path):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")  # a new session: UNKNOWN, never RELEASED
    settle(controller)
    assert not controller.state().can_load_and_scan
    assert_refused(controller, scanner, NOT_RELEASED)


def test_refused_while_latched(build, tmp_path):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    controller.load_next_holder()
    wait_until(controller, lambda: controller.state().holder_latch_text == LATCHED, SHORT)
    wait_until(controller, lambda: not controller.state().holder_busy, SHORT)
    scanner.load_calls = 0
    scanner.current_status = ScannerStatus.NOT_READY
    assert_refused(controller, scanner, NOT_RELEASED)


def test_refused_unless_a_fresh_poll_says_not_ready(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    scanner.current_status = ScannerStatus.READY  # something is in the slot again
    wait_until(controller, lambda: controller.state().scanner is ScannerStatus.READY, SHORT)
    assert not controller.state().can_load_and_scan
    assert_refused(controller, scanner, SCANNER_NOT_WAITING)


def test_refused_while_the_interrupted_marker_is_pending(build, tmp_path, monkeypatch):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    monkeypatch.setattr(load_and_scan_module.interrupted, "is_pending", lambda: True)
    controller.load_and_scan()
    settle(controller)
    assert scanner.load_calls == 0
    assert "Confirm scanner reset" in banner_text(controller)


def test_the_interrupted_marker_shows_the_control_disabled(build, tmp_path, monkeypatch):
    """Read outside the lock on each refresh, so the button is not enabled and then refused."""
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    monkeypatch.setattr(load_and_scan_module.interrupted, "is_pending", lambda: True)
    wait_until(controller, lambda: not controller.state().can_load_and_scan, SHORT)
    monkeypatch.setattr(load_and_scan_module.interrupted, "is_pending", lambda: False)
    wait_until(controller, lambda: controller.state().can_load_and_scan, SHORT)


def test_the_marker_is_not_read_while_the_setting_is_off(build, tmp_path, monkeypatch):
    controller, scanner = build(load_and_scan=False)
    released(controller, scanner, tmp_path)
    reads = []
    monkeypatch.setattr(
        load_and_scan_module.interrupted, "is_pending", lambda: reads.append(1) or False
    )
    settle(controller, 0.3)
    assert reads == []
    assert not controller.state().can_load_and_scan


def test_refused_while_a_cut_off_is_reported(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    scanner.current_status = ScannerStatus.INTERRUPTED
    wait_until(controller, lambda: controller.state().scanner is ScannerStatus.INTERRUPTED, SHORT)
    controller.load_and_scan()
    settle(controller)
    assert scanner.load_calls == 0
    assert banner_text(controller) is not None


def test_refused_while_next_strip_is_off(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    controller._next_strip = False
    wait_until(controller, lambda: not controller.state().can_load_and_scan, SHORT)
    assert_refused(controller, scanner, NEXT_STRIP_OFF)


def test_refused_for_a_read_only_batch(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    controller.close_batch()
    wait_until(controller, lambda: controller.state().batch_name is None, SHORT)
    controller.open_batch(tmp_path / "batch", readonly=True)
    wait_until(controller, lambda: controller.state().readonly, SHORT)
    controller.load_and_scan()
    settle(controller)
    assert scanner.load_calls == 0
    assert not controller.state().can_load_and_scan


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("_scans_queued", WAIT_SCAN),
        ("_holder_busy", WAIT_HOLDER),
        ("_lifecycle_pending", WAIT_BATCH),
    ],
    ids=["scan-queued", "holder-busy", "lifecycle-pending"],
)
def test_refused_while_other_work_is_queued(build, tmp_path, field, message):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    with controller._lock:
        setattr(controller, field, 1 if field != "_holder_busy" else True)
    try:
        assert_refused(controller, scanner, message)
    finally:
        with controller._lock:
            setattr(controller, field, 0 if field != "_holder_busy" else False)


def test_refused_while_an_export_job_is_out(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    with controller._lock:
        controller._long_jobs = 1
    try:
        controller.load_and_scan()
        settle(controller)
        assert scanner.load_calls == 0
    finally:
        with controller._lock:
            controller._long_jobs = 0


# -- only the explicit click starts it --------------------------------------------------------


def test_auto_eject_and_a_front_panel_press_never_start_a_load(build, tmp_path):
    controller, scanner = build(auto_eject=True)
    open_new(controller, tmp_path / "batch")
    controller.load_next_holder()
    wait_until(controller, lambda: controller.state().can_scan, SHORT)
    controller.scan()
    wait_until(controller, lambda: controller.state().holder_latch_text == RELEASED)  # auto-eject
    wait_until(controller, lambda: controller.state().can_load_and_scan, SHORT)
    scanner.pressed = True
    settle(controller, 0.5)
    assert scanner.calls.count("load") == 1  # only the manual one before the scan
    assert scanner.scans == 1


def test_a_click_while_a_click_is_running_starts_nothing_more(build, tmp_path):
    controller, scanner = build()
    gate = threading.Event()
    released(controller, scanner, tmp_path)
    scanner.during_load = lambda: gate.wait(SHORT)
    controller.load_and_scan()
    wait_phase(controller, "loading")
    controller.load_and_scan()  # a key repeat
    gate.set()
    wait_until(controller, lambda: scanner.scans == 1)
    wait_phase(controller, "idle")
    assert scanner.load_calls == 1


def test_bad_status_message_names_the_status(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    scanner.after_load = ScannerStatus.ERROR
    controller.load_and_scan()
    wait_phase(controller, "failed")
    wait_until(controller, lambda: banner_text(controller) == STOPPED_ANOMALY, SHORT)


def test_a_manual_scan_during_the_wait_is_refused_and_only_one_scan_runs(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    scanner.after_load = ScannerStatus.NOT_READY
    controller.load_and_scan()
    wait_phase(controller, "waiting_ready")

    controller.scan()  # holder still reserved by the chain
    wait_until(controller, lambda: banner_text(controller) == WAIT_HOLDER, SHORT)
    scanner.current_status = ScannerStatus.READY

    wait_until(controller, lambda: scanner.scans == 1)
    wait_phase(controller, "idle")
    settle(controller)
    assert scanner.scans == 1
    assert scanner.load_calls == 1


def test_a_not_ready_answer_asked_before_the_release_does_not_count(build, tmp_path):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    controller.load_next_holder()
    wait_until(controller, lambda: controller.state().holder_latch_text == LATCHED, SHORT)
    wait_until(controller, lambda: not controller.state().holder_busy, SHORT)
    scanner.current_status = ScannerStatus.NOT_READY
    wait_until(controller, lambda: controller.state().scanner is ScannerStatus.NOT_READY, SHORT)
    poller = controller._session.poller
    wait_until(controller, lambda: poller.wait_idle(0.0), SHORT)
    poller.poll = lambda: None  # frozen: its last NOT_READY was asked before the release
    controller.eject_holder()
    wait_until(controller, lambda: controller.state().holder_latch_text == RELEASED, SHORT)
    scanner.load_calls = 0
    assert not controller.state().can_load_and_scan
    assert_refused(controller, scanner, SCANNER_NOT_WAITING)


def test_a_latch_lost_before_the_hand_over_stops_without_a_scan(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    scanner.on_ready = lambda: controller._remember_holder_latch(HolderLatch.UNKNOWN)

    controller.load_and_scan()

    wait_phase(controller, "failed")
    wait_until(controller, lambda: banner_text(controller) == STOPPED_ANOMALY, SHORT)
    settle(controller)
    assert scanner.scans == 0
    assert scanner.load_calls == 1


def test_a_batch_change_pending_at_the_hand_over_stops_without_a_scan(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)

    def batch_change():
        with controller._lock:
            controller._lifecycle_pending += 1

    scanner.on_ready = batch_change
    controller.load_and_scan()

    wait_phase(controller, "failed")
    wait_until(controller, lambda: banner_text(controller) == STOPPED_CLOSING, SHORT)
    settle(controller)
    with controller._lock:
        controller._lifecycle_pending -= 1
    assert scanner.scans == 0
    assert scanner.load_calls == 1


def test_closing_the_window_during_the_wait_counts_as_a_running_scan(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    scanner.after_load = ScannerStatus.NOT_READY
    controller.load_and_scan()
    wait_phase(controller, "waiting_ready")
    assert controller.shutdown_decision() == "scan_running"


@pytest.mark.parametrize("phase", ["loading", "waiting_ready"])
def test_the_load_and_wait_count_as_a_pending_scan(build, tmp_path, phase):
    """The window's "Wait, then close" waits on scan_pending: it must not close early."""
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    gate = threading.Event()
    if phase == "loading":
        scanner.during_load = lambda: gate.wait(SHORT)
    else:
        scanner.after_load = ScannerStatus.NOT_READY
    controller.load_and_scan()
    wait_phase(controller, phase)
    state = controller.state()
    assert state.scan_pending
    assert not state.can_scan
    gate.set()
    scanner.current_status = ScannerStatus.READY
    wait_until(controller, lambda: scanner.scans == 1)
    wait_phase(controller, "idle")
    wait_until(controller, lambda: not controller.state().scan_pending, SHORT)


def test_a_failed_chain_is_not_a_pending_scan(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    scanner.load_error = ScanError("cassette not taken")
    controller.load_and_scan()
    wait_phase(controller, "failed")
    assert not controller.state().scan_pending
    assert controller.shutdown_decision() == "ok"


def test_a_close_request_before_the_job_starts_sends_no_load(build, tmp_path):
    controller, scanner = build()
    released(controller, scanner, tmp_path)
    gate = threading.Event()
    controller._holder_pool.submit(lambda: gate.wait(SHORT))  # the holder worker is busy
    controller.load_and_scan()
    controller._load_scan_cancel.set()  # the close request, without cancelling the future
    gate.set()
    wait_phase(controller, "failed")
    wait_until(controller, lambda: banner_text(controller) == STOPPED_CLOSING, SHORT)
    assert scanner.load_calls == 0
