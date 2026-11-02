"""Cleanup owner decisions, committed recovery, and immutable release evidence."""

import dataclasses

import pytest

from opticfilm_linux.batch import export
from opticfilm_linux.batch.model import Flag, SlideStatus, TrayStage
from test_export_safety import _assert_raw_kept, _confirmed_blank, _delivered
from test_export_safety import delivery as delivery


@pytest.mark.parametrize("flagged", [False, True])
def test_blank_without_owner_decision_blocks_cleanup_and_freeable_list(delivery, flagged):
    store, _ = _delivered(delivery)
    slide = store.snapshot().trays[0].slides[0]
    blank = dataclasses.replace(
        slide, status=SlideStatus.BLANK, flags=(Flag.TOO_DARK,) if flagged else ()
    )
    store.update_tray(1, slides=(blank,), auto_flagged=(1,) if flagged else ())
    with pytest.raises(export.CleanupPolicyRefused) as refused:
        export.cleanup_raw(store, 1)
    assert str(refused.value) == "1 frame(s) look empty - confirm they are empty to free raw"
    assert export.freeable_trays(store) == ()
    _assert_raw_kept(store)
    store.update_tray(1, slides=(_confirmed_blank(store.snapshot().trays[0], blank),))
    assert [tray.number for tray in export.freeable_trays(store)] == [1]
    assert export.cleanup_raw(store, 1) > 0


@pytest.mark.parametrize("legacy", [False, True])
def test_interrupted_partial_deletion_requires_receipt_reverification(
    delivery, monkeypatch, legacy
):
    store, path = _delivered(delivery)
    (store.sweep_dir(1, 1) / "scan").unlink()
    path.unlink()
    tray = store.snapshot().trays[0]
    slide = dataclasses.replace(tray.slides[0], exports=(), preview_stale=True)
    store.update_tray(1, raw_removed=True, cleanup_sweeps=() if legacy else (1,), slides=(slide,))
    monkeypatch.setattr(export, "load_sweep", lambda *a: pytest.fail("raw IO"))
    with pytest.raises(export.ExportVerificationFailed):
        export.cleanup_raw(store, 1)
    assert store.sweep_dir(1, 1).exists()


@pytest.mark.parametrize("unsafe", ["symlink", "future", "unverified"])
def test_interrupted_cleanup_still_checks_paths_versions_and_membership(delivery, unsafe):
    store, _ = _delivered(delivery)
    store.update_tray(1, raw_removed=True, cleanup_sweeps=(1,))
    number = 2 if unsafe == "future" else 0
    extra = store.sweep_dir(1, number)
    if unsafe == "symlink":
        extra.symlink_to(store.sweep_dir(1, 1), target_is_directory=True)
    else:
        extra.mkdir()
    with pytest.raises(export.CleanupRefused):
        export.cleanup_raw(store, 1)
    assert store.sweep_dir(1, 1).exists()
    assert extra.exists()


@pytest.mark.parametrize("older", [False, True])
def test_missing_current_raw_refusal_does_not_claim_committed_removal(delivery, older):
    store, _ = _delivered(delivery)
    export.shutil.rmtree(store.sweep_dir(1, 1))
    if older:
        store.sweep_dir(1, 0).mkdir()
    with pytest.raises(export.CleanupRefused, match="current raw sweep is missing") as refused:
        export.cleanup_raw(store, 1)
    assert "removal pending" not in str(refused.value)
    assert not store.snapshot().trays[0].raw_removed


@pytest.mark.parametrize("cleanup", [False, True])
def test_release_evidence_digest_runs_outside_manifest_lock(delivery, monkeypatch, cleanup):
    store, _ = _delivered(delivery)
    digest = export._durable_digest
    checked = []

    def unlocked_digest(path):
        acquired = store._lock.acquire(blocking=False)
        assert acquired, "JPEG hashing must not hold the manifest lock"
        store._lock.release()
        checked.append(True)
        return digest(path)

    monkeypatch.setattr(export, "_durable_digest", unlocked_digest)
    if cleanup:
        export.cleanup_raw(store, 1, release_first=True)
    else:
        export.release_first_tray(store, 1)
    assert checked


