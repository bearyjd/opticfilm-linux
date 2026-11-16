"""Close anyway: the owner's way out of a hung scan, without losing saved work."""

import threading

from controller_helpers import open_new, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.scanner.holder_latch import HolderLatch
from opticfilm_linux.scanner.fake import FakeScanner


class StoppableScanner(FakeScanner):
    """A scanner whose scan hangs until the driver's process group is stopped."""

    def __init__(self, sweep_dir, gate: threading.Event, stops: list[str]):
        super().__init__(sweep_dir, before_scan=lambda: gate.wait(30))
        self._gate = gate
        self._stops = stops

    def abort_running(self) -> None:
        self._stops.append("stopped")
        self._gate.set()


def _hung_scan(sweep_dir, tmp_path, scanner_type=StoppableScanner):
    gate, stops = threading.Event(), []
    controller = Controller(lambda _dpi: scanner_type(sweep_dir, gate, stops))
    open_new(controller, tmp_path / "b")
    wait_until(controller, lambda: controller.state().can_scan)
    controller.scan()
    wait_until(controller, lambda: controller.shutdown_decision() == "scan_running")
    return controller, gate, stops


def test_close_anyway_stops_the_driver_and_marks_the_holder_unknown(sweep600_dir, tmp_path):
    controller, _gate, stops = _hung_scan(sweep600_dir, tmp_path)
    try:
        controller._remember_holder_latch(HolderLatch.LATCHED)
        controller.close_anyway()
        assert stops == ["stopped"]
        assert controller._holder_latch is HolderLatch.UNKNOWN
    finally:
        controller.shutdown()


def test_close_anyway_keeps_the_batch_on_disk(sweep600_dir, tmp_path):
    controller, _gate, _stops = _hung_scan(sweep600_dir, tmp_path)
    controller.close_anyway()
    controller.shutdown()
    assert (tmp_path / "b").is_dir() and any((tmp_path / "b").iterdir())


def test_close_anyway_survives_a_scanner_that_cannot_be_stopped(sweep600_dir, tmp_path, caplog):
    class Stubborn(StoppableScanner):
        def abort_running(self) -> None:
            self._gate.set()  # lets the test finish
            raise OSError("no such process group")

    controller, _gate, _stops = _hung_scan(sweep600_dir, tmp_path, Stubborn)
    try:
        controller.close_anyway()  # never raises
        assert controller._holder_latch is HolderLatch.UNKNOWN
        assert "could not stop" in caplog.text
    finally:
        controller.shutdown()
