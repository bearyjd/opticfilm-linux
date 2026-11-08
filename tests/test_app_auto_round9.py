"""Regression coverage for owner gates and recoverable automatic cleanup."""

import dataclasses
from pathlib import Path

import pytest

from opticfilm_linux.app import auto as automatic
from opticfilm_linux.batch import export as exports
from opticfilm_linux.batch import review
from opticfilm_linux.batch.empty import empty_frame_fingerprints
from opticfilm_linux.batch.model import Flag, SlideStatus
from opticfilm_linux.provenance import raw_region_sha256
from test_app_auto import pipeline

pytest_plugins = ("test_app_auto",)


@pytest.mark.parametrize("live_flags", [False, True])
def test_detector_blank_requires_owner_confirmation_before_raw_cleanup(store, live_flags):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    auto.confirm_free_held(1)
    tray = auto._tray(2)
    blank = dataclasses.replace(
        tray.slides[0],
        status=SlideStatus.BLANK,
        blank_fingerprint=raw_region_sha256(exports.split_positions(None)[1]),
    )
    store.update_tray(2, slides=(blank, *tray.slides[1:]), auto_flagged=(1,) if live_flags else ())
    auto.arm(2)
    auto.run(2)
    held = auto._tray(2)
    assert held.auto == "held" and not held.raw_removed
    assert held.auto_error == "1 frame(s) look empty - confirm they are empty to free raw"
    assert held.auto_flagged == (1,)
    assert not held.slides[0].exported
    assert all(s.exported for s in held.slides[1:])
    assert 2 not in auto.resume()
    review.confirm_empty_frames(
        store,
        2,
        (1,),
        expected_fingerprints=empty_frame_fingerprints(auto._tray(2)),
        expected_sweep_version=auto._tray(2).sweep_version,
    )
    assert 2 in auto.resume()
    auto.run(2)
    assert auto._tray(2).raw_removed
    assert not auto._tray(2).slides[0].exported


def test_owner_exclusion_blocks_without_failure_or_repeated_resume(store):
    auto = pipeline(store)
    review.set_slide_status(store, 2, 2, SlideStatus.NEEDS_LOOK)
    auto.arm(2)
    auto.run(2)
    tray = auto._tray(2)
    assert tray.auto == "blocked" and tray.auto_failed == ()
    assert "blocked by your decision" in tray.auto_error
    assert "raw data kept" not in tray.auto_error
    assert not any(s.exported for s in tray.slides)
    assert 2 not in auto.resume()
    review.set_slide_status(store, 2, 2, SlideStatus.ACCEPTED)
    assert 2 in auto.resume()


def test_transient_cleanup_commit_race_is_retryable_on_resume(store, monkeypatch):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    auto.confirm_free_held(1)
    cleanup = automatic.cleanup_raw

    def race(*args, **kwargs):
        raise exports.CleanupRetryRequired("tray changed meanwhile; retry cleanup")

    monkeypatch.setattr(automatic, "cleanup_raw", race)
    auto.arm(2)
    auto.run(2)
    assert auto._tray(2).auto == "verified"
    assert auto._tray(2).auto_failed == ()
    assert 2 in auto.resume()
    assert store.sweep_dir(2, 1).exists()
    monkeypatch.setattr(automatic, "cleanup_raw", cleanup)
    auto.run(2)
    assert auto._tray(2).auto == "freed"
    assert auto._tray(2).auto_error == ""


def test_changed_settings_hold_backlog_until_surviving_tray_reapproves(store):
    auto = pipeline(store)
    for number in (1, 2):
        auto.arm(number)
        auto.run(number)
    auto.confirm_free_held(1)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, jpeg_size="3600"))
    )
    assert 2 not in auto.resume()
    assert not auto._tray(2).raw_removed
    keys = frozenset({(2, slide.position) for slide in auto._tray(2).slides})
    exports.clear_exported(store, keys)
    exports.export_batch(
        store, Path(store.snapshot().defaults.export_dir), "jpeg", keys=keys, jpeg_size="3600"
    )
    auto.confirm_free_held(2)
    assert auto._tray(2).auto == "freed"
    assert store.snapshot().released_render == exports._release_defaults(store.snapshot().defaults)


def test_reapproval_of_old_receipt_stays_held_until_manual_reexport(store):
    from opticfilm_linux.app.state import _auto_fields

    auto = pipeline(store)
    for number in (1, 2):
        auto.arm(number)
        auto.run(number)
    auto.confirm_free_held(1)
    prior = store.snapshot().released_render
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, jpeg_size="3600"))
    )
    auto.confirm_free_held(2)
    held = auto._tray(2)
    assert held.auto == "held" and held.auto_failed == ()
    assert not held.raw_removed and store.sweep_dir(2, 1).exists()
    assert "re-export" in held.auto_error
    assert _auto_fields(store.snapshot(), False)["held_tray"] == 2
    assert store.snapshot().released_render == prior
    keys = frozenset({(2, slide.position) for slide in held.slides})
    exports.clear_exported(store, keys)
    exports.export_batch(
        store, Path(store.snapshot().defaults.export_dir), "jpeg", keys=keys, jpeg_size="3600"
    )
    assert auto._tray(2).auto == "held"
    assert _auto_fields(store.snapshot(), False)["held_tray"] == 2
    auto.confirm_free_held(2)
    assert auto._tray(2).auto == "freed"
    assert store.snapshot().released_render == exports._release_defaults(store.snapshot().defaults)


