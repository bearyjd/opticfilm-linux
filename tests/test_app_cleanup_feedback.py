"""Cleanup progress, policy feedback, and refresh workers stay responsive."""

import threading
from dataclasses import replace

import pytest

from opticfilm_linux.app import auto, jobs
from opticfilm_linux.app.recovery_checks import RecoveryChecks
from opticfilm_linux.batch.model import Flag, SlideRecord, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchStore


@pytest.mark.parametrize("policy", ["keep", "release", "flags"])
def test_free_space_names_hold_policy(tmp_path, monkeypatch, policy):
    monkeypatch.setattr(jobs, "freeable_trays", lambda _: ())
    with BatchStore.create(tmp_path / "batch", "B") as store:
        slides = (SlideRecord(1, flags=(Flag.TOO_DARK,)),) if policy == "flags" else ()
        store.update(
            lambda m: replace(
                m,
                defaults=replace(
                    m.defaults, keep_raw=policy == "keep", auto_export=policy == "release"
                ),
                trays=(TrayRecord(1, TrayStage.EXPORTED, slides=slides),),
            )
        )
        message = jobs.free_space_candidates(store).message
        expected = {"keep": "Keep raw", "release": "first-tray", "flags": "owner review"}
        assert expected[policy] in message


@pytest.mark.parametrize("already_removed", [False, True])
def test_cleanup_completion_notifies_without_redundant_state_write(
    tmp_path, monkeypatch, already_removed
):
    with BatchStore.create(tmp_path / "batch", "B") as store:
        store.update(
            lambda m: replace(
                m,
                trays=(
                    TrayRecord(1, TrayStage.EXPORTED, auto="verified", raw_removed=already_removed),
                ),
            )
        )
        updates = []
        pipeline = auto.AutoPipeline(store, on_update=lambda: updates.append("changed"))
        monkeypatch.setattr(pipeline, "_cleanup_permitted", lambda *_: True)
        monkeypatch.setattr(
            auto,
            "cleanup_raw",
            lambda *_a, **_kw: store.update_tray(1, raw_removed=True, auto="freed"),
        )
        if already_removed:
            pipeline._verify_and_finish(1)
        else:
            pipeline._finish_verified(1, release=False)
        assert updates == ["changed"]
        assert store.snapshot().trays[0].auto == "freed"


def test_recovery_decoding_does_not_block_refresh_or_reuse_other_batch():
    entered, release = threading.Event(), threading.Event()
    caller = threading.get_ident()

    def check(*_):
        assert threading.get_ident() != caller
        entered.set()
        assert release.wait(3)
        return frozenset({1})

    checks = RecoveryChecks()
    session = object()
    try:
        assert checks.poll(session, None, frozenset({1}), check) == frozenset()
        assert entered.wait(3)
        release.set()
        checks._future.result(timeout=3)
        assert checks.poll(session, None, frozenset({1}), check) == frozenset({1})
        assert checks.poll(object(), None, frozenset({1}), check) == frozenset()
    finally:
        release.set()
        checks.close()


def test_free_space_reports_trays_that_became_ineligible(tmp_path, monkeypatch):
    def clean(_store, **kwargs):
        kwargs["on_progress"](1, 1)
        return 10

    monkeypatch.setattr(jobs, "free_space", clean)
    monkeypatch.setattr(jobs, "freeable_trays", lambda _: pytest.fail("redundant verification"))
    with BatchStore.create(tmp_path / "batch", "B") as store:
        views = []
        jobs.run_free_space(store, threading.Event(), views.append, (1, 2))
    assert views[-1].total == 1
    assert views[-1].freed == 10
    assert "1 tray(s) kept" in views[-1].message


def test_closed_recovery_worker_cannot_be_resubmitted():
    checks = RecoveryChecks()
    checks.close()
    assert checks.poll(None, None, frozenset(), lambda *_: pytest.fail("closed")) == frozenset()