@pytest.mark.parametrize("cleanup", [False, True])
@pytest.mark.parametrize("race", ["defaults", "tray"])
def test_release_commit_race_requires_retry_and_keeps_raw(delivery, monkeypatch, cleanup, race):
    store, _ = _delivered(delivery)
    update = store.update

    def changed_before_commit(change):
        monkeypatch.setattr(store, "update", update)
        if race == "defaults":
            update(
                lambda m: dataclasses.replace(
                    m, defaults=dataclasses.replace(m.defaults, rotation=90)
                )
            )
        else:
            tray = store.snapshot().trays[0]
            store.update_tray(1, slides=(dataclasses.replace(tray.slides[0], owner_decided=True),))
        return update(change)

    monkeypatch.setattr(store, "update", changed_before_commit)
    with pytest.raises(export.CleanupRetryRequired, match="changed meanwhile"):
        if cleanup:
            export.cleanup_raw(store, 1, release_first=True)
        else:
            export.release_first_tray(store, 1)
    _assert_raw_kept(store)
    assert not store.snapshot().first_tray_released


def _surviving_delivery(delivery):
    store, directory = delivery
    _delivered(delivery)
    export.cleanup_raw(store, 1)
    first = store.snapshot().trays[0]
    slide = dataclasses.replace(first.slides[0], exported=False, exports=())
    second = dataclasses.replace(
        first,
        number=2,
        raw_removed=False,
        stage=TrayStage.REVIEW,
        cleanup_sweeps=(),
        slides=(slide,),
    )
    store.update(
        lambda m: dataclasses.replace(
            m,
            trays=(first, second),
            defaults=dataclasses.replace(m.defaults, rotation=90, auto_export=True),
        )
    )
    store.sweep_dir(2, 1).mkdir(parents=True)
    (store.sweep_dir(2, 1) / "scan").write_bytes(b"new raw")
    export.export_batch(store, directory, "jpeg")
    return store


@pytest.mark.parametrize("cleanup", [False, True])
def test_surviving_tray_can_explicitly_approve_new_render_settings(delivery, cleanup):
    store = _surviving_delivery(delivery)
    with pytest.raises(export.CleanupPolicyRefused, match="explicit owner release"):
        export.cleanup_raw(store, 2)
    if cleanup:
        export.cleanup_raw(store, 2, release_first=True)
    else:
        export.release_first_tray(store, 2)
    manifest = store.snapshot()
    assert manifest.first_tray_released
    assert manifest.released_render == export._release_defaults(manifest.defaults)
    assert manifest.trays[1].raw_removed is cleanup


def test_surviving_tray_cannot_release_settings_from_an_old_receipt(delivery):
    store = _surviving_delivery(delivery)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, rotation=180))
    )
    with pytest.raises(export.CleanupRefused, match="settings differ"):
        export.cleanup_raw(store, 2, release_first=True)
    assert not store.snapshot().trays[1].raw_removed


@pytest.mark.parametrize("confirmed", [False, True])
def test_empty_first_requires_confirmation_and_later_jpeg_approval(delivery, confirmed):
    store, directory = delivery
    _delivered(delivery)
    first = store.snapshot().trays[0]
    blank = dataclasses.replace(
        first.slides[0], status=SlideStatus.BLANK, owner_decided=confirmed, exports=()
    )
    if confirmed:
        blank = _confirmed_blank(first, first.slides[0], exports=())
    second = dataclasses.replace(
        first,
        number=2,
        stage=TrayStage.REVIEW,
        slides=(dataclasses.replace(first.slides[0], exported=False),),
    )
    store.update(
        lambda m: dataclasses.replace(
            m,
            trays=(dataclasses.replace(first, slides=(blank,)), second),
            defaults=dataclasses.replace(m.defaults, auto_export=True),
        )
    )
    store.sweep_dir(2, 1).mkdir(parents=True)
    (store.sweep_dir(2, 1) / "scan").write_bytes(b"later JPEG evidence raw")
    export.export_batch(store, directory, "jpeg")
    assert not store.snapshot().first_tray_released
    if not confirmed:
        with pytest.raises(export.CleanupRefused, match="only the first tray"):
            export.release_first_tray(store, 2)
        return
    with pytest.raises(export.CleanupRefused, match="no verified JPEGs"):
        export.release_first_tray(store, 1)
    with pytest.raises(export.CleanupPolicyRefused, match="explicit owner release"):
        export.cleanup_raw(store, 1)
    export.release_first_tray(store, 2)
    assert export.cleanup_raw(store, 1) > 0
    assert not store.sweep_dir(1, 1).exists()
    assert store.sweep_dir(2, 1).exists()


