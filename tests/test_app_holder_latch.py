"""The holder label remembers commands, never infers a latch from scanner registers."""

import threading
import time
from dataclasses import replace

import pytest

from controller_helpers import open_new, wait_until
from opticfilm_linux.app.controller import Controller
from opticfilm_linux.batch.model import BatchDefaults, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.base import ScanError, ScannerStatus, StatusReading
from opticfilm_linux.scanner.fake import FakeScanner

UNKNOWN = "Holder: unknown after power-on or a fault; check by hand before scanning"
LATCHED = "Holder: latched"
RELEASED = "Holder: released"


class LatchScanner(FakeScanner):
    def __init__(self, sweep_dir, address=(1, 2)):
        super().__init__(sweep_dir)
        self.usb_address = address
        self.current_status = ScannerStatus.NOT_READY
        self.outcome = "ejected"
        self.fail = None
        self.calls = []
        self.pressed = False

    def status(self):
        return self.current_status

    def status_reading(self):
        return StatusReading(self.current_status, self.pressed)

    def _move(self, action):
        self.calls.append(action)
        if self.fail:
            raise self.fail

    def load_next_strip(self):
        self._move("load")

    def eject(self):
        self._move("eject")
        self.eject_outcome = self.outcome
        return None


@pytest.fixture
def build(tmp_path, monkeypatch, sweep600_dir):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    controllers = []

    def make(address=(1, 2)):
        scanner = LatchScanner(sweep600_dir, address)
        controller = Controller(lambda _dpi: scanner, next_strip=True)
        controllers.append(controller)
        return controller, scanner

    yield make
    for controller in controllers:
        controller.shutdown()


def label(controller, expected):
    wait_until(controller, lambda: controller.state().holder_latch_text == expected, timeout=5)


def load(controller):
    controller.load_next_holder()
    label(controller, LATCHED)
    wait_until(controller, lambda: not controller.state().holder_busy, timeout=5)


def test_no_record_and_ready_status_do_not_claim_a_latch(build, tmp_path):
    controller, scanner = build()
    assert controller.state().holder_latch_text == UNKNOWN
    scanner.current_status = ScannerStatus.READY
    open_new(controller, tmp_path / "batch")
    wait_until(controller, lambda: controller.state().can_scan)
    assert controller.state().holder_latch_text == UNKNOWN
    assert scanner.calls == []


@pytest.mark.parametrize("outcome", ["ejected", "already_ejected", "ready", None])
def test_load_then_eject_uses_only_confirmed_ejected_outcome(build, tmp_path, outcome):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)
    scanner.outcome = outcome
    controller.eject_holder()
    label(controller, RELEASED if outcome == "ejected" else UNKNOWN)
    assert scanner.calls == ["load", "eject"]


def test_new_session_does_not_restore_stored_latch_for_reused_usb_address(build, tmp_path):
    first, _ = build()
    open_new(first, tmp_path / "batch")
    load(first)
    first.close_batch()
    wait_until(first, lambda: first.state().batch_name is None)
    same, _ = build()
    same.open_batch(tmp_path / "batch")
    wait_until(same, lambda: same._latch_address == (1, 2))
    label(same, UNKNOWN)
    other, _ = build((1, 3))
    open_new(other, tmp_path / "other")
    label(other, UNKNOWN)


def test_first_polled_identity_and_later_address_change_are_unknown(build, tmp_path):
    first, _ = build()
    open_new(first, tmp_path / "first")
    load(first)
    controller, scanner = build(None)
    open_new(controller, tmp_path / "second")
    label(controller, UNKNOWN)
    scanner.usb_address = (1, 2)
    wait_until(controller, lambda: controller._latch_address == (1, 2))
    label(controller, UNKNOWN)
    scanner.usb_address = (1, 3)
    label(controller, UNKNOWN)


