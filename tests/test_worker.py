"""Scheduling tweaks must never prevent background work from running."""

import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from opticfilm_linux import worker


def test_linux_worker_is_niced_once(monkeypatch):
    monkeypatch.setattr(worker.sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(worker.os, "nice", lambda value: calls.append(value), raising=False)

    with ThreadPoolExecutor(max_workers=1, initializer=worker.lower_worker_priority) as pool:
        assert pool.submit(lambda: 1).result() == 1
        assert pool.submit(lambda: 2).result() == 2

    assert calls == [5]


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_other_platforms_leave_priority_unchanged(monkeypatch, platform):
    monkeypatch.setattr(worker.sys, "platform", platform)
    calls = []
    monkeypatch.setattr(worker.os, "nice", lambda value: calls.append(value), raising=False)

    worker.lower_worker_priority()

    assert calls == []


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("unavailable")])
def test_nice_failure_is_logged_and_worker_still_runs(monkeypatch, caplog, error):
    monkeypatch.setattr(worker.sys, "platform", "linux")

    def fail(value):
        raise error

    monkeypatch.setattr(worker.os, "nice", fail, raising=False)
    with caplog.at_level(logging.WARNING, logger=worker.__name__):
        with ThreadPoolExecutor(max_workers=1, initializer=worker.lower_worker_priority) as pool:
            assert pool.submit(lambda: "completed").result() == "completed"

    assert "Could not lower background worker priority" in caplog.text
