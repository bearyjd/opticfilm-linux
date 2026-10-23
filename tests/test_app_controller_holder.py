"""Eject and load-next-holder from the window, and plain failure banners."""

import threading

import pytest

from controller_helpers import open_new, scan_when_ready, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.messages import POWER_CYCLE_ADVICE, holder_failed_text
from opticfilm_linux.model import ScanSettings
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner

CAL_FAIL = "phase cal_dark_b: CalibrationError: dark_b is residual. Power the scanner OFF."


class RecordingScanner(FakeScanner):
    def __init__(self, sweep_dir, statuses, fail=None, gate=None, **kwargs):
        super().__init__(sweep_dir, statuses, **kwargs)
        self.calls: list[str] = []
        self._fail = fail
        self._gate = gate

    def _move(self, name: str) -> None:
        self.calls.append(name)
        if self._gate is not None:
            self._gate.wait(30)
        if self._fail is not None:
            raise ScanError(self._fail)

    def eject(self) -> None:
        self._move("eject")

    def load_next_strip(self) -> None:
        self._move("load_next_strip")


class ScanOnly:
    """A scanner backend without holder control."""

    def __init__(self, inner: FakeScanner) -> None:
        self._inner = inner

    def status(self) -> ScannerStatus:
        return self._inner.status()

    def scan_holder(self, settings: ScanSettings):
        return self._inner.scan_holder(settings)

    def eject(self) -> None:
        raise AssertionError("never called")


@pytest.fixture
def build(sweep600_dir):
    made: list[Controller] = []

    def make(status=ScannerStatus.NOT_READY, next_strip=True, holder=True, **kwargs):
        scanner = RecordingScanner(sweep600_dir, (status,), **kwargs)
        backend = scanner if holder else ScanOnly(scanner)
        controller = Controller(lambda _dpi: backend, next_strip=next_strip)
        made.append(controller)
        return controller, scanner

    yield make
    for controller in made:
        controller.shutdown()


def test_eject_and_load_next_reach_the_scanner(build, tmp_path):
    c, scanner = build()
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject and c.state().can_load_next)
    c.eject_holder()
    wait_until(c, lambda: scanner.calls == ["eject"] and not c._holder_busy)
    c.load_next_holder()
    wait_until(c, lambda: scanner.calls == ["eject", "load_next_strip"])
    wait_until(c, lambda: c.state().can_eject)
    assert c.state().banner is None


def test_load_next_is_refused_while_next_strip_is_switched_off(build, tmp_path):
    c, scanner = build(next_strip=False)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    assert not c.state().can_load_next and not c.state().next_strip_enabled
    c.load_next_holder()
    wait_until(c, lambda: c.state().banner is not None)
    assert "switched off" in c.state().banner.message and scanner.calls == []


def test_a_read_only_batch_refuses_holder_commands(build, tmp_path):
    first, _ = build()
    open_new(first, tmp_path / "b")
    first.close_batch()
    wait_until(first, lambda: first.state().batch_name is None)
    c, scanner = build()
    c.open_batch(tmp_path / "b", readonly=True)
    wait_until(c, lambda: c.state().readonly and c.state().batch_name == "Test")
    assert not c.state().can_eject and not c.state().can_load_next
    c.eject_holder()
    wait_until(c, lambda: c.state().banner is not None)
    assert "read-only" in c.state().banner.message and scanner.calls == []


@pytest.mark.parametrize(
    ("action", "name"), [("eject_holder", "eject"), ("load_next_holder", "load")]
)
def test_a_failed_move_gives_a_plain_banner_with_the_driver_detail(build, tmp_path, action, name):
    c, _ = build(fail="eject failed. Driver said: usb timeout")
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    getattr(c, action)()
    wait_until(c, lambda: c.state().banner is not None)
    banner = c.state().banner
    assert banner.message == holder_failed_text(name)
    assert "lights go out" in banner.message and "usb timeout" in banner.detail
    wait_until(c, lambda: c.state().can_eject)  # the buttons come back after a failure


def test_a_scanner_without_holder_control_disables_the_buttons(build, tmp_path):
    c, _ = build(holder=False)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().scanner is ScannerStatus.NOT_READY)
    assert not c.state().can_eject and not c.state().can_load_next
    c.eject_holder()
    wait_until(c, lambda: c.state().banner is not None)
    assert "cannot move the holder" in c.state().banner.message


def test_a_second_click_while_moving_is_ignored(build, tmp_path):
    gate = threading.Event()
    c, scanner = build(gate=gate)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_eject)
    c.eject_holder()
    wait_until(c, lambda: scanner.calls == ["eject"] and not c.state().can_eject)
    assert not c.state().can_load_next
    c.eject_holder()
    gate.set()
    wait_until(c, lambda: c.state().can_eject)
    assert scanner.calls == ["eject"] and c.state().banner is None


def test_holder_commands_wait_out_a_running_scan(build, tmp_path):
    gate = threading.Event()
    c, scanner = build(status=ScannerStatus.READY, before_scan=lambda: gate.wait(30))
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    wait_until(c, lambda: c.state().scan_pending)
    assert not c.state().can_eject
    c.eject_holder()
    wait_until(c, lambda: c.state().banner is not None)
    assert "scan" in c.state().banner.message and scanner.calls == []
    gate.set()


@pytest.mark.parametrize(
    ("error", "message"),
    [(CAL_FAIL, POWER_CYCLE_ADVICE), ("the disk is full", "Tray 1 failed")],
)
def test_a_failed_tray_banner_is_plain_and_keeps_the_detail(build, tmp_path, error, message):
    def refuse() -> None:
        raise ScanError(error)

    c, _ = build(status=ScannerStatus.READY, before_scan=refuse)
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_scan)
    c.scan()  # fails at once, so can_scan may never be seen false
    wait_until(c, lambda: c.state().banner is not None)
    assert c.state().banner.message == message and error in c.state().banner.detail