@pytest.mark.parametrize("action", ["load_next_holder", "eject_holder"])
def test_failed_move_invalidates_the_remembered_latch(build, tmp_path, action):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)
    scanner.fail = ScanError("move failed")
    getattr(controller, action)()
    label(controller, UNKNOWN)
    wait_until(controller, lambda: controller.state().banner is not None, timeout=5)
    assert "move failed" in controller.state().banner.detail


@pytest.mark.parametrize(
    "status", [ScannerStatus.INTERRUPTED, ScannerStatus.ERROR, ScannerStatus.NOT_CONNECTED]
)
def test_cut_off_or_failed_scanner_session_invalidates_persistence(build, tmp_path, status):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)
    scanner.current_status = status
    label(controller, UNKNOWN)
    scanner.current_status = ScannerStatus.NOT_READY
    controller.close_batch()
    wait_until(controller, lambda: controller.state().batch_name is None)
    second, _ = build()
    second.open_batch(tmp_path / "batch")
    label(second, UNKNOWN)


def test_recovering_an_interrupted_batch_invalidates_a_saved_latch(build, tmp_path):
    first, _ = build()
    open_new(first, tmp_path / "first")
    load(first)
    directory = tmp_path / "interrupted"
    store = BatchStore.create(directory, "Interrupted", "", None, BatchDefaults(dpi=600))
    store.update(lambda manifest: replace(manifest, trays=(TrayRecord(1, TrayStage.SCANNING),)))
    store.close()
    second, _ = build()
    second.open_batch(directory)
    wait_until(second, lambda: second.state().batch_name == "Interrupted")
    label(second, UNKNOWN)


def test_failed_scan_invalidates_the_latch(build, tmp_path, monkeypatch):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)
    scanner.current_status = ScannerStatus.READY

    def fail(_settings):
        raise ScanError("scan interrupted")

    monkeypatch.setattr(scanner, "scan_holder", fail)
    wait_until(controller, lambda: controller.state().can_scan)
    controller.scan()
    wait_until(controller, lambda: controller.state().banner is not None)
    label(controller, UNKNOWN)


def test_front_panel_eject_updates_the_same_latch_and_keeps_controls_informational(build, tmp_path):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)
    wait_until(controller, lambda: controller.state().can_eject)
    scanner.pressed = True
    label(controller, RELEASED)
    wait_until(controller, lambda: controller.state().can_eject)
    assert controller.state().can_load_next
    assert scanner.calls == ["load", "eject"]
    scanner.current_status = ScannerStatus.READY
    wait_until(controller, lambda: controller.state().scanner is ScannerStatus.READY)
    assert not controller.state().can_scan
    assert controller.state().holder_latch_text == RELEASED


def test_failed_session_open_invalidates_the_device_record(build, tmp_path, monkeypatch):
    first, _ = build()
    open_new(first, tmp_path / "batch")
    load(first)
    first.close_batch()
    wait_until(first, lambda: first.state().batch_name is None)
    from opticfilm_linux.app.controller import BatchRunner

    original = BatchRunner.resume

    def fail_resume(_runner):
        raise RuntimeError("session failed")

    monkeypatch.setattr(BatchRunner, "resume", fail_resume)
    second, _ = build()
    second.open_batch(tmp_path / "batch")
    wait_until(second, lambda: second.state().banner is not None)
    assert second.state().holder_latch_text == UNKNOWN
    monkeypatch.setattr(BatchRunner, "resume", original)
    third, _ = build()
    third.open_batch(tmp_path / "batch")
    wait_until(third, lambda: third.state().batch_name is not None)
    label(third, UNKNOWN)


def test_factory_failure_invalidates_the_previous_device_record(build, tmp_path, monkeypatch):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)

    def fail_factory(_dpi):
        raise RuntimeError("scanner session failed")

    monkeypatch.setattr(controller, "_factory", fail_factory)
    controller.open_batch(tmp_path / "batch")
    wait_until(controller, lambda: controller.state().banner is not None)
    assert controller._session is None
    monkeypatch.setattr(controller, "_factory", lambda _dpi: scanner)
    controller.open_batch(tmp_path / "batch")
    wait_until(controller, lambda: controller.state().batch_name is not None)
    label(controller, UNKNOWN)


