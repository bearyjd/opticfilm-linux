import dataclasses
from pathlib import Path

import numpy as np
import pytest

from opticfilm_linux.batch import export as exports
from opticfilm_linux.batch import review
from opticfilm_linux.batch.model import (
    EmptyConfirmation,
    Flag,
    SlideStatus,
)
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.provenance import raw_region_sha256
from test_app_auto import pipeline

pytest_plugins = ("test_app_auto",)


@pytest.mark.parametrize("reopened", [False, True])
def test_auto_preserves_owner_needs_look_before_queued_or_resumed_run(store, reopened):
    auto = pipeline(store)
    auto.arm(2)
    review.set_slide_status(store, 2, 2, SlideStatus.NEEDS_LOOK)
    if reopened:
        directory = store.directory
        store.close()
        store = BatchStore.open(directory)
        auto = pipeline(store)
        assert 2 in auto.resume()
    try:
        auto.run(2)
        tray = store.snapshot().trays[1]
        assert tray.slides[1].status is SlideStatus.NEEDS_LOOK
        assert not tray.slides[1].exported
        assert not tray.raw_removed and store.sweep_dir(2, 1).exists()
    finally:
        if reopened:
            store.close()


def test_automatic_acceptance_is_not_an_owner_decision(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    slides = store.snapshot().trays[0].slides
    assert all(s.auto_accepted and not s.owner_decided for s in slides)
    review.set_slide_status(store, 1, 1, SlideStatus.ACCEPTED)
    slide = store.snapshot().trays[0].slides[0]
    assert slide.owner_decided and not slide.auto_accepted


def test_auto_flags_hold_cleanup_until_owner_review(store):
    tray = store.snapshot().trays[1]
    store.update_tray(
        2, slides=(dataclasses.replace(tray.slides[0], flags=(Flag.DUST,)), *tray.slides[1:])
    )
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    exports.release_first_tray(store, 1)
    auto.arm(2)
    auto.run(2)
    assert auto._tray(2).auto == "held"
    assert not auto._tray(2).raw_removed
    assert 2 not in auto.resume()
    review.set_slide_status(store, 2, 1, SlideStatus.ACCEPTED)
    assert 2 in auto.resume()
    auto.run(2)
    assert auto._tray(2).auto == "freed"


@pytest.mark.parametrize("keep_raw", [False, True])
@pytest.mark.parametrize("folders", [False, True])
def test_auto_disabled_only_reconciles_completed_cleanup(store, keep_raw, folders):
    import shutil

    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    store.update_tray(1, auto="verified", raw_removed=True)
    if not folders:
        shutil.rmtree(store.sweep_dir(1, 1))
    store.update(
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, auto_export=False, keep_raw=keep_raw)
        )
    )
    assert 1 not in auto.resume()
    assert auto._tray(1).auto == ("verified" if folders else "freed")
    auto.run(1)
    assert auto._tray(1).auto == ("verified" if folders else "freed")
    assert store.sweep_dir(1, 1).exists() is folders


def test_first_release_requires_reviewed_first_tray(store):
    auto = pipeline(store)
    auto.arm(1)
    review.set_slide_status(store, 1, 1, SlideStatus.NEEDS_LOOK)
    auto.run(1)
    assert auto._tray(1).auto == "blocked"
    auto.arm(2)
    auto.run(2)
    auto.confirm_free_held(1)
    assert not store.snapshot().first_tray_released
    assert not auto._tray(1).raw_removed
    assert 2 not in auto.resume()
    auto.run(2)
    assert not auto._tray(2).raw_removed


def test_owner_held_auto_slide_is_blocked_without_export_failure(store):
    auto = pipeline(store)
    auto.arm(2)
    review.set_slide_status(store, 2, 2, SlideStatus.NEEDS_LOOK)
    auto.run(2)
    tray = auto._tray(2)
    assert tray.auto == "blocked" and tray.auto_failed == ()
    assert "Owner excluded slides 2" in tray.auto_error
    assert not any(s.exported for s in tray.slides)


