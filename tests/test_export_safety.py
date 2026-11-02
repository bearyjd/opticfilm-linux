import dataclasses
import hashlib
import os
import stat
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from opticfilm_linux.batch import export
from opticfilm_linux.batch.manifest import from_json, to_json
from opticfilm_linux.batch.model import (
    EmptyConfirmation,
    SlideRecord,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.model import Box, Slide


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    with BatchStore.create(tmp_path / "batch", name="Safety") as store:
        store.update(
            lambda m: dataclasses.replace(
                m,
                trays=(
                    TrayRecord(
                        1,
                        TrayStage.REVIEW,
                        slides=(SlideRecord(1, status=SlideStatus.ACCEPTED),),
                    ),
                ),
            )
        )
        raw = store.sweep_dir(1, 1)
        raw.mkdir(parents=True)
        (raw / "scan").write_bytes(b"precious raw scan")
        monkeypatch.setattr(export, "load_sweep", lambda _: SimpleNamespace(dpi=600))
        rgb = np.full((32, 40, 3), 32000, dtype=np.uint16)
        monkeypatch.setattr(
            export, "split_slides", lambda _: [Slide(0, rgb, None, Box(0, 0, 40, 32))]
        )
        monkeypatch.setattr(
            export, "enhance_slide", lambda *_: np.full((32, 40, 3), 32000, dtype=np.uint16)
        )
        yield store, tmp_path / "album"


def _delivered(delivery):
    store, directory = delivery
    (path,) = export.export_batch(store, directory, "jpeg").written
    return store, path


def _assert_raw_kept(store):
    assert store.sweep_dir(1, 1).is_dir()
    assert not store.snapshot().trays[0].raw_removed


def _confirmed_blank(tray, slide, **changes):
    fingerprint = slide.exports[-1].raw_sha256
    return dataclasses.replace(
        slide,
        status=SlideStatus.BLANK,
        owner_decided=True,
        blank_fingerprint=fingerprint,
        empty_confirmed=EmptyConfirmation(tray.sweep_version, fingerprint),
        **changes,
    )


def test_cleanup_respects_keep_raw_after_manual_export(delivery):
    store, _ = _delivered(delivery)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, keep_raw=True))
    )
    with pytest.raises(export.CleanupRefused, match="keep"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_cleanup_requires_first_held_tray_release(delivery):
    store, _ = _delivered(delivery)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, auto_export=True))
    )
    store.update_tray(1, auto="held")
    with pytest.raises(export.CleanupRefused, match="first"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_manual_free_space_releases_first_held_tray_durably(delivery):
    store, _ = _delivered(delivery)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, auto_export=True))
    )
    store.update_tray(1, auto="held")
    export.free_space(store, only=(1,), release_first=True)
    assert store.snapshot().first_tray_released
    assert store.snapshot().trays[0].auto == "freed"
    assert store.snapshot().trays[0].raw_removed


