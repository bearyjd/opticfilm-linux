"""An owner can recover intact raw from a committed, interrupted cleanup."""

import dataclasses
import errno
import shutil
import stat

import numpy as np
import pytest

from opticfilm_linux.batch import export
from opticfilm_linux.batch.model import (
    BatchDefaults,
    Flag,
    SlideRecord,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.sweepstore import save_sweep
from synthetic import make_sweep


@pytest.fixture
def interrupted(tmp_path, monkeypatch):
    with BatchStore.create(
        tmp_path / "batch", name="Recovery", defaults=BatchDefaults(dpi=600)
    ) as store:
        save_sweep(make_sweep(dpi=600), store.sweep_dir(1, 1))
        slides = tuple(SlideRecord(p, status=SlideStatus.ACCEPTED) for p in range(1, 5))
        store.update(
            lambda m: dataclasses.replace(
                m, trays=(TrayRecord(1, TrayStage.REVIEW, slides=slides),)
            )
        )
        monkeypatch.setattr(
            export, "enhance_slide", lambda *_: np.full((32, 40, 3), 32000, np.uint16)
        )
        written = export.export_batch(store, tmp_path / "album", "jpeg").written
        store.update_tray(1, raw_removed=True, cleanup_sweeps=(1,), auto="freed")
        yield store, written


@pytest.mark.parametrize("legacy", [False, True])
def test_owner_recovery_reopens_intact_raw_without_delivery_files(interrupted, legacy):
    store, written = interrupted
    for path in written:
        path.unlink()
    if legacy:
        tray = store.snapshot().trays[0]
        store.update_tray(1, slides=tuple(dataclasses.replace(s, exports=()) for s in tray.slides))
    store.update(
        lambda m: dataclasses.replace(m, first_tray_released=True, released_render=m.defaults)
    )
    export.recover_interrupted_cleanup(store, 1)
    manifest = store.snapshot()
    tray = manifest.trays[0]
    assert not tray.raw_removed and tray.cleanup_sweeps == ()
    assert tray.stage is TrayStage.REVIEW and tray.auto == "held"
    assert not any(s.exported or s.export_path or s.empty_confirmed for s in tray.slides)
    assert not manifest.first_tray_released and manifest.released_render is None
    assert len(export.export_batch(store, written[0].parent, "jpeg").written) == 4


@pytest.mark.parametrize("missing", ["rgb.npy", "meta.json", "ir.npy"])
def test_owner_recovery_refuses_partial_current_raw(interrupted, missing):
    store, _ = interrupted
    (store.sweep_dir(1, 1) / missing).unlink()
    with pytest.raises(export.CleanupPolicyRefused, match="tray 1.*restore.*rescan"):
        export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot().trays[0].raw_removed


@pytest.mark.parametrize("change", ["extra", "legacy", "older_partial", "duplicate_version"])
def test_owner_recovery_requires_intact_remaining_inventory(interrupted, change):
    store, _ = interrupted
    if change == "older_partial":
        store.update_tray(1, cleanup_sweeps=(0, 1))
    if change == "older_partial":
        store.sweep_dir(1, 0).mkdir()
    elif change == "extra":
        store.sweep_dir(1, 0).mkdir()
    elif change == "duplicate_version":
        shutil.copytree(store.sweep_dir(1, 1), store.tray_dir(1) / "sweep-01")
    elif change == "legacy":
        store.update_tray(1, cleanup_sweeps=())
    with pytest.raises(export.CleanupPolicyRefused, match="tray 1"):
        export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot().trays[0].raw_removed


def test_owner_recovery_allows_missing_older_sweeps(interrupted):
    store, _ = interrupted
    store.update_tray(1, cleanup_sweeps=(0, 1))
    export.recover_interrupted_cleanup(store, 1)
    assert not store.snapshot().trays[0].raw_removed
    assert not store.sweep_dir(1, 0).exists()


@pytest.mark.parametrize("damage", ["none", "missing", "hash", "older_partial", "extra"])
def test_recovery_availability_matches_recovery_verification(interrupted, damage):
    from types import SimpleNamespace

    from opticfilm_linux.app.controller import Controller

    store, _ = interrupted
    store.update_tray(1, cleanup_sweeps=(0, 1))
    if damage == "missing":
        (store.sweep_dir(1, 1) / "rgb.npy").unlink()
    elif damage == "hash":
        raw = np.load(store.sweep_dir(1, 1) / "rgb.npy", mmap_mode="r+")
        raw[:] = 10000
        raw.flush()
    elif damage == "older_partial":
        store.sweep_dir(1, 0).mkdir()
    elif damage == "extra":
        store.sweep_dir(1, 2).mkdir()
    available = Controller._raw_recoverable(
        SimpleNamespace(store=store), store.snapshot(), frozenset({1})
    )
    assert available == (frozenset({1}) if damage == "none" else frozenset())


def test_recovery_availability_cache_rechecks_changed_raw(interrupted, monkeypatch):
    from opticfilm_linux.batch import cleanup_recovery

    store, _ = interrupted
    manifest = store.snapshot()
    tray = manifest.trays[0]
    assert cleanup_recovery.can_recover_interrupted_cleanup(store, manifest, tray)
    verify = cleanup_recovery._recovery_raw
    monkeypatch.setattr(
        cleanup_recovery, "_recovery_raw", lambda *_: pytest.fail("unchanged raw rechecked")
    )
    assert cleanup_recovery.can_recover_interrupted_cleanup(store, manifest, tray)
    monkeypatch.setattr(cleanup_recovery, "_recovery_raw", verify)
    (store.sweep_dir(1, 1) / "rgb.npy").unlink()
    assert not cleanup_recovery.can_recover_interrupted_cleanup(store, manifest, tray)


def test_owner_recovery_checks_every_recorded_sweep(interrupted):
    store, _ = interrupted
    shutil.copytree(store.sweep_dir(1, 1), store.sweep_dir(1, 0))
    store.update_tray(1, cleanup_sweeps=(0, 1))
    export.recover_interrupted_cleanup(store, 1)
    assert all(store.sweep_dir(1, version).exists() for version in (0, 1))
    assert not store.snapshot().trays[0].raw_removed


def test_owner_recovery_refuses_raw_hash_mismatch(interrupted):
    store, _ = interrupted
    raw = np.load(store.sweep_dir(1, 1) / "rgb.npy", mmap_mode="r+")
    raw[:] = 10000
    raw.flush()
    with pytest.raises(export.CleanupPolicyRefused, match="tray 1.*restore.*rescan"):
        export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot().trays[0].raw_removed


def test_owner_recovery_requires_complete_current_positions(interrupted):
    store, _ = interrupted
    tray = store.snapshot().trays[0]
    store.update_tray(1, slides=tray.slides[:-1])
    with pytest.raises(export.CleanupPolicyRefused, match="positions"):
        export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot().trays[0].raw_removed


def test_owner_recovery_rechecks_manifest_before_reset(interrupted, monkeypatch):
    store, _ = interrupted
    update = store.update

    def concurrent_edit(change):
        monkeypatch.setattr(store, "update", update)
        store.update_tray(1, error="concurrent change")
        return update(change)

    monkeypatch.setattr(store, "update", concurrent_edit)
    with pytest.raises(export.CleanupRetryRequired, match="changed meanwhile"):
        export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot().trays[0].raw_removed


def test_other_tray_cleanup_names_interrupted_blocker_as_policy(interrupted):
    store, _ = interrupted
    first = store.snapshot().trays[0]
    second = dataclasses.replace(first, number=2, raw_removed=False, cleanup_sweeps=())
    store.update(lambda m: dataclasses.replace(m, trays=(first, second), first_tray_released=True))
    (store.sweep_dir(1, 1) / "rgb.npy").unlink()
    with pytest.raises(export.CleanupPolicyRefused, match="tray 1.*recover"):
        export.check_cleanup_allowed(store, 2)
    assert not store.snapshot().trays[1].raw_removed


@pytest.mark.parametrize("policy", ["keep_raw", "flagged"])
def test_freeable_list_excludes_owner_policy_blocked_trays(interrupted, policy):
    store, _ = interrupted
    tray = store.snapshot().trays[0]
    store.update_tray(1, raw_removed=False, cleanup_sweeps=(), auto="")
    if policy == "keep_raw":
        store.update(
            lambda m: dataclasses.replace(
                m, defaults=dataclasses.replace(m.defaults, keep_raw=True)
            )
        )
    else:
        slides = tuple(
            dataclasses.replace(s, flags=(Flag.TOO_DARK,), owner_decided=False) for s in tray.slides
        )
        store.update_tray(1, slides=slides)
    assert export.freeable_trays(store) == ()


def test_recovery_rearms_automatic_export_after_revoking_release(interrupted):
    store, _ = interrupted
    store.update(
        lambda m: dataclasses.replace(
            m,
            defaults=dataclasses.replace(m.defaults, auto_export=True),
            first_tray_released=True,
            released_render=m.defaults,
        )
    )
    export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot().trays[0].auto == "pending"
    assert not store.snapshot().first_tray_released


def test_legacy_recovery_refuses_missing_configured_ir(interrupted):
    store, _ = interrupted
    tray = store.snapshot().trays[0]
    store.update_tray(1, slides=tuple(dataclasses.replace(s, exports=()) for s in tray.slides))
    (store.sweep_dir(1, 1) / "ir.npy").unlink()
    with pytest.raises(export.CleanupPolicyRefused, match="infrared"):
        export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot().trays[0].raw_removed


def test_legacy_recovery_allows_valid_scan_without_configured_ir(interrupted):
    store, _ = interrupted
    tray = store.snapshot().trays[0]
    store.update_tray(1, slides=tuple(dataclasses.replace(s, exports=()) for s in tray.slides))
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, infrared=False))
    )
    (store.sweep_dir(1, 1) / "ir.npy").unlink()
    export.recover_interrupted_cleanup(store, 1)
    assert not store.snapshot().trays[0].raw_removed