def test_verified_tray_waits_for_current_settings_approval_before_cleanup(store, monkeypatch):
    auto = pipeline(store)
    for number in (1, 2):
        auto.arm(number)
        auto.run(number)
    auto.confirm_free_held(1)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, jpeg_size="3600"))
    )
    store.update_tray(2, auto="verified")

    def unexpected_cleanup(*args, **kwargs):
        raise AssertionError("cleanup attempted before current settings were approved")

    monkeypatch.setattr(automatic, "cleanup_raw", unexpected_cleanup)
    auto.run(2)
    assert auto._tray(2).auto == "held"
    assert auto._tray(2).auto_error == "Waiting for first-tray raw removal approval"
    assert 2 not in auto.resume()


@pytest.mark.parametrize("blocked", ["blank", "flagged", "stale", "empty", "incomplete"])
def test_reapproval_skips_surviving_trays_that_still_need_owner_review(store, blocked):
    from opticfilm_linux.app.state import _auto_fields

    store.update(
        lambda m: dataclasses.replace(
            m, trays=(*m.trays, dataclasses.replace(m.trays[1], number=3))
        )
    )
    folder = store.sweep_dir(3, 1)
    folder.mkdir(parents=True)
    (folder / "raw").write_bytes(b"third tray raw")
    auto = pipeline(store)
    for number in (1, 2, 3):
        auto.arm(number)
        auto.run(number)
    auto.confirm_free_held(1)
    tray = auto._tray(2)
    changes = {
        "blank": {"status": SlideStatus.BLANK},
        "flagged": {"flags": (Flag.TOO_DARK,)},
        "stale": {"preview_stale": True},
        "empty": {"status": SlideStatus.BLANK, "owner_decided": True},
        "incomplete": {"exported": False},
    }[blocked]
    slides = tuple(dataclasses.replace(s, **changes) for s in tray.slides)
    store.update_tray(2, slides=slides)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, jpeg_size="3600"))
    )
    keys = frozenset({(3, s.position) for s in auto._tray(3).slides})
    exports.clear_exported(store, keys)
    exports.export_batch(
        store, Path(store.snapshot().defaults.export_dir), "jpeg", keys=keys, jpeg_size="3600"
    )
    assert _auto_fields(store.snapshot(), False)["held_tray"] == 3
    auto.confirm_free_held(3)
    assert auto._tray(3).raw_removed
    assert not auto._tray(2).raw_removed


def test_confirmed_empty_first_tray_waits_for_later_jpeg_approval_then_cleans_up(store):
    from opticfilm_linux.app.state import _auto_fields

    auto = pipeline(store)
    first = auto._tray(1)
    store.update_tray(
        1,
        slides=tuple(
            dataclasses.replace(
                s,
                status=SlideStatus.BLANK,
                blank_fingerprint=raw_region_sha256(exports.split_positions(None)[s.position]),
            )
            for s in first.slides
        ),
    )
    for number in (1, 2):
        auto.arm(number)
        auto.run(number)
    assert _auto_fields(store.snapshot(), False)["held_tray"] is None
    review.confirm_empty_frames(
        store,
        1,
        (1, 2, 3),
        expected_fingerprints=empty_frame_fingerprints(auto._tray(1)),
        expected_sweep_version=auto._tray(1).sweep_version,
    )
    assert _auto_fields(store.snapshot(), False)["held_tray"] == 2
    assert auto.resume() == ()
    assert all(not t.raw_removed for t in store.snapshot().trays)
    auto.confirm_free_held(1)
    assert not store.snapshot().first_tray_released
    auto.confirm_free_held(2)
    assert auto._tray(2).raw_removed
    assert 1 in auto.resume()
    auto.run(1)
    assert auto._tray(1).raw_removed
    assert all(s.status is SlideStatus.BLANK and not s.exported for s in auto._tray(1).slides)
    assert not store.sweep_dir(1, 1).exists()


def test_committed_cleanup_with_remaining_raw_waits_for_owner_recovery(store, monkeypatch):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    tray = auto._tray(1)
    for slide in tray.slides:
        Path(slide.export_path).unlink()
    slides = tuple(dataclasses.replace(s, exports=()) for s in tray.slides)
    store.update_tray(1, auto="verified", raw_removed=True, cleanup_sweeps=(1,), slides=slides)

    def unexpected_verification(*args, **kwargs):
        pytest.fail("committed tombstone recovery must not verify delivery artifacts")

    monkeypatch.setattr(automatic, "verify_tray_export", unexpected_verification)
    assert 1 not in auto.resume()
    auto.run(1)
    assert auto._tray(1).auto == "verified"
    assert store.sweep_dir(1, 1).exists()


def test_confirmed_empty_warning_is_cleared_while_raw_stays_held(store):
    auto = pipeline(store)
    tray = auto._tray(1)
    blank = dataclasses.replace(
        tray.slides[0],
        status=SlideStatus.BLANK,
        blank_fingerprint=raw_region_sha256(exports.split_positions(None)[1]),
    )
    store.update_tray(1, slides=(blank, *tray.slides[1:]))
    auto.arm(1)
    auto.run(1)
    assert "look empty" in auto._tray(1).auto_error
    review.confirm_empty_frames(
        store,
        1,
        (1,),
        expected_fingerprints=empty_frame_fingerprints(auto._tray(1)),
        expected_sweep_version=tray.sweep_version,
    )
    auto._verify_and_finish(1)
    assert auto._tray(1).auto == "held"
    assert auto._tray(1).auto_error == "Waiting for first-tray raw removal approval"
    assert not auto._tray(1).raw_removed
