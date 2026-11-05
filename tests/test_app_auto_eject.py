"""Automatic eject after a good tray (OPTICFILM_AUTO_EJECT, plan PR 4, owner choice B2).

The app runs its own eject, through the usual holder path, only once the scan job has fully
finished: the sweep is saved, the tray reached SCANNED and the scan has left the runner. Every
other ending of a scan means no eject at all. These tests use the in-process fake scanner; the
fake-driver tests are in ``test_app_auto_eject_driver.py``.
"""

import threading
import time

import pytest

from controller_helpers import open_new, wait_all_in_review, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.holder import WAIT_HOLDER, WAIT_SCAN
from opticfilm_linux.app.messages import EJECT_DID_NOT_RUN, holder_failed_text
from opticfilm_linux.batch import runner as runner_module
from opticfilm_linux.batch.events import ScanFinished, TrayStageChanged
from opticfilm_linux.batch.model import TrayStage
from opticfilm_linux.scanner import interrupted
from opticfilm_linux.scanner.base import ScanError
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.holder_latch import HolderLatch
from opticfilm_linux.scanner.interrupted import ScanInterrupted
from opticfilm_linux.scanner.select import AUTO_EJECT_ENV, auto_eject_enabled

SKIPPED = "Automatic eject skipped"


class EjectRecorder(FakeScanner):
    """The fake scanner, counting ejects; ``on_eject`` sees the app at the moment of the eject."""

    def __init__(self, sweep_dir, before_scan=None, on_eject=None, outcome="ejected"):
        super().__init__(sweep_dir, before_scan=before_scan)
        self.ejects = 0
        self.eject_outcome = outcome
        self._on_eject = on_eject

    def eject(self) -> None:
        self.ejects += 1
        if self._on_eject is not None:
            self._on_eject()


@pytest.fixture
def build(sweep600_dir, tmp_path):
    made: list[Controller] = []

    def make(auto_eject=True, **kwargs):
        scanner = EjectRecorder(sweep600_dir, **kwargs)
        c = Controller(lambda _dpi: scanner, auto_eject=auto_eject)
        made.append(c)
        open_new(c, tmp_path / "b")
        return c, scanner

    yield make
    for c in made:
        c.shutdown()