def test_fault_before_identity_is_known_cannot_restore_a_stale_latch(build, tmp_path):
    first, _ = build()
    open_new(first, tmp_path / "first")
    load(first)
    controller, scanner = build(None)
    scanner.current_status = ScannerStatus.ERROR
    open_new(controller, tmp_path / "second")
    wait_until(controller, lambda: controller.state().scanner is ScannerStatus.ERROR)
    scanner.usb_address = (1, 2)
    scanner.current_status = ScannerStatus.READY
    wait_until(controller, lambda: controller.state().can_scan)
    assert controller.state().holder_latch_text == UNKNOWN


@pytest.mark.parametrize(
    ("action", "expected"), [("load_next_holder", LATCHED), ("eject_holder", RELEASED)]
)
def test_move_before_identity_preserves_fresh_result_over_saved_record(
    build, tmp_path, action, expected
):
    first, _ = build()
    open_new(first, tmp_path / "first")
    load(first)
    if expected == LATCHED:
        first.eject_holder()
        label(first, RELEASED)
    controller, scanner = build(None)
    open_new(controller, tmp_path / "second")
    getattr(controller, action)()
    label(controller, expected)
    scanner.usb_address = (1, 2)
    wait_until(controller, lambda: controller._latch_address == (1, 2), timeout=5)
    assert controller.state().holder_latch_text == expected
    third, _ = build()
    open_new(third, tmp_path / "third")
    wait_until(third, lambda: third._latch_address == (1, 2))
    label(third, UNKNOWN)


def test_failed_scan_then_close_without_tick_invalidates_the_device_record(
    build, tmp_path, monkeypatch
):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)
    scanner.current_status = ScannerStatus.READY
    wait_until(controller, lambda: controller.state().can_scan)

    def fail(_settings):
        raise ScanError("scan interrupted")

    monkeypatch.setattr(scanner, "scan_holder", fail)
    controller.scan()
    deadline = time.monotonic() + 5
    while not controller._session.store.snapshot().trays and time.monotonic() < deadline:
        time.sleep(0.01)
    while controller._session.runner.scan_pending() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert controller._session.store.snapshot().trays[0].stage is TrayStage.FAILED
    controller.close_batch()
    controller._command.submit(lambda: None).result(timeout=5)
    assert controller._session is None
    second, _ = build()
    second.open_batch(tmp_path / "batch")
    wait_until(second, lambda: second.state().batch_name is not None)
    label(second, UNKNOWN)


def test_persistence_never_runs_on_ui_thread_or_under_controller_lock(build, tmp_path, monkeypatch):
    from opticfilm_linux.scanner import holder_latch

    controller, _ = build()
    observed = []

    def record(*_args):
        acquired = controller._lock.acquire(timeout=0.2)
        if acquired:
            controller._lock.release()
        observed.append((threading.current_thread() is threading.main_thread(), acquired))
        return holder_latch.HolderLatch.UNKNOWN

    monkeypatch.setattr(holder_latch, "load_latch", record)
    monkeypatch.setattr(holder_latch, "save_latch", record)
    open_new(controller, tmp_path / "batch")
    load(controller)
    controller._remember_holder_latch(holder_latch.HolderLatch.UNKNOWN)
    controller.shutdown()
    assert observed
    assert all(not ui and unlocked for ui, unlocked in observed), observed


def test_queued_old_scan_failure_cannot_overwrite_newer_successful_load(
    build, tmp_path, monkeypatch
):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    scanner.current_status = ScannerStatus.READY
    wait_until(controller, lambda: controller.state().can_scan)

    def fail(_settings):
        raise ScanError("queued old failure")

    monkeypatch.setattr(scanner, "scan_holder", fail)
    controller.scan()
    controller._command.submit(lambda: None).result(timeout=5)
    assert controller._session.runner.wait_idle(timeout=5)
    assert not controller._session.runner.events.empty()
    controller.load_next_holder()
    controller._holder_pool.submit(lambda: None).result(timeout=5)
    assert controller._holder_latch.value == "latched"
    controller._drain_events()
    controller._rebuild()
    assert controller.state().holder_latch_text == LATCHED