def _two_delivered_trays(delivery):
    store, directory = delivery
    _delivered(delivery)
    first = store.snapshot().trays[0]
    slide = dataclasses.replace(first.slides[0], exported=False, exports=())
    second = dataclasses.replace(first, number=2, stage=TrayStage.REVIEW, slides=(slide,))
    store.update(
        lambda m: dataclasses.replace(
            m, trays=(first, second), defaults=dataclasses.replace(m.defaults, auto_export=True)
        )
    )
    store.sweep_dir(2, 1).mkdir(parents=True)
    (store.sweep_dir(2, 1) / "scan").write_bytes(b"second raw")
    export.export_batch(store, directory, "jpeg")
    return store


def _failed_first_release_reset(store, monkeypatch):
    update, checked = store.update, export._checked_snapshot

    def reject_postcommit(opened, tray, folders):
        if tray.number == 1 and not tray.raw_removed and opened.snapshot().trays[0].raw_removed:
            raise export.CleanupRefused("postcommit raw mismatch")
        checked(opened, tray, folders)

    def reject_only_reset(change):
        before = store.snapshot()
        after = change(before)
        if before.trays[0].raw_removed and not after.trays[0].raw_removed:
            raise OSError("no space for cleanup reset")
        return update(lambda _: after)

    monkeypatch.setattr(export, "_checked_snapshot", reject_postcommit)
    monkeypatch.setattr(store, "update", reject_only_reset)
    with pytest.raises(OSError, match="no space"):
        export.cleanup_raw(store, 1, release_first=True)
    assert store.snapshot().first_tray_released
    return update


@pytest.mark.parametrize("action", ["cleanup", "release"])
def test_invalid_first_release_blocks_later_tray_until_reset_then_allows_new_approval(
    delivery, monkeypatch, action
):
    store = _two_delivered_trays(delivery)
    update = _failed_first_release_reset(store, monkeypatch)
    with pytest.raises(OSError, match="no space"):
        if action == "cleanup":
            export.cleanup_raw(store, 2)
        else:
            export.release_first_tray(store, 2)
    assert store.sweep_dir(1, 1).exists() and store.sweep_dir(2, 1).exists()
    monkeypatch.setattr(store, "update", update)
    with pytest.raises(export.CleanupPolicyRefused, match="explicit owner release"):
        export.cleanup_raw(store, 2)
    assert not store.snapshot().first_tray_released
    assert not store.snapshot().trays[0].raw_removed
    export.release_first_tray(store, 1)
    assert export.cleanup_raw(store, 2) > 0
    assert store.snapshot().first_tray_released
    assert store.sweep_dir(1, 1).exists()
    assert not store.sweep_dir(2, 1).exists()


def test_cleanup_ignores_unrelated_tray_progress_during_commit(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    first = store.snapshot().trays[0]
    second = dataclasses.replace(first, number=2, stage=TrayStage.REVIEW, auto="off")
    store.update(lambda m: dataclasses.replace(m, trays=(first, second)))
    update = store.update
    changed = False

    def concurrent_progress(change):
        nonlocal changed
        if not changed:
            changed = True
            store.update_tray(2, error="unrelated progress")
        return update(change)

    monkeypatch.setattr(store, "update", concurrent_progress)
    assert export.cleanup_raw(store, 1) > 0
    assert store.snapshot().trays[1].error == "unrelated progress"
