"""The front-panel Eject button: parsing the doctor report and acting on a press."""

import threading
import time

import pytest

from controller_helpers import open_new, scan_when_ready, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.holder import WAIT_SCAN
from opticfilm_linux.app.messages import holder_failed_text
from opticfilm_linux.app.status import StatusPoller
from opticfilm_linux.scanner.base import ScanError, ScannerStatus, StatusReading
from opticfilm_linux.scanner.driver import DriverScanner, parse_doctor_button
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.select import EJECT_BUTTON_ENV, eject_button_enabled


@pytest.mark.parametrize(
    ("text", "pressed"),
    [
        ("== Button ==\n  eject\n", True),
        ("== Scanner ==\n  x\n== Button ==\n  eject\n== Other ==\n  idle\n", True),
        ("== Button ==\n  idle\n", False),
        ("== Button ==\n  positive\n", False),
        ("== Button ==\n  negative\n", False),
        ("== Button ==\n  custom\n", False),
        ("== Button ==\n  0x12\n", False),
        ("== Button ==\n  unreadable: timeout\n", False),
        ("== Scanner ==\n  eject\n", False),  # the word outside the Button section
        ("== Button ==\n  idle\n== Other ==\n  eject\n", False),
        ("", False),
        ("== Button ==\n", False),
        ("== Button\n eject", False),
    ],
)
def test_parse_doctor_button(text, pressed):
    assert parse_doctor_button(text) is pressed


def test_the_button_setting_is_on_unless_switched_off(monkeypatch):
    monkeypatch.delenv(EJECT_BUTTON_ENV, raising=False)
    assert eject_button_enabled() is True
    monkeypatch.setenv(EJECT_BUTTON_ENV, "0")
    assert eject_button_enabled() is False
    monkeypatch.setenv(EJECT_BUTTON_ENV, "1")
    assert eject_button_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "False", "NO", " Off ", "\toff\n"])
def test_every_off_spelling_switches_the_button_off(monkeypatch, value):
    monkeypatch.setenv(EJECT_BUTTON_ENV, value)
    assert eject_button_enabled() is False


@pytest.mark.parametrize("value", ["", " ", "1", "true", "on", "yes", "garbage", "of", "00"])
def test_an_empty_or_unknown_value_leaves_the_button_on(monkeypatch, value):
    monkeypatch.setenv(EJECT_BUTTON_ENV, value)
    assert eject_button_enabled() is True


class ButtonScanner(FakeScanner):
    """A fake with a scripted button: one entry per status poll; the last one repeats."""

    def __init__(
        self, sweep_dir, buttons, status=ScannerStatus.READY, fail=False, gate=None, **kwargs
    ):
        super().__init__(sweep_dir, (status,), **kwargs)
        self._status_value = status
        self._buttons = list(buttons)
        self._fail, self._gate = fail, gate
        self._index = 0
        self.polls = 0
        self.ejects = 0

    def status_reading(self):
        with self._lock:
            self.polls += 1
            pressed = self._buttons[min(self._index, len(self._buttons) - 1)]
            self._index += 1
        return StatusReading(self._status_value, pressed)

    def status(self):
        return self.status_reading().status

    def eject(self):
        self.ejects += 1
        if self._gate is not None:
            self._gate.wait(30)
        if self._fail:
            raise ScanError("boom")

    def load_next_strip(self):
        return None


@pytest.fixture
def build(sweep600_dir, tmp_path):
    made: list[Controller] = []

    def make(buttons, enabled=None, **kwargs):
        scanner = ButtonScanner(sweep600_dir, buttons, **kwargs)
        extra = {} if enabled is None else {"eject_button": enabled}
        controller = Controller(lambda _dpi: scanner, **extra)
        made.append(controller)
        open_new(controller, tmp_path / "b")
        return controller, scanner

    yield make
    for controller in made:
        controller.shutdown()


def _settle(controller, scanner, polls):
    wait_until(controller, lambda: scanner.polls >= polls)
    time.sleep(0.1)
    controller.tick()


def test_a_press_on_an_idle_scanner_ejects(build):
    c, scanner = build([False, False, True])
    wait_until(c, lambda: scanner.ejects == 1)


def test_the_button_is_on_by_default(build):
    c, scanner = build([False, True], enabled=None)
    wait_until(c, lambda: scanner.ejects == 1)


def test_a_disabled_setting_ignores_the_button(build):
    c, scanner = build([False, True], enabled=False)
    _settle(c, scanner, 6)
    assert scanner.ejects == 0


