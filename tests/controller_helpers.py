"""Shared helpers for the controller tests: build a controller on the fake scanner and wait."""

import time
from collections.abc import Callable, Sequence
from pathlib import Path

from opticfilm_linux.app.controller import Controller
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner

TIMEOUT = 60.0


def make_controller(
    sweep_dir: Path,
    statuses: Sequence[ScannerStatus] = (ScannerStatus.READY,),
    before_scan: Callable[[], None] | None = None,
) -> Controller:
    return Controller(lambda _dpi: FakeScanner(sweep_dir, statuses, before_scan))


def wait_until(controller: Controller, condition: Callable[[], bool], timeout=TIMEOUT) -> None:
    """Tick like the UI timer does until ``condition()`` holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        controller.tick()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out; last state: {controller.state()}")


def open_new(controller: Controller, directory: Path, dpi: int = 600) -> None:
    controller.new_batch(directory, "Test", "", None, dpi)
    wait_until(controller, lambda: controller.state().batch_name == "Test")


def scan_when_ready(controller: Controller) -> None:
    wait_until(controller, lambda: controller.state().can_scan)
    controller.scan()
    wait_until(controller, lambda: not controller.state().can_scan)


def wait_all_in_review(controller: Controller, count: int) -> None:
    def done() -> bool:
        trays = controller.state().trays
        return len(trays) == count and all(t.stage.value == "review" for t in trays)

    wait_until(controller, done)