def test_keep_raw_held_tray_resumes_after_setting_change(store):
    store.update(
        lambda m: dataclasses.replace(
            m,
            first_tray_released=True,
            released_render=exports._release_defaults(m.defaults),
            defaults=dataclasses.replace(m.defaults, keep_raw=True),
        )
    )
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    exports.release_first_tray(store, 1)
    auto.arm(2)
    auto.run(2)
    assert auto._tray(2).auto == "held"
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, keep_raw=False))
    )
    assert 2 in auto.resume()
    auto.run(2)
    assert auto._tray(2).raw_removed


def test_owner_acceptance_resumes_blocked_auto_export(store):
    auto = pipeline(store)
    auto.arm(2)
    review.set_slide_status(store, 2, 2, SlideStatus.NEEDS_LOOK)
    auto.run(2)
    review.set_slide_status(store, 2, 2, SlideStatus.ACCEPTED)
    assert 2 in auto.resume()
    auto.run(2)
    assert auto._tray(2).auto == "held"
    assert all(s.exported for s in auto._tray(2).slides)


@pytest.mark.parametrize("phase", ["exported", "verified"])
def test_disabled_auto_preserves_incomplete_export_phase(store, phase):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    store.update_tray(1, auto=phase)
    store.update(
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, auto_export=False)
        )
    )
    auto.run(1)
    assert auto._tray(1).auto == phase
    assert not auto._tray(1).raw_removed


def test_owner_blank_is_skipped_without_blocking_other_exports(store):
    tray = store.snapshot().trays[1]
    store.update_tray(
        2,
        slides=(
            dataclasses.replace(tray.slides[0], status=SlideStatus.BLANK, owner_decided=True),
            *tray.slides[1:],
        ),
    )
    auto = pipeline(store)
    auto.arm(2)
    auto.run(2)
    tray = auto._tray(2)
    assert tray.auto == "held"
    assert not tray.slides[0].exported
    assert all(s.exported for s in tray.slides[1:])


def test_verified_tray_waits_for_preview_updates(store):
    auto = pipeline(store)
    auto.arm(2)
    auto.run(2)
    tray = auto._tray(2)
    store.update_tray(
        2,
        auto="verified",
        slides=(dataclasses.replace(tray.slides[0], preview_stale=True), *tray.slides[1:]),
    )
    auto.run(2)
    assert auto._tray(2).auto == "held"
    assert auto._tray(2).auto_failed == ()


@pytest.mark.parametrize("phase", ["", "off", "pending", "exported", "failed", "blocked"])
def test_release_requires_verified_held_first_tray(store, phase):
    auto = pipeline(store)
    store.update_tray(1, auto=phase)
    auto.confirm_free_held(1)
    assert not store.snapshot().first_tray_released
    assert not auto._tray(1).raw_removed


def test_release_refuses_corrupt_first_tray_jpeg(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    Path(auto._tray(1).slides[0].export_path).write_bytes(b"corrupt")
    auto.confirm_free_held(1)
    assert not store.snapshot().first_tray_released
    assert store.sweep_dir(1, 1).exists()


def test_freed_first_tray_cannot_release_changed_render(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    auto.confirm_free_held(1)
    prior = store.snapshot().released_render
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, jpeg_size="3600"))
    )
    auto.confirm_free_held(1)
    assert store.snapshot().released_render == prior
    assert store.snapshot().released_render != exports._release_defaults(store.snapshot().defaults)
    from opticfilm_linux.app.state import _auto_fields

    assert _auto_fields(store.snapshot(), False)["held_tray"] is None


def test_owner_rejection_resumes_blocked_auto_export(store):
    auto = pipeline(store)
    tray = auto._tray(2)
    store.update_tray(
        2,
        slides=(
            tray.slides[0],
            dataclasses.replace(tray.slides[1], status=SlideStatus.NEEDS_LOOK),
            tray.slides[2],
        ),
    )
    auto.arm(2)
    auto.run(2)
    assert auto._tray(2).auto == "blocked"
    review.set_slide_status(store, 2, 2, SlideStatus.NEEDS_LOOK)
    assert 2 in auto.resume()
    auto.run(2)
    tray = auto._tray(2)
    assert tray.auto == "blocked"
    assert tray.auto_failed == ()
    assert not tray.raw_removed and store.sweep_dir(2, 1).exists()
    assert not tray.slides[1].exported
    assert "Owner excluded slides 2" in tray.auto_error
    assert "Waiting for owner" not in tray.auto_error