def test_a_press_already_there_at_attach_is_discarded(build):
    c, scanner = build([True, True, True])
    _settle(c, scanner, 6)
    assert scanner.ejects == 0


def test_a_held_press_ejects_once_and_a_new_press_ejects_again(build):
    c, scanner = build([False, True, True, True, True, False, False, True])
    wait_until(c, lambda: scanner.ejects >= 1)
    time.sleep(0.2)
    c.tick()
    assert scanner.ejects == 1  # held through several polls: still one
    wait_until(c, lambda: scanner.ejects == 2)


def test_a_press_while_the_holder_is_busy_is_dropped_not_queued(build):
    gate = threading.Event()
    c, scanner = build([False, True, False, True, True, False], gate=gate)
    wait_until(c, lambda: scanner.ejects == 1)  # the first press; its eject is held open
    wait_until(c, lambda: scanner.polls >= 6)
    gate.set()
    wait_until(c, lambda: not c.state().holder_busy)
    c.tick()
    assert scanner.ejects == 1
    assert c.state().banner is None  # a dropped press is silent: nothing was refused either


def test_a_press_while_a_scan_is_running_is_dropped(build):
    release = threading.Event()
    c, scanner = build([False], before_scan=lambda: release.wait(30))
    try:
        scan_when_ready(c)
        scanner._buttons, scanner._index = [False, True, False], 0
        _settle(c, scanner, scanner.polls + 4)
        assert scanner.ejects == 0
        assert c.state().banner.message == WAIT_SCAN
    finally:
        release.set()
    wait_until(c, lambda: c.state().can_scan)
    _settle(c, scanner, scanner.polls + 4)
    assert scanner.ejects == 0  # the refused press must not run after the scan


class _StubPoller:
    def __init__(self, press=True, status=ScannerStatus.READY):
        self._press, self._status = press, status

    def take_eject_press(self):
        press, self._press = self._press, False
        return press

    def current(self):
        return self._status


def test_a_press_on_a_read_only_batch_is_dropped(build, sweep600_dir, tmp_path):
    first, _ = build([False])
    first.close_batch()
    wait_until(first, lambda: first.state().batch_name is None)
    scanner = ButtonScanner(sweep600_dir, [False])
    c = Controller(lambda _dpi: scanner)
    try:
        c.open_batch(tmp_path / "b", readonly=True)
        wait_until(c, lambda: c.state().readonly)
        asked = []
        c.eject_holder = lambda: asked.append(1)  # type: ignore[method-assign]
        c._act_on_eject_button(_StubPoller())  # a read-only batch has no poller; force the press
        assert asked == [] and scanner.ejects == 0
    finally:
        c.shutdown()


def test_a_press_is_not_acted_on_when_the_pollers_own_answer_is_an_error(build):
    c, scanner = build([False])
    wait_until(c, lambda: c.state().can_eject)  # the (one poll behind) state still says idle
    asked = []
    c.eject_holder = lambda: asked.append(1)  # type: ignore[method-assign]
    for status in (ScannerStatus.ERROR, ScannerStatus.INTERRUPTED, ScannerStatus.BUSY):
        c._act_on_eject_button(_StubPoller(status=status))
    assert asked == []
    c._act_on_eject_button(_StubPoller(status=ScannerStatus.READY))  # the control: it does eject
    assert asked == [1]


def test_a_press_read_beside_an_error_status_is_never_a_press(sweep600_dir):
    scanner = ButtonScanner(sweep600_dir, [False, True, True, False, True], ScannerStatus.ERROR)
    poller = StatusPoller(scanner, time.monotonic, 5.0, lambda *_: None)
    for number in range(1, 6):
        _poll_once(poller, scanner, number)
        assert poller.take_eject_press() is False


def test_the_press_after_an_error_reading_is_a_new_baseline(sweep600_dir):
    scanner = ButtonScanner(sweep600_dir, [False, True, True])
    poller = StatusPoller(scanner, time.monotonic, 5.0, lambda *_: None)
    _poll_once(poller, scanner, 1)  # baseline: released
    scanner._status_value = ScannerStatus.ERROR
    _poll_once(poller, scanner, 2)  # a press, but the scanner is in error
    scanner._status_value = ScannerStatus.READY
    _poll_once(poller, scanner, 3)  # still held: stale, not a fresh press
    assert poller.take_eject_press() is False


