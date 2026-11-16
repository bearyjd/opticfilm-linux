"""Close anyway: the owner's way out of a hung scan, without losing saved work."""

import threading

from controller_helpers import open_new, scan_when_ready, wait_all_in_review, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.batch.model import SlideStatus
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.holder_latch import HolderLatch


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


def test_close_anyway_keeps_the_accepted_slides_on_disk(sweep600_dir, tmp_path):
    gate, stops = threading.Event(), []
    gate.set()  # the first scan runs through
    controller = Controller(lambda _dpi: StoppableScanner(sweep600_dir, gate, stops))
    open_new(controller, tmp_path / "b")
    scan_when_ready(controller)
    wait_all_in_review(controller, 1)
    controller.accept_all()
    # The scan already auto-accepted clean slides, so "accepted" says nothing about the command:
    # wait for the owner's decision itself to be recorded on every deliverable slide.
    store = controller._session.store
    wait_until(
        controller,
        lambda: all(
            s.owner_decided or s.status in (SlideStatus.BLANK, SlideStatus.SKIPPED)
            for t in store.snapshot().trays
            for s in t.slides
        ),
    )
    before = BatchStore.open(tmp_path / "b", readonly=True)
    expected = before.snapshot().trays[0]
    before.close()
    gate.clear()  # the next scan hangs
    wait_until(controller, lambda: controller.state().can_scan)
    controller.scan()
    wait_until(controller, lambda: controller.shutdown_decision() == "scan_running")
    controller.close_anyway()
    controller.shutdown()
    after = BatchStore.open(tmp_path / "b", readonly=True)
    manifest = after.snapshot()
    after.close()
    assert manifest.name == "Test"
    assert manifest.trays[0] == expected
    assert any(s.status is SlideStatus.ACCEPTED for s in manifest.trays[0].slides)


def test_close_anyway_does_nothing_once_the_scan_has_ended(sweep600_dir, tmp_path):
    gate, stops = threading.Event(), []
    gate.set()
    controller = Controller(lambda _dpi: StoppableScanner(sweep600_dir, gate, stops))
    try:
        open_new(controller, tmp_path / "b")
        scan_when_ready(controller)
        wait_all_in_review(controller, 1)
        controller._remember_holder_latch(HolderLatch.LATCHED)
        controller.close_anyway()
        assert stops == []
        assert controller._holder_latch is HolderLatch.LATCHED
    finally:
        controller.shutdown()


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
