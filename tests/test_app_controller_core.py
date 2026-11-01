import threading
import time

import pytest

from controller_helpers import (
    make_controller,
    open_new,
    scan_when_ready,
    wait_all_in_review,
    wait_until,
)
from opticfilm_linux.batch.model import BatchDefaults, TrayStage
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner


@pytest.fixture
def controller(sweep600_dir):
    made = []

    def build(**kwargs):
        c = make_controller(sweep600_dir, **kwargs)
        made.append(c)
        return c

    yield build
    for c in made:
        c.shutdown()


def test_scan_five_trays_reach_review(controller, tmp_path):
    c = controller()
    open_new(c, tmp_path / "b")
    for _ in range(5):
        scan_when_ready(c)
    wait_all_in_review(c, 5)


def test_scan_when_not_ready_sets_banner_not_raises(controller, tmp_path):
    c = controller(statuses=(ScannerStatus.NOT_READY,))
    open_new(c, tmp_path / "b")
    c.scan()
    wait_until(c, lambda: c.state().banner is not None)
    assert "not ready" in c.state().banner.detail and c.state().trays == ()


def test_can_scan_false_while_scan_pending(controller, tmp_path):
    gate = threading.Event()
    c = controller(before_scan=lambda: gate.wait(30))
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    wait_until(c, lambda: c.state().scan_pending)
    assert not c.state().can_scan
    gate.set()
    wait_all_in_review(c, 1)
    wait_until(c, lambda: c.state().can_scan)


def test_tick_returns_fast_when_scanner_status_blocks(sweep600_dir, tmp_path):
    slow = threading.Event()

    class SlowStatus(FakeScanner):
        def status(self):
            if slow.is_set():
                time.sleep(1.0)
            return ScannerStatus.READY

    from opticfilm_linux.app.controller import Controller

    c = Controller(lambda _dpi: SlowStatus(sweep600_dir))
    try:
        open_new(c, tmp_path / "b")
        slow.set()
        c.tick()
        started = time.monotonic()
        c.tick()
        c.state()
        assert time.monotonic() - started < 0.05
    finally:
        c.shutdown()


def test_scanner_status_exception_shows_error(sweep600_dir, tmp_path):
    broken = threading.Event()

    class Flaky(FakeScanner):
        def status(self):
            if broken.is_set():
                raise OSError("usb gone")
            return ScannerStatus.READY

    from opticfilm_linux.app.controller import Controller

    c = Controller(lambda _dpi: Flaky(sweep600_dir))
    try:
        open_new(c, tmp_path / "b")
        broken.set()
        wait_until(c, lambda: c.state().scanner is ScannerStatus.ERROR)
        assert not c.state().can_scan and c.state().banner is not None
    finally:
        c.shutdown()


def test_scan_returns_fast_when_scan_blocks(controller, tmp_path):
    gate = threading.Event()
    c = controller(before_scan=lambda: gate.wait(30))
    open_new(c, tmp_path / "b")
    wait_until(c, lambda: c.state().can_scan)
    started = time.monotonic()
    c.scan()
    assert time.monotonic() - started < 0.05
    wait_until(c, lambda: c.shutdown_decision() == "scan_running")
    gate.set()
    wait_all_in_review(c, 1)
    assert c.shutdown_decision() == "ok"


def test_select_tray_fills_slides_with_plain_flag_sentences(controller, tmp_path):
    c = controller()
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    wait_all_in_review(c, 1)
    c.select_tray(1)
    wait_until(c, lambda: c.state().selected == 1 and len(c.state().slides) == 4)
    sentences = {f for card in c.state().slides for f in card.flags}
    assert all(isinstance(s, str) and s[0].isupper() for s in sentences)
    assert all(card.preview_token > 0 for card in c.state().slides)
    assert c.preview_path(1, 1, "after").is_file()


def test_open_batch_held_by_other_writer_reports_locked(controller, tmp_path):
    with BatchStore.create(tmp_path / "b", name="Held", defaults=BatchDefaults(dpi=600)):
        c = controller()
        c.open_batch(tmp_path / "b")
        wait_until(c, lambda: c.state().banner is not None)
        assert c.state().banner.detail.startswith("LOCKED:") and c.state().batch_name is None


def test_open_batch_readonly_disables_scan(controller, tmp_path):
    BatchStore.create(tmp_path / "b", name="Held", defaults=BatchDefaults(dpi=600)).close()
    c = controller()
    c.open_batch(tmp_path / "b", readonly=True)
    wait_until(c, lambda: c.state().batch_name == "Held")
    assert c.state().readonly and not c.state().can_scan
    c.scan()
    wait_until(c, lambda: c.state().banner is not None)
    assert "read-only" in c.state().banner.message and c.state().trays == ()


def test_open_missing_folder_gives_plain_banner(controller, tmp_path):
    c = controller()
    c.open_batch(tmp_path / "nowhere")
    wait_until(c, lambda: c.state().banner is not None)
    assert "Traceback" not in c.state().banner.message
    assert "nowhere" in c.state().banner.detail or "not found" in c.state().banner.message


def test_open_folder_with_garbage_manifest_gives_plain_banner(controller, tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "batch.json").write_text("{ not json")
    c = controller()
    c.open_batch(tmp_path / "b")
    wait_until(c, lambda: c.state().banner is not None)
    assert c.state().batch_name is None and "Traceback" not in c.state().banner.message