def test_recovery_rejects_symlinked_raw_member(interrupted, tmp_path):
    store, _ = interrupted
    raw = store.sweep_dir(1, 1) / "rgb.npy"
    target = tmp_path / "preserved.npy"
    raw.rename(target)
    raw.symlink_to(target)
    with pytest.raises(export.CleanupPolicyRefused, match="symlink"):
        export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot().trays[0].raw_removed
    assert target.exists()


def test_recovery_rechecks_folders_before_reset(interrupted, monkeypatch):
    store, _ = interrupted
    update = store.update

    def concurrent_folder(change):
        monkeypatch.setattr(store, "update", update)
        store.sweep_dir(1, 0).mkdir()
        return update(change)

    monkeypatch.setattr(store, "update", concurrent_folder)
    with pytest.raises(export.CleanupRetryRequired, match="folders changed meanwhile"):
        export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot().trays[0].raw_removed


def test_recovery_replaces_known_failed_live_rollback(interrupted):
    store, _ = interrupted
    manifest = store.snapshot()
    before = dataclasses.replace(
        manifest,
        trays=(
            dataclasses.replace(
                manifest.trays[0],
                raw_removed=False,
                cleanup_sweeps=(),
                auto="held",
            ),
        ),
    )
    export._PENDING_CLEANUP_RESETS.setdefault(store, {})[1] = (before, True)
    export.recover_interrupted_cleanup(store, 1)
    assert not store.snapshot().trays[0].raw_removed
    assert 1 not in export._PENDING_CLEANUP_RESETS[store]