def test_an_unconsumed_press_expires(sweep600_dir):
    now = [100.0]
    scanner = ButtonScanner(sweep600_dir, [False, True])
    poller = StatusPoller(scanner, lambda: now[0], 5.0, lambda *_: None, press_ttl_s=2.0)
    _poll_once(poller, scanner, 1)
    _poll_once(poller, scanner, 2)
    now[0] += 1.9
    assert poller.take_eject_press() is True
    assert poller.take_eject_press() is False  # taken once
    _poll_once(poller, scanner, 3)  # still held: no new press
    scanner._buttons, scanner._index = [False, True], 0
    _poll_once(poller, scanner, 4)  # released
    _poll_once(poller, scanner, 5)  # pressed again
    now[0] += 2.5  # the UI timer was paused
    assert poller.take_eject_press() is False


def _poll_once(poller, scanner, expected_polls):
    """Poll and wait until that answer has been fully recorded (no sleeping)."""
    poller.poll()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with poller._lock:
            if poller._started is None and scanner.polls >= expected_polls:
                return
        time.sleep(0.002)
    raise AssertionError("the poll did not finish")


def test_a_press_when_the_scanner_is_not_idle_is_dropped(build):
    c, scanner = build([False, True, False], status=ScannerStatus.BUSY)
    _settle(c, scanner, 4)
    assert scanner.ejects == 0


def test_a_failed_eject_shows_the_banner_and_is_not_retried(build):
    c, scanner = build([False, True, True, True, True], fail=True)
    wait_until(c, lambda: c.state().banner is not None)
    assert holder_failed_text("eject") in c.state().banner.message
    time.sleep(0.3)
    c.tick()
    assert scanner.ejects == 1


def test_a_reading_gap_makes_the_next_reading_the_new_baseline(sweep600_dir):
    class Gappy(ButtonScanner):
        def status_reading(self):
            reading = super().status_reading()
            return StatusReading(reading.status, None if self.polls == 3 else reading.eject_pressed)

    scanner = Gappy(sweep600_dir, [False, False, True, True])
    poller = StatusPoller(scanner, time.monotonic, 5.0, lambda *_: None)
    edges = []
    for number in range(1, 5):
        _poll_once(poller, scanner, number)
        edges.append(poller.take_eject_press())
    assert edges == [False, False, False, False]  # the press after the gap is stale


_REPORT = "reg 0x01 = 0x22\nstatus word: 0xdc55\npresent: True\n== Button ==\n  eject\n"


def _driver(monkeypatch, output=_REPORT, returncode=0):
    scanner = DriverScanner()
    calls = []

    class _Result:
        pass

    result = _Result()
    result.returncode, result.output = returncode, output
    monkeypatch.setattr(scanner, "_require_driver", lambda: None)
    monkeypatch.setattr(scanner, "_run", lambda *_a, **_k: calls.append(1) or result)
    return scanner, calls


def test_the_driver_reports_the_button_from_the_same_doctor_call(monkeypatch):
    scanner, calls = _driver(monkeypatch)
    assert scanner.status_reading() == StatusReading(ScannerStatus.READY, True)
    assert scanner.status() is ScannerStatus.READY  # cached: no second doctor
    assert scanner.status_reading().eject_pressed is True and len(calls) == 1


def test_the_driver_reads_no_button_when_the_report_ends_in_failure(monkeypatch):
    scanner, _ = _driver(monkeypatch, returncode=1)
    assert scanner.status_reading() == StatusReading(ScannerStatus.ERROR, None)


def test_the_driver_reads_no_button_while_a_power_cycle_is_needed(monkeypatch):
    scanner, _ = _driver(monkeypatch)
    scanner._needs_power_cycle = True  # the driver failed and nothing has re-enumerated yet
    assert scanner.status_reading() == StatusReading(ScannerStatus.ERROR, None)
    assert scanner._needs_power_cycle


def test_the_driver_keeps_the_button_for_a_scanner_without_a_holder(monkeypatch):
    report = "reg 0x01 = 0x22\npresent: False\n== Button ==\n  eject\n"
    scanner, _ = _driver(monkeypatch, output=report)
    assert scanner.status_reading() == StatusReading(ScannerStatus.NOT_READY, True)


def test_the_driver_reads_no_button_for_an_engine_state_it_refuses(monkeypatch):
    report = "reg 0x01 = 0x23\nstatus word: 0xdc55\npresent: True\n== Button ==\n  eject\n"
    scanner, _ = _driver(monkeypatch, output=report)
    assert scanner.status_reading() == StatusReading(ScannerStatus.ERROR, None)
