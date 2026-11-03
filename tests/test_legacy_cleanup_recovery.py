"""Legacy cleanup markers require verified current raw and fresh delivery receipts."""

import dataclasses
import shutil

import pytest

from opticfilm_linux.batch import export
from opticfilm_linux.batch.cleanup_recovery import can_recover_interrupted_cleanup
from opticfilm_linux.batch.model import TrayStage

pytest_plugins = ("test_export_owner_recovery",)


@pytest.fixture
def legacy_interrupted(interrupted):
    store, written = interrupted
    tray = store.snapshot().trays[0]
    store.update_tray(
        1, cleanup_sweeps=(), slides=tuple(dataclasses.replace(s, exports=()) for s in tray.slides)
    )
    return store, written


def test_legacy_recovery_preserves_older_raw_and_requires_fresh_exports(legacy_interrupted):
    store, written = legacy_interrupted
    older = store.sweep_dir(1, 0)
    older.mkdir()
    fragment = older / "rgb.npy"
    fragment.write_bytes(b"older incomplete raw")
    manifest = store.snapshot()
    assert can_recover_interrupted_cleanup(store, manifest, manifest.trays[0])
    with pytest.raises(export.ExportVerificationFailed) as refused:
        export.cleanup_raw(store, 1)
    assert refused.value.position == 1
    export.recover_interrupted_cleanup(store, 1)
    tray = store.snapshot().trays[0]
    assert not tray.raw_removed and tray.stage is TrayStage.REVIEW
    assert fragment.read_bytes() == b"older incomplete raw"
    assert store.sweep_dir(1, 1).is_dir()
    with pytest.raises(export.CleanupRefused):
        export.cleanup_raw(store, 1)
    assert len(export.export_batch(store, written[0].parent, "jpeg").written) == 4


@pytest.mark.parametrize(
    "damage", ["newer", "duplicate", "missing_rgb", "missing_meta", "missing_ir", "symlink"]
)
def test_legacy_recovery_refuses_unsafe_raw_and_keeps_marker(legacy_interrupted, damage):
    store, _ = legacy_interrupted
    current = store.sweep_dir(1, 1)
    if damage == "newer":
        store.sweep_dir(1, 2).mkdir()
    elif damage == "duplicate":
        shutil.copytree(current, store.tray_dir(1) / "sweep-01")
    elif damage == "symlink":
        (current / "linked").symlink_to(current / "rgb.npy")
    else:
        member = {"missing_rgb": "rgb.npy", "missing_meta": "meta.json", "missing_ir": "ir.npy"}
        (current / member[damage]).unlink()
    before = store.snapshot()
    assert not can_recover_interrupted_cleanup(store, before, before.trays[0])
    with pytest.raises(export.CleanupPolicyRefused, match="tray 1.*restore.*rescan"):
        export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot() == before
    assert current.is_dir()


@pytest.mark.parametrize("damage", ["positions", "hash"])
def test_legacy_recovery_checks_positions_and_available_hashes(legacy_interrupted, damage):
    store, _ = legacy_interrupted
    tray = store.snapshot().trays[0]
    slides = tray.slides[:-1] if damage == "positions" else tray.slides
    if damage == "hash":
        slides = (
            dataclasses.replace(
                slides[0], status=export.SlideStatus.BLANK, blank_fingerprint="a" * 64
            ),
            *slides[1:],
        )
    store.update_tray(1, slides=slides)
    before = store.snapshot()
    assert not can_recover_interrupted_cleanup(store, before, before.trays[0])
    with pytest.raises(export.CleanupPolicyRefused, match="positions|hash"):
        export.recover_interrupted_cleanup(store, 1)
    assert store.snapshot() == before


def test_legacy_cleanup_refusal_names_tray_and_recovery_steps(legacy_interrupted):
    store, _ = legacy_interrupted
    before = store.snapshot()
    with pytest.raises(export.BatchError) as refused:
        export.cleanup_raw(store, 1)
    message = str(refused.value)
    assert "tray 1" in message
    assert "Verify remaining scans…" in message
    assert "re-export" in message and "rescan" in message
    assert store.snapshot() == before
    assert store.sweep_dir(1, 1).is_dir()