@pytest.mark.parametrize("damage", ["export", "raw"])
def test_freeable_list_excludes_unverifiable_interrupted_cleanup(interrupted, damage):
    store, written = interrupted
    if damage == "export":
        written[0].unlink()
    else:
        (store.sweep_dir(1, 1) / "rgb.npy").unlink()
    assert export.freeable_trays(store) == ()


def test_freeable_release_requires_explicit_candidate_request(interrupted):
    store, _ = interrupted
    store.update_tray(1, raw_removed=False, cleanup_sweeps=(), auto="held")
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, auto_export=True))
    )
    assert export.freeable_trays(store) == ()
    assert [tray.number for tray in export.freeable_trays(store, release_first=True)] == [1]


def test_freeable_list_excludes_mismatched_render_receipts(interrupted):
    store, _ = interrupted
    store.update_tray(1, raw_removed=False, cleanup_sweeps=(), auto="verified")
    store.update(
        lambda m: dataclasses.replace(
            m,
            defaults=dataclasses.replace(m.defaults, auto_export=True, jpeg_size="3600"),
        )
    )
    assert export.freeable_trays(store, release_first=True) == ()


def test_freeable_list_refuses_malformed_receipt_path_without_aborting(interrupted):
    store, _ = interrupted
    tray = store.snapshot().trays[0]
    slide = tray.slides[0]
    record = dataclasses.replace(slide.exports[-1], path="/".join(["part"] * 100))
    slides = (dataclasses.replace(slide, exports=(record,)), *tray.slides[1:])
    store.update_tray(1, raw_removed=False, cleanup_sweeps=(), auto="held", slides=slides)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, auto_export=True))
    )
    assert export.freeable_trays(store, release_first=True) == ()
    with pytest.raises(export.CleanupPolicyRefused, match="invalid delivery path"):
        export.check_cleanup_allowed(store, 1, release_first=True)
    assert store.sweep_dir(1, 1).is_dir()