def test_eject_reads_recorded_confirmation_without_changing_none_return(
    build, tmp_path, monkeypatch
):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)
    scanner.eject_outcome = "ejected"
    monkeypatch.setattr(scanner, "eject", lambda: None)
    controller.eject_holder()
    label(controller, RELEASED)


@pytest.mark.parametrize("outcome", ["already_ejected", "ready", None, "skipped"])
def test_skipped_or_unconfirmed_eject_never_releases_even_if_call_returns_success(
    build, tmp_path, monkeypatch, outcome
):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)
    scanner.eject_outcome = outcome
    monkeypatch.setattr(scanner, "eject", lambda: "ejected")
    controller.eject_holder()
    controller._holder_pool.submit(lambda: None).result(timeout=5)
    controller._rebuild()
    assert controller.state().holder_latch_text == UNKNOWN


def test_slow_persistence_does_not_delay_movement_status_poll_or_ui_access(
    build, tmp_path, monkeypatch
):
    from opticfilm_linux.scanner import holder_latch

    controller, _ = build()
    open_new(controller, tmp_path / "batch")
    controller._latch_pool.submit(lambda: None).result(timeout=5)
    entered, release, polled = threading.Event(), threading.Event(), threading.Event()

    def save(*_args):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(holder_latch, "save_latch", save)
    monkeypatch.setattr(controller._session.poller, "poll", polled.set)
    try:
        controller.load_next_holder()
        assert entered.wait(2)
        assert polled.wait(0.5)
        assert controller._lock.acquire(timeout=0.5)
        controller._lock.release()
        controller._holder_pool.submit(lambda: None).result(timeout=0.5)
    finally:
        release.set()


def test_new_session_never_reads_saved_latch(build, tmp_path, monkeypatch):
    from opticfilm_linux.scanner import holder_latch

    def unexpected(*_args):
        pytest.fail("new session must not restore stored latch knowledge")

    monkeypatch.setattr(holder_latch, "load_latch", unexpected)
    controller, _ = build()
    open_new(controller, tmp_path / "batch")
    controller._latch_pool.submit(lambda: None).result(timeout=5)
    label(controller, UNKNOWN)


def test_worker_failure_is_logged_without_losing_in_memory_state(
    build, tmp_path, monkeypatch, caplog
):
    from opticfilm_linux.scanner import holder_latch

    controller, _ = build()
    open_new(controller, tmp_path / "batch")

    def fail(*_args):
        raise OSError("broken storage")

    monkeypatch.setattr(holder_latch, "save_latch", fail)
    load(controller)
    controller._latch_pool.submit(lambda: None).result(timeout=5)
    assert controller._holder_latch is holder_latch.HolderLatch.LATCHED
    assert "could not persist holder latch information" in caplog.text


def test_identity_change_during_old_transition_does_not_roll_back_generation(build, tmp_path):
    from opticfilm_linux.scanner.holder_latch import HolderLatch

    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)
    with controller._lock:
        sequence = controller._next_latch_sequence()
    scanner.usb_address = (1, 3)
    controller._remember_holder_latch(HolderLatch.LATCHED, sequence=sequence)
    assert controller._holder_latch is HolderLatch.UNKNOWN
    assert controller._latch_applied > sequence


