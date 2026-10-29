"""The front-panel Eject button: parsing the doctor report and acting on a press."""

import threading
import time

import pytest

from controller_helpers import open_new, scan_when_ready, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.messages import holder_failed_text
from opticfilm_linux.app.status import StatusPoller
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
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


class ButtonScanner(FakeScanner):
    """A fake with a scripted button: one entry per status poll; the last one repeats."""

    def __init__(
        self, sweep_dir, buttons, status=ScannerStatus.READY, fail=False, gate=None, **kwargs
    ):
        super().__init__(sweep_dir, (status,), **kwargs)
        self._buttons = list(buttons)
        self._fail, self._gate = fail, gate
        self._index = 0
        self.polls = 0
        self.ejects = 0
        self._current: bool | None = None

    def status(self):
        with self._lock:
            self.polls += 1
            self._current = self._buttons[min(self._index, len(self._buttons) - 1)]
            self._index += 1
        return super().status()

    def eject_pressed(self):
        return self._current

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
    time.sleep(0.2)
    c.tick()
    assert scanner.ejects == 1


def test_a_press_while_a_scan_is_running_is_dropped(build):
    release = threading.Event()
    c, scanner = build([False], before_scan=lambda: release.wait(30))
    try:
        scan_when_ready(c)
        scanner._buttons, scanner._index = [False, True, False], 0
        _settle(c, scanner, scanner.polls + 4)
        assert scanner.ejects == 0
    finally:
        release.set()


def test_a_press_on_a_read_only_batch_is_dropped(sweep600_dir, tmp_path):
    first = Controller(lambda _dpi: ButtonScanner(sweep600_dir, [False]))
    open_new(first, tmp_path / "b")
    first.close_batch()
    wait_until(first, lambda: first.state().batch_name is None)
    scanner = ButtonScanner(sweep600_dir, [False, True])
    c = Controller(lambda _dpi: scanner)
    try:
        c.open_batch(tmp_path / "b", readonly=True)
        wait_until(c, lambda: c.state().batch_name is not None)
        time.sleep(0.3)
        c.tick()
        assert scanner.ejects == 0 and scanner.polls == 0
    finally:
        c.shutdown()
        first.shutdown()


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
        def eject_pressed(self):
            return None if self.polls == 3 else self._current

    scanner = Gappy(sweep600_dir, [False, False, True, True])
    poller = StatusPoller(scanner, time.monotonic, 5.0, lambda *_: None)
    edges = []
    for number in range(1, 5):
        poller.poll()
        deadline = time.monotonic() + 5
        while scanner.polls < number and time.monotonic() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)
        edges.append(poller.take_eject_press())
    assert edges == [False, False, False, False]  # the press after the gap is stale


def test_the_driver_reports_the_button_from_the_same_doctor_call(monkeypatch):
    scanner = DriverScanner()
    calls = []

    class _Result:
        returncode = 0
        output = "reg 0x01 = 0x22\nstatus word: 0xdc55\npresent: True\n== Button ==\n  eject\n"

    monkeypatch.setattr(scanner, "_require_driver", lambda: None)
    monkeypatch.setattr(scanner, "_run", lambda *_a, **_k: calls.append(1) or _Result())
    assert scanner.status() is ScannerStatus.READY
    assert scanner.eject_pressed() is True
    assert scanner.status() is ScannerStatus.READY  # cached: no second doctor
    assert scanner.eject_pressed() is True and len(calls) == 1
