"""Delivery receipts survive provenance and heirloom export integration."""

import hashlib
import io
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from opticfilm_linux.batch import export
from opticfilm_linux.batch.manifest import from_json, to_json
from opticfilm_linux.batch.model import TrayLocation
from opticfilm_linux.provenance import XMP_HEADER
from test_export_safety import delivery  # noqa: F401


def _slide(store):
    return store.snapshot().trays[0].slides[0]


def _replace_slide(store, slide):
    tray = replace(store.snapshot().trays[0], slides=(slide,))
    store.update(lambda manifest: replace(manifest, trays=(tray,)))


def test_receipt_hashes_final_exif_xmp_bytes(delivery):  # noqa: F811
    store, directory = delivery
    (path,) = export.export_batch(store, directory, "jpeg").written
    record = _slide(store).exports[-1]
    data = path.read_bytes()
    assert XMP_HEADER in data and b"Exif\x00\x00" in data
    assert record.export_path == str(path.absolute())
    assert record.size_bytes == len(data)
    assert record.sha256 == hashlib.sha256(data).hexdigest()
    assert record.sha256 != record.raw_sha256
    with Image.open(path) as image:
        image.load()
        assert (record.width, record.height) == image.size
    assert from_json(to_json(store.snapshot())) == store.snapshot()
    export.verify_tray_export(store, 1)


def test_same_length_same_dimensions_replacement_fails_recorded_path(delivery):  # noqa: F811
    store, directory = delivery
    (path,) = export.export_batch(store, directory, "jpeg").written
    length = path.stat().st_size
    replacement = io.BytesIO()
    Image.new("RGB", (40, 32), "red").save(replacement, "JPEG")
    payload = replacement.getvalue()
    assert len(payload) < length
    path.write_bytes(payload + bytes(length - len(payload)))
    with Image.open(path) as image:
        image.load()
        assert image.size == (40, 32)
    assert path.stat().st_size == _slide(store).exports[-1].size_bytes
    with pytest.raises(export.ExportVerificationFailed, match="digest"):
        export.cleanup_raw(store, 1)
    assert store.sweep_dir(1, 1).exists()
    assert not store.snapshot().trays[0].raw_removed


def test_reexports_own_receipts_across_destinations(delivery):  # noqa: F811
    store, directory = delivery
    (first_path,) = export.export_batch(store, directory, "jpeg").written
    first = _slide(store).exports[-1]
    export.clear_exported(store)
    (second_path,) = export.export_batch(store, directory / "second", "jpeg").written
    records = _slide(store).exports
    assert records[0] == first
    assert [r.export_path for r in records] == [str(first_path), str(second_path)]
    for record in records:
        data = Path(record.export_path).read_bytes()
        assert record.sha256 == hashlib.sha256(data).hexdigest()
        assert record.size_bytes == len(data)
    first_path.unlink()
    export.verify_tray_export(store, 1)
    second_path.unlink()
    with pytest.raises(export.ExportVerificationFailed):
        export.verify_tray_export(store, 1)


def test_reordered_receipts_cannot_verify_an_old_delivery(delivery):  # noqa: F811
    store, directory = delivery
    export.export_batch(store, directory, "jpeg")
    export.clear_exported(store)
    export.export_batch(store, directory / "second", "jpeg")
    slide = _slide(store)
    _replace_slide(store, replace(slide, exports=tuple(reversed(slide.exports))))
    with pytest.raises(export.ExportVerificationFailed, match="current delivery"):
        export.cleanup_raw(store, 1)
    assert store.sweep_dir(1, 1).exists()


def test_verification_uses_recorded_path_not_reconstructed_filename(delivery):  # noqa: F811
    store, directory = delivery
    (path,) = export.export_batch(store, directory, "jpeg").written
    renamed = path.with_name("owner-chosen-name.jpg")
    path.rename(renamed)
    slide = _slide(store)
    receipt = replace(slide.exports[-1], path=renamed.name, export_path=str(renamed))
    _replace_slide(store, replace(slide, export_path=str(renamed), exports=(receipt,)))
    export.verify_tray_export(store, 1)


def test_incomplete_receipt_cannot_use_legacy_fallback(delivery):  # noqa: F811
    store, directory = delivery
    export.export_batch(store, directory, "jpeg")
    slide = _slide(store)
    incomplete = replace(slide.exports[-1], sha256="")
    _replace_slide(store, replace(slide, exports=(incomplete,)))
    with pytest.raises(export.ExportVerificationFailed, match="missing export record"):
        export.verify_tray_export(store, 1)


def test_verification_fully_decodes_even_when_digest_matches(delivery):  # noqa: F811
    store, directory = delivery
    (path,) = export.export_batch(store, directory, "jpeg").written
    data = path.read_bytes()[:-40]
    path.write_bytes(data)
    slide = _slide(store)
    digest = hashlib.sha256(data).hexdigest()
    receipt = replace(slide.exports[-1], sha256=digest, size_bytes=len(data))
    _replace_slide(store, replace(slide, export_sha256=digest, exports=(receipt,)))
    with pytest.raises(export.ExportVerificationFailed):
        export.verify_tray_export(store, 1)


def test_cleanup_finishes_committed_removal_if_keep_raw_changes(delivery, monkeypatch):  # noqa: F811
    store, directory = delivery
    export.export_batch(store, directory, "jpeg")
    original = store.update

    def keep_raw_after_commit(change):
        result = original(change)
        original(
            lambda manifest: replace(manifest, defaults=replace(manifest.defaults, keep_raw=True))
        )
        return result

    monkeypatch.setattr(store, "update", keep_raw_after_commit)
    export.cleanup_raw(store, 1)
    assert not store.sweep_dir(1, 1).exists()
    assert store.snapshot().trays[0].raw_removed


def test_numbered_reexport_cannot_verify_latest_with_old_receipt(delivery):  # noqa: F811
    store, directory = delivery
    tray = replace(store.snapshot().trays[0], location=TrayLocation("family", 1, "a", 1, 4))
    store.update(lambda manifest: replace(manifest, collection="family", trays=(tray,)))
    stride = store.snapshot().section_size
    (first_path,) = export.export_batch(store, directory, "jpeg").written
    first_bytes = first_path.read_bytes()
    first_receipt = _slide(store).exports[-1]
    export.clear_exported(store)
    (latest_path,) = export.export_batch(store, directory, "jpeg").written
    records = _slide(store).exports
    assert first_path.parent == latest_path.parent
    assert first_path.name == "001.jpg"
    assert latest_path.name == f"{1 + stride:03d}.jpg"
    assert len(records) == 2 and records[0] == first_receipt
    assert records[0].path != records[1].path
    for record, path in zip(records, (first_path, latest_path), strict=True):
        data = path.read_bytes()
        assert record.export_path == str(path)
        assert record.size_bytes == len(data)
        assert record.sha256 == hashlib.sha256(data).hexdigest()
    export.verify_tray_export(store, 1)
    damaged = bytearray(latest_path.read_bytes())
    damaged[-20] ^= 1
    latest_path.write_bytes(damaged)
    with pytest.raises(export.ExportVerificationFailed, match="digest"):
        export.verify_tray_export(store, 1)
    latest_path.unlink()
    with pytest.raises(export.ExportVerificationFailed):
        export.cleanup_raw(store, 1)
    assert first_path.read_bytes() == first_bytes
    assert _slide(store).exports[0] == first_receipt
    assert store.sweep_dir(1, 1).exists()
    assert not store.snapshot().trays[0].raw_removed