def test_live_flags_hold_automatic_cleanup(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    first = auto._tray(1)
    store.update_tray(
        1, slides=(dataclasses.replace(first.slides[0], flags=(Flag.DUST,)), *first.slides[1:])
    )
    auto.confirm_free_held(1)
    assert not store.snapshot().first_tray_released
    assert store.sweep_dir(1, 1).exists()


@pytest.mark.parametrize("phase", ["", "off", "failed", "blocked", "freed"])
def test_release_button_only_for_verified_held_first_tray(store, phase):
    from opticfilm_linux.app.state import _auto_fields

    store.update_tray(1, auto=phase)
    assert _auto_fields(store.snapshot(), False)["held_tray"] is None


@pytest.mark.parametrize("phase", ["pending", "held"])
@pytest.mark.parametrize("live_flags", [False, True])
def test_blank_slots_ignore_live_and_remembered_flags(store, phase, live_flags):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    auto.arm(2)
    if phase == "held":
        auto.run(2)
    auto.confirm_free_held(1)
    tray = auto._tray(2)
    fingerprint = raw_region_sha256(exports.split_positions(None)[1])
    blank = dataclasses.replace(
        tray.slides[0],
        status=SlideStatus.BLANK,
        owner_decided=True,
        blank_fingerprint=fingerprint,
        empty_confirmed=EmptyConfirmation(tray.sweep_version, fingerprint),
        flags=(Flag.DUST,) if live_flags else (),
    )
    store.update_tray(2, auto_flagged=(1,), slides=(blank, *tray.slides[1:]))
    assert 2 in auto.resume()
    auto.run(2)
    tray = auto._tray(2)
    assert tray.auto == "freed" and tray.raw_removed
    assert not store.sweep_dir(2, 1).exists()
    if phase == "pending":
        assert tray.auto_flagged == (1,)
        assert not tray.slides[0].exported


@pytest.mark.parametrize("damage", ["raw_mismatch", "unsafe_path"])
def test_refused_first_cleanup_never_releases_later_trays(store, monkeypatch, damage):
    auto = pipeline(store)
    for number in (1, 2):
        auto.arm(number)
        auto.run(number)
    if damage == "raw_mismatch":
        split = exports.split_positions
        monkeypatch.setattr(
            exports,
            "split_positions",
            lambda sweep: {
                p: dataclasses.replace(s, rgb=np.zeros_like(s.rgb)) for p, s in split(sweep).items()
            },
        )
    else:
        sweep = store.sweep_dir(1, 1)
        outside = store.directory.parent / "outside-raw"
        sweep.rename(outside)
        sweep.symlink_to(outside, target_is_directory=True)
    auto.confirm_free_held(1)
    assert auto._tray(1).auto == "failed"
    assert not store.snapshot().first_tray_released
    assert store.snapshot().released_render is None
    assert 2 not in auto.resume()
    auto.run(2)
    assert all(not tray.raw_removed for tray in store.snapshot().trays)
    assert store.sweep_dir(2, 1).exists()


@pytest.mark.parametrize("policy", [False, True])
def test_cleanup_refusal_uses_exception_type_instead_of_message(store, monkeypatch, policy):
    from opticfilm_linux.app import auto as auto_module

    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    exception = (
        exports.CleanupPolicyRefused("Owner action required")
        if policy
        else exports.CleanupRefused("unsafe raw folder named keep raw")
    )

    def refuse(*args, **kwargs):
        raise exception

    monkeypatch.setattr(auto_module, "cleanup_raw", refuse)
    auto.confirm_free_held(1)
    tray = auto._tray(1)
    assert tray.auto == ("held" if policy else "failed")
    assert str(exception) in tray.auto_error
    assert not tray.raw_removed and store.sweep_dir(1, 1).exists()
    assert not store.snapshot().first_tray_released