def test_select_unknown_tray_is_noop_on_empty_batch(controller, tmp_path):
    c = controller()
    open_new(c, tmp_path / "b")
    c.select_tray(9)
    c.tick()
    time.sleep(0.2)
    c.tick()
    assert c.state().selected is None and c.state().slides == ()


def test_rescan_resets_slides_and_keeps_old_sweep(controller, tmp_path):
    c = controller()
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    wait_all_in_review(c, 1)
    c.rescan(1)
    second = tmp_path / "b" / "trays" / "0001" / "sweep-2"
    wait_until(c, second.is_dir)
    wait_all_in_review(c, 1)
    assert (tmp_path / "b" / "trays" / "0001" / "sweep-1").is_dir()


def test_shutdown_resumes_next_time(controller, tmp_path, sweep600_dir):
    c = controller()
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    scan_when_ready(c)
    c._command.submit(lambda: None).result(10)  # hand both scans to the runner before closing
    assert c._session.runner.wait_scans_idle(10)  # shutdown may cancel queued raw scans
    c.shutdown()
    with BatchStore.open(tmp_path / "b") as store:
        stages = [t.stage for t in store.snapshot().trays]
    assert TrayStage.SCANNING not in stages
    c2 = controller()
    c2.open_batch(tmp_path / "b")
    wait_all_in_review(c2, 2)


def test_first_tray_is_selected_automatically_but_a_later_choice_is_kept(controller, tmp_path):
    c = controller()
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    wait_until(c, lambda: c.state().selected == 1)
    scan_when_ready(c)
    wait_all_in_review(c, 2)
    assert c.state().selected == 1
    c.select_tray(2)
    wait_until(c, lambda: c.state().selected == 2)


def test_shutdown_drops_queued_commands_and_refuses_new_ones(sweep600_dir, tmp_path):
    from opticfilm_linux.app.controller import Controller

    gate = threading.Event()
    calls: list[int] = []

    def factory(_dpi):
        calls.append(1)
        if len(calls) == 2:
            gate.wait(30)  # the second batch opened blocks the command worker
        return FakeScanner(sweep600_dir)

    c = Controller(factory)
    open_new(c, tmp_path / "b")
    BatchStore.create(tmp_path / "b2", name="B2", defaults=BatchDefaults(dpi=600)).close()
    other = tmp_path / "other"
    c.open_batch(tmp_path / "b2")
    time.sleep(0.2)
    c.new_batch(other, "Other", "", None, 600)  # queued behind it
    closer = threading.Thread(target=c.shutdown)
    closer.start()
    time.sleep(0.2)
    gate.set()
    closer.join(30)
    assert not closer.is_alive()
    c.new_batch(tmp_path / "late", "Late", "", None, 600)  # after shutdown: ignored
    time.sleep(0.3)
    assert not (other / "batch.json").exists() and not (tmp_path / "late").exists()


def test_failed_attach_releases_the_writer_lock(sweep600_dir, tmp_path):
    from opticfilm_linux.app.controller import Controller

    BatchStore.create(tmp_path / "b", name="T", defaults=BatchDefaults(dpi=600)).close()

    def broken(_dpi):
        raise OSError("no scanner backend")

    c = Controller(broken)
    try:
        c.open_batch(tmp_path / "b")
        wait_until(c, lambda: c.state().banner is not None)
        with BatchStore.open(tmp_path / "b"):  # the lock is free again
            pass
    finally:
        c.shutdown()


def test_hung_scanner_status_shows_error_and_does_not_freeze_the_app(
    sweep600_dir, tmp_path, monkeypatch
):
    from opticfilm_linux.app import controller as controller_module

    monkeypatch.setattr(controller_module, "STATUS_TIMEOUT_S", 0.3)
    hang = threading.Event()
    release = threading.Event()

    class Hanging(FakeScanner):
        def status(self):
            if hang.is_set():
                release.wait(60)
            return ScannerStatus.READY

    c = controller_module.Controller(lambda _dpi: Hanging(sweep600_dir))
    try:
        open_new(c, tmp_path / "b")
        wait_until(c, lambda: c.state().can_scan)
        hang.set()
        wait_until(c, lambda: c.state().scanner is ScannerStatus.ERROR)
        assert not c.state().can_scan and c.state().banner is not None
        c.dismiss_banner()
        c.scan()  # must be refused, not block the command worker
        c.select_tray(1)
        wait_until(c, lambda: c.state().banner is not None)
        closer = threading.Thread(target=c.shutdown)
        closer.start()
        closer.join(10)
        assert not closer.is_alive()
    finally:
        release.set()


def test_failed_reenhance_clears_the_updating_marker(sweep600_dir, tmp_path, monkeypatch):
    from opticfilm_linux.batch import runner as runner_module
    from opticfilm_linux.batch.model import SlideSettings

    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "b")
        scan_when_ready(c)
        wait_all_in_review(c, 1)
        c.select_tray(1)
        wait_until(c, lambda: len(c.state().slides) == 4)

        def broken(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(runner_module, "reenhance_slide", broken)
        c.edit_slide(1, 1, SlideSettings(brightness_ev=1.0))
        wait_until(c, lambda: c.state().banner is not None)
        wait_until(c, lambda: not any(s.updating for s in c.state().slides))
    finally:
        c.shutdown()
