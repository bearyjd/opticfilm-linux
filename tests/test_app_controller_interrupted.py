"""The window after an interrupted scan: no scan, no holder move, until the owner confirms."""

import threading

import pytest

from controller_helpers import open_new, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.holder import ANOTHER_PROGRAM, REFUSED, SCANNER_INTERRUPTED
from opticfilm_linux.app.messages import scanner_text
from opticfilm_linux.app.state import build_state
from opticfilm_linux.scanner import driver as driver_module
from opticfilm_linux.scanner import interrupted
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.interrupted import ScannerInterrupted, ScannerRefused


class GuardedScanner(FakeScanner):
    """A fake scanner that reads the durable mark, as the real backend does."""

    def __init__(self, sweep_dir):
        super().__init__(sweep_dir)
        self.asked: list[str] = []  # every move that reached the backend
        self.moves: list[str] = []  # the ones it carried out

    def status(self) -> ScannerStatus:
        return ScannerStatus.INTERRUPTED if interrupted.is_pending() else ScannerStatus.READY

    def eject(self) -> None:
        self.asked.append("eject")
        interrupted.require_clear()
        self.moves.append("eject")

    def load_next_strip(self) -> None:
        self.asked.append("load")
        interrupted.require_clear()
        self.moves.append("load")


@pytest.fixture
def build(sweep600_dir):
    made: list[Controller] = []

    def make():
        scanner = GuardedScanner(sweep600_dir)
        controller = Controller(lambda _dpi: scanner, next_strip=True)
        made.append(controller)
        return controller, scanner

    yield make
    for controller in made:
        controller.shutdown()


def _interrupted_state(controller):
    wait_until(controller, lambda: controller.state().scanner is ScannerStatus.INTERRUPTED)
    return controller.state()


def test_an_interrupted_scanner_disables_scan_and_holder_and_offers_confirm(build, tmp_path):
    interrupted.begin("scan")
    controller, _scanner = build()
    open_new(controller, tmp_path / "b")
    state = _interrupted_state(controller)
    assert not state.can_scan and not state.can_eject and not state.can_load_next
    assert state.can_confirm_scanner
    assert "power-cycle" in state.scanner_text.lower()


@pytest.mark.parametrize("action", ["eject_holder", "load_next_holder"])
def test_a_holder_move_asked_for_anyway_is_refused_with_the_way_out(build, tmp_path, action):
    interrupted.begin("scan")
    controller, scanner = build()
    open_new(controller, tmp_path / "b")
    _interrupted_state(controller)
    getattr(controller, action)()
    wait_until(controller, lambda: controller.state().banner is not None)
    assert controller.state().banner.message == SCANNER_INTERRUPTED
    assert scanner.asked == []  # refused by the window itself, before the backend


def test_a_refusal_from_the_backend_itself_gets_the_same_banner(build, tmp_path, monkeypatch):
    controller, scanner = build()
    open_new(controller, tmp_path / "b")
    wait_until(controller, lambda: controller.state().can_eject)
    interrupted.begin("scan")  # another program was interrupted after the window looked
    controller.eject_holder()
    wait_until(controller, lambda: controller.state().banner is not None)
    assert controller.state().banner.message == SCANNER_INTERRUPTED
    assert scanner.moves == []


def test_confirm_clears_the_mark_and_scanning_is_allowed_again(build, tmp_path):
    interrupted.begin("scan")
    controller, _scanner = build()
    open_new(controller, tmp_path / "b")
    _interrupted_state(controller)
    controller.confirm_scanner_reset()
    wait_until(controller, lambda: controller.state().can_scan)
    assert not interrupted.is_pending()
    assert not controller.state().can_confirm_scanner


def test_confirm_is_refused_while_another_program_holds_the_scanner(build, tmp_path):
    interrupted.begin("scan")
    controller, _scanner = build()
    open_new(controller, tmp_path / "b")
    _interrupted_state(controller)
    held = threading.Event()
    release = threading.Event()

    def other_program() -> None:
        with driver_module._HW_LOCK:
            held.set()
            release.wait(30)

    thread = threading.Thread(target=other_program)
    thread.start()
    try:
        held.wait(10)
        controller.confirm_scanner_reset()
        wait_until(controller, lambda: controller.state().banner is not None)
    finally:
        release.set()
        thread.join(10)
    assert controller.state().banner.message == ANOTHER_PROGRAM
    assert interrupted.is_pending()


def test_a_refusal_that_is_not_an_interruption_says_so(build, tmp_path, monkeypatch):
    controller, scanner = build()
    open_new(controller, tmp_path / "b")
    wait_until(controller, lambda: controller.state().can_eject)

    def unwritable() -> None:
        raise ScannerRefused("cannot record that a scanner command is starting; refusing")

    monkeypatch.setattr(scanner, "eject", unwritable)
    controller.eject_holder()
    wait_until(controller, lambda: controller.state().banner is not None)
    assert controller.state().banner.message == REFUSED


def test_the_backend_refusal_type_is_a_scan_error():
    from opticfilm_linux.scanner.base import ScanError

    assert issubclass(ScannerInterrupted, ScanError)


@pytest.mark.parametrize("status", list(ScannerStatus))
def test_confirm_is_offered_only_for_an_interrupted_scanner(status):
    from opticfilm_linux.batch.model import BatchManifest

    state = build_state(
        BatchManifest(name="T"),
        preview_tokens={},
        scanner=status,
        scan_pending=False,
        readonly=False,
        selected=None,
        updating=frozenset(),
        progress={},
        scan_started={},
        now=0.0,
        banner=None,
        export=None,
        free_space=None,
        holder_control=True,
    )
    assert state.can_confirm_scanner is (status is ScannerStatus.INTERRUPTED)
    assert scanner_text(status)  # every status has words
    if status is ScannerStatus.INTERRUPTED:
        assert not state.can_scan and not state.can_eject and not state.can_load_next
