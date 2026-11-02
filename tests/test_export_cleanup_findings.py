import dataclasses
import shutil

import numpy as np
import pytest

from opticfilm_linux.batch import export
from opticfilm_linux.batch.model import SlideStatus
from test_export_safety import _assert_raw_kept, _delivered
from test_export_safety import delivery as delivery


def _automatic_release(store):
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, auto_export=True))
    )
    export.release_first_tray(store, 1)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("jpeg_size", "3600"),
        ("export_dir", "/different/album"),
        ("rotation", 90),
    ],
)
def test_release_is_bound_to_owner_inspected_settings(delivery, setting, value):
    store, _ = _delivered(delivery)
    _automatic_release(store)
    store.update(
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, **{setting: value})
        )
    )
    with pytest.raises(export.CleanupRefused, match="release.*settings"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_cleanup_refuses_export_moved_inside_raw(delivery):
    store, path = _delivered(delivery)
    destination = store.sweep_dir(1, 1) / path.name
    path.rename(destination)
    tray = store.snapshot().trays[0]
    slide = tray.slides[0]
    record = dataclasses.replace(slide.exports[-1], export_path=str(destination))
    store.update_tray(
        1, slides=(dataclasses.replace(slide, export_path=str(destination), exports=(record,)),)
    )
    with pytest.raises(export.CleanupRefused, match="inside the batch"):
        export.cleanup_raw(store, 1)
    assert destination.exists()
    _assert_raw_kept(store)


def test_cleanup_refuses_album_symlink_repointed_into_batch(delivery):
    store, path = _delivered(delivery)
    album = path.parent
    preserved = album.with_name("preserved")
    album.rename(preserved)
    target = store.sweep_dir(1, 1)
    shutil.copy(preserved / path.name, target / path.name)
    album.symlink_to(target, target_is_directory=True)
    with pytest.raises(export.CleanupRefused, match="inside the batch"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_cleanup_rechecks_album_location_after_commit(delivery, monkeypatch):
    store, path = _delivered(delivery)
    original = store.update

    def repoint(change):
        manifest = original(change)
        if manifest.trays[0].raw_removed:
            album = path.parent
            preserved = album.with_name("preserved")
            album.rename(preserved)
            shutil.copy(preserved / path.name, store.sweep_dir(1, 1) / path.name)
            album.symlink_to(store.sweep_dir(1, 1), target_is_directory=True)
        return manifest

    monkeypatch.setattr(store, "update", repoint)
    with pytest.raises(export.CleanupRefused, match="inside the batch"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_cleanup_requires_receipt_to_match_live_raw(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    original = export.split_slides

    def new_raw(sweep):
        slides = original(sweep)
        return [dataclasses.replace(slides[0], rgb=np.zeros_like(slides[0].rgb))]

    monkeypatch.setattr(export, "split_slides", new_raw)
    with pytest.raises(export.CleanupRefused, match="current raw scan"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


@pytest.mark.parametrize("timing", ["before", "after"])
def test_cleanup_never_picks_up_new_sweep_at_commit(delivery, monkeypatch, timing):
    store, _ = _delivered(delivery)
    original = store.update
    created = []

    def add_sweep(change):
        if not created and timing == "before":
            store.sweep_dir(1, 2).mkdir()
            created.append(True)
        manifest = original(change)
        if not created and timing == "after":
            store.sweep_dir(1, 2).mkdir()
            created.append(True)
        return manifest

    monkeypatch.setattr(store, "update", add_sweep)
    with pytest.raises(export.CleanupRefused, match="changed meanwhile"):
        export.cleanup_raw(store, 1)
    assert store.sweep_dir(1, 1).is_dir()
    assert store.sweep_dir(1, 2).is_dir()
    assert not store.snapshot().trays[0].raw_removed


def test_partial_current_sweep_removal_finishes_using_committed_verification(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    original = export.shutil.rmtree

    def partial(path):
        (path / "scan").unlink()
        raise OSError("power lost during deletion")

    monkeypatch.setattr(export.shutil, "rmtree", partial)
    with pytest.raises(export.BatchError, match="removal pending"):
        export.cleanup_raw(store, 1)
    assert store.snapshot().trays[0].raw_removed
    monkeypatch.setattr(export.shutil, "rmtree", original)
    monkeypatch.setattr(export, "load_sweep", lambda p: (p / "scan").read_bytes())
    export.cleanup_raw(store, 1)
    assert not store.sweep_dir(1, 1).exists()


def test_new_sweep_on_interrupted_cleanup_is_refused(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    original = export.shutil.rmtree
    monkeypatch.setattr(export.shutil, "rmtree", lambda _: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(export.BatchError):
        export.cleanup_raw(store, 1)
    monkeypatch.setattr(export.shutil, "rmtree", original)
    store.sweep_dir(1, 0).mkdir()
    with pytest.raises(export.CleanupRefused, match="unverified raw sweep"):
        export.cleanup_raw(store, 1)
    assert store.sweep_dir(1, 0).exists()


def test_legacy_slide_level_receipt_has_clear_reexport_message(delivery):
    store, path = _delivered(delivery)
    slide = store.snapshot().trays[0].slides[0]
    store.update_tray(1, slides=(dataclasses.replace(slide, exports=()),))
    with pytest.raises(export.ExportVerificationFailed, match="legacy.*re-export as JPEG"):
        export.cleanup_raw(store, 1)
    assert path.exists()
    _assert_raw_kept(store)


def test_relative_slide_level_receipt_is_rejected(delivery, monkeypatch):
    store, path = _delivered(delivery)
    slide = store.snapshot().trays[0].slides[0]
    monkeypatch.chdir(path.parent)
    store.update_tray(1, slides=(dataclasses.replace(slide, exports=(), export_path=path.name),))
    with pytest.raises(export.ExportVerificationFailed, match="receipt"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_tiff_cleanup_verifies_writer_receipt(delivery):
    store, directory = delivery
    (path,) = export.export_batch(store, directory, "tiff16").written
    export.cleanup_raw(store, 1)
    assert store.snapshot().trays[0].raw_removed
    assert not store.sweep_dir(1, 1).exists()
    assert path.exists()


@pytest.mark.parametrize("truncate", [False, True])
def test_tiff_cleanup_preserves_raw_when_artifact_changed(delivery, truncate):
    store, directory = delivery
    (path,) = export.export_batch(store, directory, "tiff16").written
    artifact = bytearray(path.read_bytes())
    if truncate:
        del artifact[-1:]
    else:
        artifact[-1] ^= 1
    path.write_bytes(artifact)
    with pytest.raises(export.ExportVerificationFailed, match="digest"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_jpeg_verification_rejects_tiff_receipt(delivery):
    store, directory = delivery
    export.export_batch(store, directory, "tiff16")
    with pytest.raises(export.ExportVerificationFailed, match="not a JPEG"):
        export.verify_tray_export(store, 1)
    _assert_raw_kept(store)


def test_auto_flagged_tray_requires_real_owner_decision(delivery):
    store, _ = _delivered(delivery)
    store.update_tray(1, auto_flagged=(1,))
    with pytest.raises(export.CleanupRefused, match="owner review"):
        export.cleanup_raw(store, 1)
    slide = store.snapshot().trays[0].slides[0]
    store.update_tray(
        1, slides=(dataclasses.replace(slide, status=SlideStatus.ACCEPTED, owner_decided=True),)
    )
    export.cleanup_raw(store, 1)
    assert store.snapshot().trays[0].raw_removed


def test_reduced_delivery_cannot_be_freed_by_reverting_to_released_full(delivery):
    store, directory = delivery
    export.export_batch(store, directory, "jpeg")
    _automatic_release(store)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, jpeg_size="3600"))
    )
    export.clear_exported(store)
    export.export_batch(store, directory, "jpeg", jpeg_size="3600")
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, jpeg_size="full"))
    )
    with pytest.raises(export.CleanupRefused, match="export settings differ"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_delivery_elsewhere_cannot_be_freed_by_reverting_destination(delivery):
    store, directory = delivery
    store.update(
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, export_dir=str(directory))
        )
    )
    export.export_batch(store, directory, "jpeg")
    _automatic_release(store)
    other = directory.with_name("other-album")
    store.update(
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, export_dir=str(other))
        )
    )
    export.clear_exported(store)
    export.export_batch(store, other, "jpeg")
    store.update(
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, export_dir=str(directory))
        )
    )
    with pytest.raises(export.CleanupRefused, match="export settings differ"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_automatic_cleanup_of_receipt_without_settings_requires_reexport(delivery):
    store, _ = _delivered(delivery)
    slide = store.snapshot().trays[0].slides[0]
    _automatic_release(store)
    receipt = dataclasses.replace(slide.exports[-1], render_signature="")
    store.update_tray(1, slides=(dataclasses.replace(slide, exports=(receipt,)),))
    with pytest.raises(export.CleanupRefused, match="settings receipt; re-export"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_manual_cleanup_accepts_full_receipt_without_settings_signature(delivery):
    store, _ = _delivered(delivery)
    slide = store.snapshot().trays[0].slides[0]
    receipt = dataclasses.replace(slide.exports[-1], render_signature="")
    store.update_tray(1, slides=(dataclasses.replace(slide, exports=(receipt,)),))
    export.cleanup_raw(store, 1)
    assert store.snapshot().trays[0].raw_removed


@pytest.mark.parametrize("fmt", ["tiff16", "png8"])
def test_auto_jpeg_receipt_matches_actual_format_when_manual_format_differs(delivery, fmt):
    store, directory = delivery
    store.update(
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, export_format=fmt)
        )
    )
    export.export_batch(store, directory, "jpeg")
    _automatic_release(store)
    export.cleanup_raw(store, 1)
    assert store.snapshot().trays[0].raw_removed