def test_old_status_failure_cannot_overwrite_a_newer_successful_load(build, tmp_path, monkeypatch):
    from opticfilm_linux.scanner.holder_latch import HolderLatch

    controller, _ = build()
    open_new(controller, tmp_path / "batch")
    controller._refresher.submit(lambda: None).result(timeout=5)
    sequence = controller._begin_latch_operation()

    def old_status():
        controller._remember_holder_latch(HolderLatch.LATCHED)
        return ScannerStatus.ERROR, sequence

    monkeypatch.setattr(controller._session.poller, "snapshot", old_status)
    controller._rebuild()
    assert controller._holder_latch is HolderLatch.LATCHED


def test_persistence_submission_failure_is_logged_and_keeps_memory(
    build, tmp_path, monkeypatch, caplog
):
    from opticfilm_linux.scanner.holder_latch import HolderLatch

    controller, _ = build()
    open_new(controller, tmp_path / "batch")

    def rejected(*_args):
        raise RuntimeError("worker unavailable")

    monkeypatch.setattr(controller._latch_pool, "submit", rejected)
    controller._remember_holder_latch(HolderLatch.LATCHED)
    assert controller._holder_latch is HolderLatch.LATCHED
    assert "could not schedule holder latch persistence" in caplog.text


def test_close_finishes_queued_persistence_off_lock_before_restart(build, tmp_path, monkeypatch):
    from opticfilm_linux.scanner import holder_latch

    controller, _ = build()
    open_new(controller, tmp_path / "batch")
    controller._latch_pool.submit(lambda: None).result(timeout=5)
    entered, release = threading.Event(), threading.Event()
    original = holder_latch.save_latch

    def save(*args):
        entered.set()
        assert release.wait(5)
        original(*args)

    monkeypatch.setattr(holder_latch, "save_latch", save)
    try:
        load(controller)
        assert entered.wait(2)
        controller.close_batch()
        closed = controller._command.submit(lambda: None)
        time.sleep(0.1)
        assert not closed.done(), "close completed before its queued state was saved"
        assert controller._lock.acquire(timeout=0.5)
        controller._lock.release()
    finally:
        release.set()
    closed.result(timeout=5)
    second, _ = build()
    second.open_batch(tmp_path / "batch")
    wait_until(second, lambda: second._latch_address == (1, 2))
    label(second, UNKNOWN)


def test_delayed_status_request_failure_cannot_overwrite_newer_load_on_later_refreshes(
    build, tmp_path, monkeypatch
):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    poller = controller._session.poller
    assert poller.wait_idle(5)
    entered, release = threading.Event(), threading.Event()

    def old_reading():
        entered.set()
        assert release.wait(5)
        raise ScanError("older poll failed")

    monkeypatch.setattr(scanner, "status_reading", old_reading)
    poller.poll()
    assert entered.wait(2)
    monkeypatch.setattr(poller, "poll", lambda: None)
    try:
        load(controller)
    finally:
        release.set()
    assert poller.wait_idle(5)
    controller._rebuild()
    controller._rebuild()
    assert controller.state().holder_latch_text == LATCHED


@pytest.mark.parametrize("action", ["scan", "rescan"])
def test_released_holder_refuses_scans_until_owner_confirms(build, tmp_path, action):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    load(controller)
    controller.eject_holder()
    label(controller, RELEASED)
    scanner.current_status = ScannerStatus.READY
    wait_until(controller, lambda: controller.state().scanner is ScannerStatus.READY)
    assert not controller.state().can_scan
    getattr(controller, action)(*([1] if action == "rescan" else []))
    wait_until(controller, lambda: controller.state().banner is not None)
    assert "seat" in controller.state().banner.message.lower()
    before = list(scanner.calls)
    controller.confirm_holder_latched()
    assert scanner.calls == before
    label(controller, LATCHED)
    assert controller.state().can_scan