def _settle(c, seconds=0.3) -> None:
    """Tick like the UI timer for a while, so a wrongly chained eject would have run."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        c.tick()
        time.sleep(0.01)
    wait_until(c, lambda: not c._holder_busy)


def _scan_held(c) -> None:
    """Scan and wait, WITHOUT ticking, until the scan has fully finished: its finished event
    then waits in the queue while the test sets the scene."""
    wait_until(c, lambda: c.state().can_scan)
    c.scan()
    deadline = time.monotonic() + 60
    while c._scans_queued and time.monotonic() < deadline:
        time.sleep(0.01)
    assert c._session.runner.wait_scans_idle(60)


def _banner(c) -> str:
    banner = c.state().banner
    return banner.message if banner is not None else ""


@pytest.mark.parametrize(
    ("value", "on"),
    [("1", True), ("true", True), ("YES", True), (" on ", True), ("", False), ("0", False)]
    + [("off", False), ("2", False), ("enable", False)],
)
def test_the_switch_is_off_unless_explicitly_on(monkeypatch, value, on):
    monkeypatch.setenv(AUTO_EJECT_ENV, value)
    assert auto_eject_enabled() is on


def test_the_controller_reads_the_switch_once(monkeypatch, sweep600_dir):
    monkeypatch.setenv(AUTO_EJECT_ENV, "1")
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir))
    monkeypatch.setenv(AUTO_EJECT_ENV, "0")
    try:
        assert c.auto_eject_enabled
    finally:
        c.shutdown()
    monkeypatch.delenv(AUTO_EJECT_ENV)
    c = Controller(lambda _dpi: FakeScanner(sweep600_dir))
    try:
        assert not c.auto_eject_enabled
    finally:
        c.shutdown()


def test_switched_off_a_good_tray_never_ejects(build):
    c, scanner = build(auto_eject=False)
    _scan_held(c)
    wait_all_in_review(c, 1)
    _settle(c)
    assert scanner.ejects == 0 and c.state().banner is None


def test_one_eject_after_the_sweep_is_saved_and_the_scan_has_left_the_runner(build):
    seen = []

    def at_eject():
        session = c._session
        tray = session.store.snapshot().trays[0]
        sweep = session.store.sweep_dir(tray.number, tray.sweep_version)
        seen.append((session.runner.scan_pending(), sweep.is_dir(), tray.stage))

    c, scanner = build(on_eject=at_eject)
    _scan_held(c)
    wait_until(c, lambda: scanner.ejects == 1)
    wait_all_in_review(c, 1)
    _settle(c)
    assert scanner.ejects == 1
    pending, saved, stage = seen[0]
    assert not pending and saved and stage is not TrayStage.SCANNING
    assert c._holder_latch is HolderLatch.RELEASED and c.state().banner is None


def test_an_eject_chained_from_the_scanned_event_would_be_refused(build):
    """Regression for the timing trap: SCANNED is emitted inside the scan job, while the scan
    still holds the runner, so a holder move started there is refused with WAIT_SCAN."""
    c, scanner = build()
    runner = c._session.runner
    original, refusals = runner._emit, []

    def emit(event):
        if isinstance(event, TrayStageChanged) and event.stage is TrayStage.SCANNED:
            with c._lock:
                refusals.append(c._holder_refusal())
        original(event)

    runner._emit = emit
    _scan_held(c)
    wait_until(c, lambda: scanner.ejects == 1)
    assert refusals == [WAIT_SCAN]


def test_the_eject_runs_while_the_tray_is_still_being_enhanced(build, monkeypatch):
    release, enhancing = threading.Event(), threading.Event()
    real = runner_module.enhance_tray

    def held(*args, **kwargs):
        enhancing.set()
        release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(runner_module, "enhance_tray", held)
    c, scanner = build()
    try:
        _scan_held(c)
        assert enhancing.wait(30)
        wait_until(c, lambda: scanner.ejects == 1 and not c._holder_busy)
        stage = c._session.store.snapshot().trays[0].stage
        assert stage in (TrayStage.SCANNED, TrayStage.ENHANCING)  # enhancement is held
    finally:
        release.set()
    wait_all_in_review(c, 1)


def _raise(error: BaseException):
    def before_scan() -> None:
        raise error

    return before_scan


@pytest.mark.parametrize(
    "error",
    [
        ScanError("driver error: phase cal_dark_b"),
        ScanInterrupted("the driver was stopped mid-scan"),
        ScanError("tray-f3 is missing"),
        KeyboardInterrupt(),  # Ctrl-C: a BaseException
    ],
    ids=["driver-error", "interrupted", "missing-frames", "ctrl-c"],
)
def test_a_failed_scan_never_ejects(build, error):
    c, scanner = build(before_scan=_raise(error))
    _scan_held(c)
    wait_until(c, lambda: c.state().trays and c.state().trays[0].stage is TrayStage.FAILED)
    _settle(c)
    assert scanner.ejects == 0 and SKIPPED not in _banner(c)


def test_a_tray_whose_check_fails_never_ejects(build, monkeypatch):
    def wrong(_sweep, _settings):
        raise ScanError("the scanner returned openings (1, 2) but (1, 2, 3, 4) were asked for")

    monkeypatch.setattr(runner_module, "check_scanned_positions", wrong)
    c, scanner = build()
    _scan_held(c)
    wait_until(c, lambda: c.state().trays and c.state().trays[0].stage is TrayStage.FAILED)
    _settle(c)
    assert scanner.ejects == 0


def _held_scan(build):
    """A controller whose scan waits at the scanner until ``release`` is set."""
    started, release = threading.Event(), threading.Event()

    def before_scan() -> None:
        started.set()
        release.wait(30)

    c, scanner = build(before_scan=before_scan)
    wait_until(c, lambda: c.state().can_scan)
    c.scan()
    assert started.wait(30)
    return c, scanner, release


def test_a_runner_stopped_during_the_scan_never_ejects(build):
    c, scanner, release = _held_scan(build)
    runner = c._session.runner
    stopper = threading.Thread(target=runner.stop)
    stopper.start()
    wait_until(c, lambda: runner._stopping)
    release.set()
    stopper.join(30)
    _settle(c)
    assert scanner.ejects == 0


def test_closing_the_batch_during_the_scan_never_ejects(build):
    c, scanner, release = _held_scan(build)
    c.close_batch()
    time.sleep(0.1)
    release.set()
    wait_until(c, lambda: c.state().batch_name is None)
    _settle(c)
    assert scanner.ejects == 0 and SKIPPED not in _banner(c)


def test_a_batch_closed_after_the_scan_but_before_the_eject_never_ejects(build):
    c, scanner = build()
    _scan_held(c)
    c.close_batch()
    wait_until(c, lambda: c.state().batch_name is None)
    _settle(c)
    assert scanner.ejects == 0 and SKIPPED not in _banner(c)


def test_a_finished_event_that_is_not_ok_never_ejects(build):
    c, scanner = build()
    c._session.runner.events.put(ScanFinished(1, ok=False))
    _settle(c)
    assert scanner.ejects == 0 and c.state().banner is None


@pytest.mark.parametrize("reason", ["holder-busy", "scan-queued", "interrupted-marker"])
def test_an_eject_that_cannot_start_is_skipped_with_a_banner(build, reason):
    c, scanner = build()
    _scan_held(c)
    if reason == "holder-busy":
        c._set(_holder_busy=True)
    elif reason == "scan-queued":
        c._set(_scans_queued=1)
    else:
        interrupted.begin("scan")
    try:
        wait_until(c, lambda: _banner(c) != "")
        assert scanner.ejects == 0 and _banner(c).startswith(SKIPPED)
        assert _banner(c).endswith("Press Eject when ready.")
        if reason != "interrupted-marker":
            assert (WAIT_HOLDER if reason == "holder-busy" else WAIT_SCAN) in _banner(c)
    finally:
        c._set(_holder_busy=False, _scans_queued=0)
        interrupted.clear()
    _settle(c)
    assert scanner.ejects == 0  # skipped means skipped: no later retry


def test_a_refused_scan_never_ejects(build):
    c, scanner = build()
    wait_until(c, lambda: c.state().can_scan)
    c._set(_holder_busy=True)  # the scan is refused: a holder move is under way
    c.scan()
    c._set(_holder_busy=False)
    wait_until(c, lambda: c.state().banner is not None)
    _settle(c)
    assert scanner.ejects == 0 and c.state().trays == ()


def test_a_failed_eject_keeps_the_tray_and_asks_for_a_power_cycle(build):
    def fail() -> None:
        raise ScanError("eject failed. Driver said: EjectIncompleteError")

    c, scanner = build(on_eject=fail)
    _scan_held(c)
    wait_until(c, lambda: scanner.ejects == 1 and not c._holder_busy)
    wait_all_in_review(c, 1)
    assert _banner(c) == holder_failed_text("eject")
    assert c._holder_latch is HolderLatch.UNKNOWN


def test_an_eject_the_scanner_did_not_confirm_says_to_check_the_slot(build):
    c, scanner = build(outcome=None)  # e.g. the loader sensor said no magazine: nothing sent
    _scan_held(c)
    wait_until(c, lambda: scanner.ejects == 1 and not c._holder_busy)
    wait_all_in_review(c, 1)
    assert _banner(c) == EJECT_DID_NOT_RUN
    assert c._holder_latch is HolderLatch.UNKNOWN


def test_after_the_eject_the_next_scan_waits_for_the_next_holder(build):
    """Hands-free trays: nothing loads by itself; the released latch refuses the next scan
    until the owner loads (Load next holder) or confirms the holder is latched."""
    c, scanner = build()
    _scan_held(c)
    wait_until(c, lambda: scanner.ejects == 1 and not c._holder_busy)
    c.scan()
    wait_until(c, lambda: c.state().banner is not None)
    assert "latch" in _banner(c) and len(c._session.store.snapshot().trays) == 1