def test_recovery_compares_sweep_versions_numerically(interrupted):
    store, _ = interrupted
    for version in (2, 10):
        shutil.copytree(store.sweep_dir(1, 1), store.sweep_dir(1, version))
    store.update_tray(1, sweep_version=10, cleanup_sweeps=(1, 2, 10))
    export.recover_interrupted_cleanup(store, 1)
    assert not store.snapshot().trays[0].raw_removed
    assert all(store.sweep_dir(1, version).is_dir() for version in (1, 2, 10))


@pytest.mark.parametrize("error", [errno.EINVAL, errno.ENOTSUP, errno.EBADF])
def test_verifier_rejects_same_directory_sync_errors_as_writer(interrupted, monkeypatch, error):
    store, _ = interrupted
    sync = export.os.fsync

    def unsupported(fd):
        if stat.S_ISDIR(export.os.fstat(fd).st_mode):
            raise OSError(error, "directory sync failed")
        sync(fd)

    monkeypatch.setattr(export.os, "fsync", unsupported)
    with pytest.raises(export.ExportVerificationFailed) as failed:
        export.verify_tray_export(store, 1)
    assert isinstance(failed.value.__cause__, OSError)
    assert failed.value.__cause__.errno == error
    assert store.sweep_dir(1, 1).is_dir()


def test_partial_cleanup_message_directs_owner_to_raw_recovery(interrupted, monkeypatch):
    store, _ = interrupted
    store.update_tray(1, raw_removed=False, cleanup_sweeps=(), auto="")

    def partial_delete(path):
        (path / "rgb.npy").unlink()
        raise OSError("power loss")

    monkeypatch.setattr(export.shutil, "rmtree", partial_delete)
    with pytest.raises(export.BatchError, match="original raw folders, or rescan") as refused:
        export.cleanup_raw(store, 1)
    assert "free disk space again to finish" not in str(refused.value)
    assert store.snapshot().trays[0].raw_removed