def test_free_space_without_explicit_release_keeps_first_held(delivery):
    store, _ = _delivered(delivery)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, auto_export=True))
    )
    store.update_tray(1, auto="held")
    assert export.freeable_trays(store) == ()
    assert export.free_space(store, only=(1,)) == 0
    with pytest.raises(export.CleanupRefused, match="first"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


@pytest.mark.parametrize("number", [1, 2])
def test_disabling_auto_keeps_held_trays_until_first_release(delivery, number):
    store, _ = _delivered(delivery)
    first = dataclasses.replace(store.snapshot().trays[0], auto="held")
    second = dataclasses.replace(first, number=2)
    store.update(lambda m: dataclasses.replace(m, trays=(first, second)))
    raw = store.sweep_dir(2, 1)
    raw.mkdir(parents=True)
    (raw / "scan").write_bytes(b"second raw")
    assert not store.snapshot().defaults.auto_export
    with pytest.raises(export.CleanupRefused, match="first"):
        export.cleanup_raw(store, number)
    assert store.sweep_dir(number, 1).is_dir()
    assert not store.snapshot().trays[number - 1].raw_removed


def test_cleanup_refuses_missing_export(delivery):
    store, path = _delivered(delivery)
    path.unlink()
    with pytest.raises(BatchError, match="verification"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_cleanup_refuses_unrelated_same_size_valid_jpeg(delivery):
    store, path = _delivered(delivery)
    Image.new("RGB", (40, 32), "red").save(path, "JPEG")
    with pytest.raises(BatchError, match="verification"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


@pytest.mark.parametrize("replacement", ["missing", "different"])
def test_cleanup_rechecks_jpeg_after_committing(delivery, monkeypatch, replacement):
    store, path = _delivered(delivery)
    original = store.update

    def change_after_commit(change):
        manifest = original(change)
        if manifest.trays[0].raw_removed:
            if replacement == "missing":
                path.unlink()
            else:
                Image.new("RGB", (40, 32), "red").save(path, "JPEG")
        return manifest

    monkeypatch.setattr(store, "update", change_after_commit)
    with pytest.raises(export.ExportVerificationFailed):
        export.cleanup_raw(store, 1)
    assert store.sweep_dir(1, 1).is_dir()
    assert not store.snapshot().trays[0].raw_removed  # no deletion began


@pytest.mark.parametrize("ancestor", ["tray", "trays"])
def test_cleanup_rechecks_ancestor_symlink_after_committing(delivery, monkeypatch, ancestor):
    store, _ = _delivered(delivery)
    target = store.tray_dir(1) if ancestor == "tray" else store.directory / "trays"
    preserved = target.with_name(target.name + "-preserved")
    outside = store.directory.parent / "outside"
    relative_raw = store.sweep_dir(1, 1).relative_to(target)
    outside_raw = outside / relative_raw
    outside_raw.mkdir(parents=True)
    sentinel = outside_raw / "unrelated-raw"
    sentinel.write_bytes(b"outside scans must survive")
    original = store.update

    def swap_after_commit(change):
        manifest = original(change)
        if manifest.trays[0].raw_removed:
            target.rename(preserved)
            target.symlink_to(outside, target_is_directory=True)
        return manifest

    monkeypatch.setattr(store, "update", swap_after_commit)
    with pytest.raises(export.CleanupRefused, match="symlink|outside"):
        export.cleanup_raw(store, 1)
    assert (preserved / relative_raw / "scan").read_bytes() == b"precious raw scan"
    assert sentinel.read_bytes() == b"outside scans must survive"
    assert not store.snapshot().trays[0].raw_removed


def test_verify_rejects_unrelated_same_size_valid_jpeg(delivery):
    store, path = _delivered(delivery)
    Image.new("RGB", (40, 32), "red").save(path, "JPEG")
    with pytest.raises(export.ExportVerificationFailed, match="digest"):
        export.verify_tray_export(store, 1)


def test_digest_binds_writer_bytes_before_path_replacement(delivery, monkeypatch):
    store, directory = delivery
    original = export.write_slide

    def replace_after_write(*args, **kwargs):
        path = original(*args, **kwargs)
        Image.new("RGB", (40, 32), "red").save(path, "JPEG")
        return path

    monkeypatch.setattr(export, "write_slide", replace_after_write)
    export.export_batch(store, directory, "jpeg")
    with pytest.raises(export.ExportVerificationFailed, match="digest"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_export_records_digest_and_manifest_roundtrips_it(delivery):
    store, path = _delivered(delivery)
    record = store.snapshot().trays[0].slides[0]
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert getattr(record, "export_sha256", "") == expected
    assert from_json(to_json(store.snapshot())).trays[0].slides[0].export_sha256 == expected


@pytest.mark.parametrize("target", ["file", "directory"])
def test_cleanup_fsync_failure_keeps_raw(delivery, monkeypatch, target):
    store, path = _delivered(delivery)
    real_sync = os.fsync

    def sync(fd):
        kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        name = os.readlink(f"/proc/self/fd/{fd}")
        if kind == target and name in (str(path), str(path.parent)):
            raise OSError("delivery fsync failed")
        return real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(BatchError, match="delivery fsync failed"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


@pytest.mark.parametrize("target", ["file", "directory"])
def test_export_syncs_artifact_and_parent_before_marking(delivery, monkeypatch, target):
    store, directory = delivery
    observed = []
    real_sync = os.fsync

    def sync(fd):
        kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        name = os.readlink(f"/proc/self/fd/{fd}")
        if name == str(directory) or name.startswith(str(directory) + "/"):
            observed.append(kind)
            assert not store.snapshot().trays[0].slides[0].exported
        return real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    export.export_batch(store, directory, "jpeg")
    assert target in observed
    assert observed.index("file") < observed.index("directory")


@pytest.mark.parametrize("target", ["file", "directory"])
def test_export_fsync_failure_keeps_raw_and_unmarked(delivery, monkeypatch, target):
    store, directory = delivery
    real_sync = os.fsync

    def sync(fd):
        kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        name = os.readlink(f"/proc/self/fd/{fd}")
        if kind == target and (name == str(directory) or name.startswith(str(directory) + "/")):
            raise OSError("delivery fsync failed")
        return real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(OSError, match="delivery fsync failed"):
        export.export_batch(store, directory, "jpeg")
    assert not store.snapshot().trays[0].slides[0].exported
    _assert_raw_kept(store)


@pytest.mark.parametrize(
    ("relative", "parent"),
    [("album", "."), ("nested/album", "nested"), ("nested/deep/album", "nested/deep")],
)
def test_new_destination_parent_fsync_failure_keeps_raw(delivery, monkeypatch, relative, parent):
    store, directory = delivery
    destination = directory.parent / relative
    required_parent = (directory.parent / parent).resolve()
    real_sync = os.fsync

    def sync(fd):
        if os.readlink(f"/proc/self/fd/{fd}") == str(required_parent):
            raise OSError("destination parent fsync failed")
        return real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(OSError, match="destination parent fsync failed"):
        export.export_batch(store, destination, "jpeg")
    assert not store.snapshot().trays[0].slides[0].exported
    _assert_raw_kept(store)
