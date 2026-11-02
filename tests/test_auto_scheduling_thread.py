"""Manual-mode automatic cleanup eligibility is inspected off the UI thread."""

import dataclasses
import threading

from controller_helpers import make_controller, open_new, wait_until
from opticfilm_linux.app import controller as controller_module
from opticfilm_linux.batch.model import TrayRecord, TrayStage


def test_manual_auto_scheduling_reads_store_only_on_worker(sweep600_dir, tmp_path, monkeypatch):
    called = threading.Event()

    class Pipeline:
        def __init__(self, store, **kwargs):
            pass

        def arm(self, number):
            pass

        def run(self, number):
            assert number == 1
            called.set()

    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "batch")
        store = c._session.store
        store.update(
            lambda m: dataclasses.replace(
                m,
                trays=(
                    TrayRecord(1, TrayStage.EXPORTED, auto="verified", raw_removed=True),
                    TrayRecord(2, TrayStage.REVIEW, auto="pending"),
                ),
            )
        )
        original = store.snapshot
        ui_thread = threading.current_thread()
        reads = []

        def checked_snapshot():
            assert threading.current_thread() is not ui_thread
            reads.append(threading.current_thread().name)
            return original()

        monkeypatch.setattr(store, "snapshot", checked_snapshot)
        monkeypatch.setattr(controller_module, "AutoPipeline", Pipeline)
        with c._lock:
            c._auto_due.update((1, 2))
        c._submit_auto_due()
        assert called.wait(3)
        wait_until(c, lambda: not c._auto_active)
        assert reads
    finally:
        monkeypatch.undo()
        c.shutdown()


def test_rebuild_checks_recovery_on_background_worker(sweep600_dir, monkeypatch):
    controller = make_controller(sweep600_dir)
    started = threading.Event()
    release = threading.Event()
    caller = threading.current_thread()
    workers = []

    def check_recovery(session, manifest, pending):
        workers.append(threading.current_thread())
        started.set()
        assert release.wait(3)
        return frozenset()

    monkeypatch.setattr(controller, "_raw_recoverable", check_recovery)
    try:
        controller._rebuild()
        assert started.wait(3)
        assert workers and workers[0] is not caller
    finally:
        release.set()
        controller.shutdown()
