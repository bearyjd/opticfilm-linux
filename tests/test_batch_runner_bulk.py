"""Bulk rendering yields the enhance worker after each slide."""

import dataclasses
import threading

import pytest

from opticfilm_linux.batch import runner as runner_module
from opticfilm_linux.batch.events import SlideUpdated, TrayFailed
from opticfilm_linux.batch.model import SlideRecord, TrayRecord, TrayStage
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchStore


@pytest.fixture
def bulk(tmp_path):
    with BatchStore.create(tmp_path / "batch", name="bulk") as store:
        trays = tuple(
            TrayRecord(n, TrayStage.REVIEW, slides=tuple(SlideRecord(p) for p in range(1, 5)))
            for n in range(1, 11)
        )
        store.update(lambda m: dataclasses.replace(m, trays=trays))
        runner = BatchRunner(store, None)
        yield store, runner
        runner.close()


def _render(store, number, position):
    def changed(manifest):
        trays = tuple(
            dataclasses.replace(
                t,
                slides=tuple(
                    dataclasses.replace(s, preview_stale=False) if s.position == position else s
                    for s in t.slides
                ),
            )
            if t.number == number
            else t
            for t in manifest.trays
        )
        return dataclasses.replace(manifest, trays=trays)

    store.update(changed)


def test_bulk_yields_to_tray_enhance_and_coalesces_requests(bulk, monkeypatch):
    store, runner = bulk
    started, release = threading.Event(), threading.Event()
    calls = []

    def render(store, number, position):
        assert runner._lock.acquire(blocking=False)
        runner._lock.release()
        calls.append((number, position))
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        _render(store, number, position)

    monkeypatch.setattr(runner_module, "reenhance_slide", render)
    keys = [(n, p) for n in range(1, 11) for p in range(1, 5)]
    try:
        result = runner.edit_many(keys, {"brightness_ev": 1.0})
        assert not result.skipped
        assert len(result.before) == 40
        assert started.wait(5)
        runner._submit(runner._enhance_pool, lambda n: calls.append("enhance"), 11)
        runner.edit_many([(10, 4)], {"mirror": True})
    finally:
        release.set()
    assert runner.wait_idle(10)
    assert calls[1] == "enhance"
    assert len(calls) == 41
    assert not any(s.preview_stale for t in store.snapshot().trays for s in t.slides)
    updates = [e for e in list(runner.events.queue) if isinstance(e, SlideUpdated)]
    assert {(e.number, e.position) for e in updates} == set(keys)


def test_failed_render_does_not_loop_or_prevent_other_slides(bulk, monkeypatch):
    store, runner = bulk
    calls = []

    def render(store, number, position):
        calls.append((number, position))
        if position == 1:
            raise ValueError("broken raw")
        _render(store, number, position)

    monkeypatch.setattr(runner_module, "reenhance_slide", render)
    runner.edit_many([(1, 1), (1, 2)], {"mirror": True})
    assert runner.wait_idle(5)
    assert calls == [(1, 1), (1, 2)]
    assert any(isinstance(e, TrayFailed) for e in list(runner.events.queue))
    runner.edit_many([(1, 1)], {"rotation": 90})
    assert runner.wait_idle(5)
    assert calls[-1] == (1, 1)
    assert len(calls) == 3


@pytest.mark.parametrize("stage,freed", [(TrayStage.ENHANCING, False), (TrayStage.REVIEW, True)])
def test_bulk_does_not_render_ineligible_slides(bulk, monkeypatch, stage, freed):
    store, runner = bulk
    store.update_tray(1, stage=stage, raw_removed=freed)
    calls = []
    monkeypatch.setattr(runner_module, "reenhance_slide", lambda *args: calls.append(args))
    runner.edit_many([(1, 1)], {"mirror": True})
    assert runner.wait_idle(5)
    assert calls == []


def test_close_finishes_the_whole_drain(bulk, monkeypatch):
    store, runner = bulk
    monkeypatch.setattr(runner_module, "reenhance_slide", _render)
    runner.edit_many([(1, p) for p in range(1, 5)], {"mirror": True})
    runner.close()
    assert all(not s.preview_stale for s in store.snapshot().trays[0].slides)


def test_empty_bulk_edit_stays_idle(bulk):
    _, runner = bulk
    assert not runner.edit_many([], {"mirror": True}).before
    assert runner.wait_idle(5)


def test_stop_leaves_unrendered_slides_for_resume(bulk, monkeypatch):
    store, runner = bulk
    started, release = threading.Event(), threading.Event()
    calls = []

    def render(store, number, position):
        calls.append((number, position))
        started.set()
        assert release.wait(5)
        _render(store, number, position)

    monkeypatch.setattr(runner_module, "reenhance_slide", render)
    runner.edit_many([(1, 1), (1, 2)], {"mirror": True})
    assert started.wait(5)
    # stop's flag prevents a running drain from re-submitting after shutdown.
    with runner._lock:
        runner._stopping = True
    release.set()
    runner.stop()
    assert calls == [(1, 1)]
    assert store.snapshot().trays[0].slides[1].preview_stale
    monkeypatch.setattr(runner_module, "reenhance_slide", _render)
    again = BatchRunner(store, None)
    try:
        again.resume()
        assert again.wait_idle(5)
        assert not store.snapshot().trays[0].slides[1].preview_stale
    finally:
        again.close()


def test_resume_ignores_freed_stale_previews(bulk, monkeypatch):
    store, runner = bulk
    store.update_tray(
        1,
        raw_removed=True,
        slides=(SlideRecord(1, preview_stale=True),),
    )
    calls = []
    monkeypatch.setattr(runner_module, "reenhance_slide", lambda *args: calls.append(args))
    runner.resume()
    assert runner.wait_idle(5)
    assert calls == []


def test_unexpected_drain_failure_allows_a_later_edit_to_retry(bulk, monkeypatch):
    _, runner = bulk
    original = runner._next_stale

    def broken(attempted):
        raise OSError("manifest unavailable")

    monkeypatch.setattr(runner, "_next_stale", broken)
    runner.edit_many([(1, 1)], {"mirror": True})
    assert runner.wait_idle(5)
    assert not runner._stale_drain_queued
    monkeypatch.setattr(runner, "_next_stale", original)
    monkeypatch.setattr(runner_module, "reenhance_slide", _render)
    runner.edit_many([(1, 1)], {"rotation": 90})
    assert runner.wait_idle(5)
    assert not runner._store.snapshot().trays[0].slides[0].preview_stale
