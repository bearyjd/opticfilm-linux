"""Batch changes reserve scanner ownership while draining background jobs."""

import threading

import pytest

from controller_helpers import open_new, wait_until
from test_app_controller_holder import build  # noqa: F401


@pytest.mark.parametrize("change", ["close", "open", "new"])
@pytest.mark.parametrize("move", ["eject_holder", "load_next_holder"])
def test_batch_change_blocks_holder_while_jobs_drain(build, tmp_path, monkeypatch, change, move):  # noqa: F811 - imported fixture
    c, scanner = build()
    directory = tmp_path / "b"
    open_new(c, directory)
    reached, release = threading.Event(), threading.Event()
    stop_jobs = c._stop_jobs

    def blocked_stop():
        reached.set()
        assert release.wait(10)
        stop_jobs()

    monkeypatch.setattr(c, "_stop_jobs", blocked_stop)
    changes = {
        "close": c.close_batch,
        "open": lambda: c.open_batch(directory),
        "new": lambda: c.new_batch(tmp_path / "next", "next", "", None, 600),
    }
    changes[change]()
    try:
        assert reached.wait(10)
        getattr(c, move)()
        c._holder_pool.submit(lambda: None).result(10)
        assert scanner.calls == []
        assert not c._holder_busy
        assert c._lock.acquire(timeout=1)
        c._lock.release()
    finally:
        release.set()
    c._command.submit(lambda: None).result(10)


def test_failed_batch_change_releases_holder_reservation(build, tmp_path):  # noqa: F811 - imported fixture
    c, scanner = build()
    open_new(c, tmp_path / "b")
    c.open_batch(tmp_path / "missing")
    c._command.submit(lambda: None).result(10)
    assert c._lifecycle_pending == 0
    open_new(c, tmp_path / "next")
    c.eject_holder()
    wait_until(c, lambda: scanner.calls == ["eject"] and not c._holder_busy)


def test_queued_batch_changes_keep_reservation_until_last_finishes(build, tmp_path, monkeypatch):  # noqa: F811 - imported fixture
    c, scanner = build()
    open_new(c, tmp_path / "b")
    reached, release = threading.Event(), threading.Event()
    detach = c._detach

    def blocked_detach():
        reached.set()
        assert release.wait(10)
        detach()

    monkeypatch.setattr(c, "_detach", blocked_detach)
    c.close_batch()
    try:
        assert reached.wait(10)
        c.close_batch()
        assert c._lifecycle_pending == 2
        c.eject_holder()
        assert scanner.calls == []
    finally:
        release.set()
    c._command.submit(lambda: None).result(10)
    assert c._lifecycle_pending == 0


def test_unsubmitted_batch_change_releases_reservation(build):  # noqa: F811 - imported fixture
    c, _scanner = build()
    c._command.shutdown()
    c.close_batch()
    assert c._lifecycle_pending == 0