def test_unknown_scan_warns_and_latched_scan_does_not(build, tmp_path, monkeypatch):
    controller, scanner = build()
    scanner.current_status = ScannerStatus.READY
    open_new(controller, tmp_path / "batch")
    wait_until(controller, lambda: controller.state().can_scan)
    calls = []
    monkeypatch.setattr(controller._session.runner, "scan_tray", lambda: calls.append("scan"))
    controller.scan()
    wait_until(controller, lambda: controller.state().holder_latch_warning and calls)
    wait_until(controller, lambda: not controller._scans_queued)
    scanner.current_status = ScannerStatus.NOT_READY
    wait_until(controller, lambda: controller.state().can_load_next)
    load(controller)
    controller._set(_banner=None)
    scanner.current_status = ScannerStatus.READY
    wait_until(controller, lambda: controller.state().can_scan)
    controller.scan()
    wait_until(controller, lambda: len(calls) == 2)
    controller._rebuild()
    assert not controller.state().holder_latch_warning


def test_confirmation_only_changes_released_state(build, tmp_path):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    controller.confirm_holder_latched()
    label(controller, UNKNOWN)
    assert scanner.calls == []
    load(controller)
    controller.confirm_holder_latched()
    label(controller, LATCHED)
    assert scanner.calls == ["load"]


def test_background_watcher_eject_while_closed_invalidates_on_reopen(build, tmp_path):
    controller, scanner = build()

    class Watcher:
        active = False

        def yield_to_app(self):
            self.active = False

        def restore(self):
            self.active = True
            return None

        def press_eject(self):
            assert self.active and controller._session is None
            scanner.eject()

    watcher = Watcher()
    controller._eject_watch = watcher
    open_new(controller, tmp_path / "batch")
    load(controller)
    controller.close_batch()
    wait_until(controller, lambda: controller.state().batch_name is None and watcher.active)
    watcher.press_eject()
    assert scanner.calls == ["load", "eject"]
    controller.open_batch(tmp_path / "batch")
    wait_until(controller, lambda: controller.state().batch_name is not None)
    assert not watcher.active
    label(controller, UNKNOWN)


def test_eject_that_discovers_identity_records_release(build, tmp_path, monkeypatch):
    controller, scanner = build(None)
    open_new(controller, tmp_path / "batch")

    def eject():
        scanner.usb_address = (1, 2)
        scanner.eject_outcome = "ejected"

    monkeypatch.setattr(scanner, "eject", eject)
    controller.eject_holder()
    label(controller, RELEASED)
    scanner.current_status = ScannerStatus.READY
    wait_until(controller, lambda: controller.state().scanner is ScannerStatus.READY)
    assert not controller.state().can_scan


def test_eject_that_loses_cached_identity_still_records_release(build, tmp_path, monkeypatch):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    wait_until(controller, lambda: controller._latch_address == (1, 2))

    def eject():
        scanner.usb_address = None
        scanner.eject_outcome = "ejected"

    monkeypatch.setattr(scanner, "eject", eject)
    controller.eject_holder()
    label(controller, RELEASED)
    scanner.current_status = ScannerStatus.READY
    wait_until(controller, lambda: controller.state().scanner is ScannerStatus.READY)
    assert not controller.state().can_scan


def test_eject_release_survives_identity_loss_seen_during_move(build, tmp_path, monkeypatch):
    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    wait_until(controller, lambda: controller._latch_address == (1, 2))

    def eject():
        scanner.usb_address = None
        controller._rebuild()
        scanner.eject_outcome = "ejected"

    monkeypatch.setattr(scanner, "eject", eject)
    controller.eject_holder()
    label(controller, RELEASED)


def test_identity_loss_cannot_let_old_eject_override_new_failure(build, tmp_path):
    from opticfilm_linux.scanner.holder_latch import HolderLatch

    controller, scanner = build()
    open_new(controller, tmp_path / "batch")
    wait_until(controller, lambda: controller._latch_address == (1, 2))
    sequence = controller._begin_latch_operation()
    scanner.usb_address = None
    controller._rebuild()
    controller._remember_holder_latch(HolderLatch.UNKNOWN)
    controller._remember_holder_latch(HolderLatch.RELEASED, sequence=sequence)
    assert controller._holder_latch is HolderLatch.UNKNOWN
