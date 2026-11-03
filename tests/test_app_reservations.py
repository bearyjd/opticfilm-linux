"""Automatic jobs release reservations even when workers reject or cancel them."""
# ruff: noqa: F811

import threading
from concurrent.futures import Future

import pytest

from controller_helpers import wait_until
from opticfilm_linux.batch.model import SlideRecord, SlideStatus
from opticfilm_linux.batch.review import accept_all
from test_app_auto_controller import auto_controller  # noqa: F401


@pytest.mark.parametrize("worker", ["_command", "_jobs"])
def test_rejected_auto_handoff_releases_tray(auto_controller, monkeypatch, worker):
    c, calls, _, _ = auto_controller

    def reject(*args, **kwargs):
        raise RuntimeError("worker shut down")

    monkeypatch.setattr(getattr(c, worker), "submit", reject)
    c.confirm_free_held(1)
    wait_until(c, lambda: not c._auto_active and c._long_jobs == 0, timeout=2)
    assert calls == []


@pytest.mark.parametrize("worker", ["_command", "_jobs"])
def test_cancelled_auto_handoff_releases_tray(auto_controller, monkeypatch, worker):
    c, calls, _, _ = auto_controller
    submitted = threading.Event()
    future = Future()

    def hold(*args, **kwargs):
        submitted.set()
        return future

    monkeypatch.setattr(getattr(c, worker), "submit", hold)
    c.confirm_free_held(1)
    assert submitted.wait(2)
    assert future.cancel()
    wait_until(c, lambda: not c._auto_active and c._long_jobs == 0, timeout=2)
    assert calls == []


def test_accept_all_preserves_explicit_exclusion_and_block(auto_controller):
    c, _, _, _ = auto_controller
    store = c._session.store
    store.update_tray(
        1,
        auto="blocked",
        auto_error="Owner excluded slides 1; blocked by your decision; raw scans kept",
        slides=(
            SlideRecord(1, SlideStatus.NEEDS_LOOK, owner_decided=True),
            SlideRecord(2, SlideStatus.PENDING),
        ),
    )
    before = store.snapshot().trays[0]
    accept_all(store)
    after = store.snapshot().trays[0]
    assert after.slides[0] == before.slides[0]
    assert after.slides[1].status is SlideStatus.ACCEPTED
    assert after.auto == before.auto and after.auto_error == before.auto_error
    from opticfilm_linux.app.auto import AutoPipeline

    assert not AutoPipeline(store)._eligible(after)


def test_old_completion_preserves_followup_reservation(auto_controller, monkeypatch):
    c, _, _, _ = auto_controller
    captured = []
    monkeypatch.setattr(
        c,
        "_after_pending_commands",
        lambda job, message, **kw: captured.append((job, kw["on_finished"])),
    )
    c.confirm_free_held(1)
    c._auto_due.add(1)
    captured[0][0](c._session.runner)
    captured[0][1]()
    assert len(captured) == 2
    assert 1 in c._auto_active
    captured[1][1]()
    assert not c._auto_active


def test_controller_accept_all_does_not_queue_owner_exclusion(auto_controller, monkeypatch):
    c, _, _, _ = auto_controller
    store = c._session.store
    store.update_tray(
        1,
        auto="blocked",
        auto_error="blocked by your decision",
        slides=(SlideRecord(1, SlideStatus.NEEDS_LOOK, owner_decided=True),),
    )
    queued = []
    monkeypatch.setattr(c, "_queue_auto", lambda *a, **kw: queued.append(a))
    from opticfilm_linux.app.auto import AutoPipeline

    monkeypatch.setattr(c, "_auto_pipeline", lambda opened: AutoPipeline(opened))
    c.accept_all()
    c._command.submit(lambda: None).result(2)
    assert queued == []
    assert store.snapshot().trays[0].slides[0].status is SlideStatus.NEEDS_LOOK


@pytest.mark.parametrize("persistent", [False, True])
def test_cleanup_commit_race_retries_once_without_spinning(
    auto_controller, monkeypatch, persistent
):
    c, _, _, _ = auto_controller
    calls = []

    class Pipeline:
        def arm(self, number):
            pass

        def run(self, number):
            calls.append(number)
            retry = len(calls) == 1 or persistent
            c._session.store.update_tray(
                number,
                auto="verified" if retry else "freed",
                auto_error="tray changed meanwhile; retry cleanup" if retry else "",
            )

    monkeypatch.setattr(c, "_auto_pipeline", lambda opened: Pipeline())
    c._auto_due.add(1)
    c._submit_auto_due()
    wait_until(c, lambda: len(calls) == 2 and not c._auto_active)
    assert not c._auto_due
    for _ in range(3):
        c.tick()
    assert calls == [1, 1]
    tray = c._session.store.snapshot().trays[0]
    assert (
        (tray.auto == "verified" and bool(tray.auto_error)) if persistent else tray.auto == "freed"
    )


def test_recovery_queues_automatic_reexport_in_same_session(auto_controller, monkeypatch):
    from opticfilm_linux.batch import export

    c, _, _, _ = auto_controller
    queued = []

    def recover(store, number):
        store.update_tray(number, auto="pending", auto_error="", raw_removed=False)

    monkeypatch.setattr(export, "recover_interrupted_cleanup", recover)
    monkeypatch.setattr(c, "_queue_auto", lambda session, number: queued.append((session, number)))
    c.recover_interrupted_cleanup(1)
    wait_until(c, lambda: bool(queued), timeout=2)
    assert queued == [(c._session, 1)]
    assert c._session.store.snapshot().trays[0].auto == "pending"
