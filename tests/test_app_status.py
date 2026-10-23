import threading
import time

from opticfilm_linux.app.status import CachedStatusScanner, StatusPoller
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner


class _Blocking(FakeScanner):
    def __init__(self, gate: threading.Event, raises: bool) -> None:
        self._gate, self._raises = gate, raises

    def status(self):
        self._gate.wait(30)
        if self._raises:
            raise OSError("late failure")
        return ScannerStatus.READY


def _until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_poller_caches_the_status_and_reports_error_once():
    problems: list[tuple[str, str]] = []
    gate = threading.Event()
    gate.set()
    poller = StatusPoller(
        _Blocking(gate, True), time.monotonic, 5.0, lambda m, d: problems.append((m, d))
    )
    poller.poll()
    _until(lambda: poller.current() is ScannerStatus.ERROR)
    poller.poll()
    _until(lambda: len(problems) >= 1)
    time.sleep(0.1)
    assert len(problems) == 1 and problems[0][0] == "Scanner error"


def test_a_closed_poller_never_reports_a_late_error():
    problems: list[tuple[str, str]] = []
    gate = threading.Event()
    poller = StatusPoller(
        _Blocking(gate, True), time.monotonic, 5.0, lambda m, d: problems.append((m, d))
    )
    poller.poll()
    time.sleep(0.1)
    poller.close()  # the batch was closed while the request was out
    gate.set()
    time.sleep(0.3)
    assert problems == []


def test_cached_scanner_status_never_calls_the_backend():
    gate = threading.Event()
    poller = StatusPoller(_Blocking(gate, False), time.monotonic, 5.0, lambda *_: None)
    cached = CachedStatusScanner(_Blocking(gate, False), poller)
    assert cached.status() is ScannerStatus.UNKNOWN  # would block for 30 s if it asked the backend


def test_close_waits_for_a_report_in_progress_and_blocks_later_ones():
    started, finished = threading.Event(), threading.Event()
    calls: list[str] = []

    def slow_callback(message, _detail):
        calls.append(message)
        started.set()
        time.sleep(0.3)
        finished.set()

    gate = threading.Event()
    gate.set()
    poller = StatusPoller(_Blocking(gate, True), time.monotonic, 5.0, slow_callback)
    poller.poll()
    assert started.wait(5)
    poller.close()  # must not return while the callback is still running
    assert finished.is_set()
    poller._report("Scanner error", "again")  # after close: never reaches the callback
    time.sleep(0.1)
    assert calls == ["Scanner error"]
