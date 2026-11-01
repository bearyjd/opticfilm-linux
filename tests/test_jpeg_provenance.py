import hashlib
import json
from dataclasses import replace
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from PIL import Image

from opticfilm_linux.batch.model import (
    BatchManifest,
    Flag,
    SlideRecord,
    SlideStatus,
    TrayLocation,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.model import Box, Slide
from opticfilm_linux.output import OutputSpec, write_slide
from opticfilm_linux.pipeline.run import ProcessOptions, enhance_slide

NS = "https://opticfilm-linux.org/ns/scan/1.0/"
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\0"


def _inputs(date=None, evidence=True):
    from opticfilm_linux.provenance import ScanEvidence

    rgb = np.arange(9 * 13 * 3, dtype=np.uint16).reshape(9, 13, 3) * 100
    slide = Slide(0, rgb, rgb[:, :, 0], Box(0, 0, 13, 9))
    record = SlideRecord(1, SlideStatus.ACCEPTED, flags=(Flag.DUST, Flag.TOO_DARK))
    captured = (
        ScanEvidence(
            digitized_at="2026-11-01T12:00:00+00:00",
            duration_s=123.5,
            software="opticfilm-linux 0.1<&é",
            driver_revision="abc123",
            diagnostics=(
                json.dumps(
                    {
                        "frame": 1,
                        "cal_integrity": "clean",
                        "gain_codes": [1, 2, 3],
                        "offset_codes": [4, 5, 6],
                        "total_seconds": 30.25,
                    }
                ),
                "{}",
                "{}",
                "{}",
            ),
        )
        if evidence
        else None
    )
    tray = TrayRecord(
        12,
        TrayStage.REVIEW,
        mode="negative",
        slides=(record,),
        evidence=captured,
        location=TrayLocation("Family & <é>", 2, "a", 1, 4),
    )
    manifest = BatchManifest("Trip & <snow> é", date=date, trays=(tray,))
    options = ProcessOptions(mode="negative", brightness_ev=0.5, rotation=90, mirror=True)
    return manifest, tray, record, slide, options


def _write(tmp_path, inputs, size="full"):
    from opticfilm_linux.provenance import jpeg_metadata

    manifest, tray, record, slide, options = inputs
    delivered = 3600 if size == "3600" else 7200
    metadata = jpeg_metadata(manifest, tray, record, slide, options, 7200, delivered)
    path = write_slide(
        enhance_slide(slide, options, 7200),
        OutputSpec(tmp_path, "jpeg", scan_dpi=7200, jpeg_size=size, metadata=metadata),
    )
    return path


def _fields(path):
    with Image.open(path) as image:
        packets = [
            payload
            for marker, payload in image.applist
            if marker == "APP1" and payload.startswith(XMP_HEADER)
        ]
    assert len(packets) == 1
    assert path.read_bytes()[2:4] == b"\xff\xe1"
    root = ET.fromstring(packets[0][len(XMP_HEADER) :])
    return {
        node.tag.removeprefix("{" + NS + "}"): node.text
        for node in root.iter()
        if node.tag.startswith("{" + NS + "}")
    }


def _expected_fields(size, raw_hash):
    from opticfilm_linux.provenance import TONE_VERSION

    return {
        "Version": "1",
        "ScannerMake": "Plustek",
        "ScannerModel": "OpticFilm 135i",
        "Software": "opticfilm-linux 0.1<&é",
        "DriverRevision": "abc123",
        "DigitizedAt": "2026-11-01T12:00:00+00:00",
        "ScanDPI": "7200",
        "DeliveredDPI": "3600" if size == "3600" else "7200",
        "FilmMode": "negative",
        "Holder": "slide4",
        "Batch": "Trip & <snow> é",
        "Collection": "Family & <é>",
        "Box": "2",
        "Section": "a",
        "Tray": "12",
        "Slot": "1",
        "ScanToken": "T012S1",
        "ToneVersion": TONE_VERSION,
        "CalibrationIntegrity": "clean",
        "GainCodes": "[1,2,3]",
        "OffsetCodes": "[4,5,6]",
        "ScanDurationSeconds": "30.25",
        "TrayScanDurationSeconds": "123.5",
        "RawSHA256": raw_hash,
    }


@pytest.mark.parametrize("size", ["full", "3600"])
def test_embedded_provenance_complete_and_escaped(tmp_path, size):
    from opticfilm_linux.provenance import raw_region_sha256

    inputs = _inputs()
    _, _, _, slide, options = inputs
    path = _write(tmp_path, inputs, size)
    fields = _fields(path)
    expected = _expected_fields(size, raw_region_sha256(slide))
    assert {key: fields[key] for key in expected} == expected
    assert json.loads(fields["ReviewFlags"]) == ["dust", "too_dark", "needs_look"]
    assert json.loads(fields["Settings"]) == {
        "brightness_ev": options.brightness_ev,
        "color_strength": options.color_strength,
        "grain_strength": options.grain_strength,
        "sharpen_amount": options.sharpen_amount,
        "remove_dust": options.remove_dust,
        "rotation": options.rotation,
        "mirror": options.mirror,
    }
    with Image.open(path) as image:
        exif = image.getexif()
        assert exif[271] == "Plustek" and exif[272] == "OpticFilm 135i"
        assert exif[274] == 1 and exif[296] == 2
        assert exif[282] == exif[283] == int(expected["DeliveredDPI"])
        assert exif.get_ifd(34665)[36868] == "2026:11:01 12:00:00"
        assert exif.get_ifd(34665)[36882] == "+00:00"
        assert image.info["icc_profile"]


@pytest.mark.parametrize(
    "date,original,precision",
    [
        (None, None, None),
        ("1975", None, "year"),
        ("1975-06", None, "month"),
        ("1975-06-01", "1975:06:01 00:00:00", "day"),
    ],
)
def test_heirloom_fields_empty_unless_owner_date(tmp_path, date, original, precision):
    path = _write(tmp_path, _inputs(date))
    fields = _fields(path)
    with Image.open(path) as image:
        exif = image.getexif()
        assert exif.get_ifd(34665).get(36867) == original
        assert 270 not in exif and 306 not in exif and 34853 not in exif
        assert 36881 not in exif.get_ifd(34665)
    if date:
        assert fields["PhotoDate"] == date
        assert fields["PhotoDatePrecision"] == precision
        assert fields["PhotoDateSource"] == "owner-entered"
    else:
        assert "PhotoDateSource" not in fields and "PhotoDate" not in fields


@pytest.mark.parametrize("diagnostic", [None, "{}", '{"gain_codes":[1,2,3]}', "broken"])
def test_missing_partial_diagnostics_are_explicit_unknown(tmp_path, diagnostic):
    inputs = list(_inputs(evidence=diagnostic is not None))
    if diagnostic is not None:
        inputs[1] = replace(
            inputs[1], evidence=replace(inputs[1].evidence, diagnostics=(diagnostic,))
        )
    fields = _fields(_write(tmp_path, inputs))
    assert fields["CalibrationIntegrity"] == fields["OffsetCodes"] == "unknown"
    assert fields["ScanDurationSeconds"] == "unknown"
    assert fields["GainCodes"] == (
        "[1,2,3]" if diagnostic and "gain_codes" in diagnostic else "unknown"
    )
    if diagnostic is None:
        assert fields["DigitizedAt"] == fields["DriverRevision"] == "unknown"
        with Image.open(next(tmp_path.glob("*.jpg"))) as image:
            assert 36868 not in image.getexif().get_ifd(34665)


def test_null_diagnostic_fields_are_unknown(tmp_path):
    inputs = list(_inputs())
    inputs[1] = replace(
        inputs[1],
        evidence=replace(
            inputs[1].evidence,
            diagnostics=(
                '{"cal_integrity":null,"gain_codes":null,"offset_codes":null,"total_seconds":null}',
            ),
        ),
    )
    fields = _fields(_write(tmp_path, inputs))
    assert all(
        fields[key] == "unknown"
        for key in (
            "CalibrationIntegrity",
            "GainCodes",
            "OffsetCodes",
            "ScanDurationSeconds",
        )
    )


def test_raw_hash_is_region_specific_and_stable():
    from opticfilm_linux.provenance import raw_region_sha256

    _, _, _, slide, _ = _inputs()
    original = raw_region_sha256(slide)
    assert len(original) == 64
    assert original == raw_region_sha256(
        replace(slide, rgb=slide.rgb.astype(">u2"), ir=slide.ir.astype(">u2"))
    )
    assert original == raw_region_sha256(replace(slide, rgb=slide.rgb.copy(), ir=slide.ir.copy()))
    for channel in range(3):
        rgb = slide.rgb.copy()
        rgb[0, 0, channel] += 1
        assert original != raw_region_sha256(replace(slide, rgb=rgb))
    ir = slide.ir.copy()
    ir[0, 0] += 1
    assert original != raw_region_sha256(replace(slide, ir=ir))
    assert original != raw_region_sha256(replace(slide, ir=None))
    surrounding = np.zeros((11, 15, 3), np.uint16)
    surrounding[1:10, 1:14] = slide.rgb
    view = replace(slide, rgb=surrounding[1:10, 1:14])
    assert raw_region_sha256(view) == original
    surrounding[0, 0] = 42
    assert raw_region_sha256(view) == original


def test_raw_hash_memory_is_bounded_by_row():
    import tracemalloc

    from opticfilm_linux.provenance import raw_region_sha256

    rgb = np.zeros((1000, 1000, 3), dtype=">u2")
    slide = Slide(0, rgb, None, Box(0, 0, 1000, 1000))
    tracemalloc.start()
    try:
        raw_region_sha256(slide)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000  # A full RGB conversion alone would allocate six MB.


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_baked_rotation_orientation_one(tmp_path, rotation):
    inputs = list(_inputs())
    inputs[4] = replace(inputs[4], rotation=rotation)
    path = _write(tmp_path, inputs)
    with Image.open(path) as image:
        assert image.getexif()[274] == 1
        assert image.size == ((9, 13) if rotation in (90, 270) else (13, 9))


def test_export_hash_roundtrips_manifest():
    from opticfilm_linux.batch.manifest import from_json, to_json
    from opticfilm_linux.batch.model import ExportRecord

    manifest, tray, record, _, _ = _inputs()
    exported = ExportRecord("box-2/a/001.jpg", 13, 9, 7200, 7200, "T012S1", raw_sha256="a" * 64)
    record = replace(record, exports=(exported,))
    manifest = replace(manifest, trays=(replace(tray, slides=(record,)),))
    assert from_json(to_json(manifest)).trays[0].slides[0].exports[0].raw_sha256 == "a" * 64


@pytest.mark.parametrize("size,expected", [("full", (9, 13, 7200)), ("3600", (5, 7, 3600))])
def test_export_records_match_jpeg_and_hash_survives_reexport(
    tmp_path, monkeypatch, size, expected
):
    from types import SimpleNamespace

    from opticfilm_linux.batch.export import clear_exported, export_batch
    from opticfilm_linux.batch.store import BatchStore
    from opticfilm_linux.provenance import raw_region_sha256

    manifest, tray, record, slide, _ = _inputs()
    monkeypatch.setattr(
        "opticfilm_linux.batch.export.load_sweep", lambda _: SimpleNamespace(dpi=7200)
    )
    monkeypatch.setattr("opticfilm_linux.batch.export.split_slides", lambda _: [slide])
    with BatchStore.create(tmp_path / "batch", manifest.name) as store:
        record = replace(record, settings=replace(record.settings, rotation=90))
        store.update(lambda _: replace(manifest, trays=(replace(tray, slides=(record,)),)))
        first = export_batch(store, tmp_path / "out", "jpeg", jpeg_size=size).written[0]
        clear_exported(store)
        second = export_batch(store, tmp_path / "out", "jpeg", jpeg_size=size).written[0]
        exports = store.snapshot().trays[0].slides[0].exports
        assert [export.path for export in exports] == [
            str(first.relative_to(tmp_path / "out")),
            str(second.relative_to(tmp_path / "out")),
        ]
        assert exports[0].raw_sha256 == exports[1].raw_sha256 == raw_region_sha256(slide)
        for path, exported in zip((first, second), exports, strict=True):
            with Image.open(path) as image:
                assert image.size == (exported.width, exported.height) == expected[:2]
                assert exported.delivered_dpi == image.getexif()[282] == expected[2]
            assert _fields(path)["RawSHA256"] == exported.raw_sha256
            assert _fields(path)["ScanToken"] == exported.scan_token == "T012S1"


@pytest.mark.parametrize(
    "status,flags,expected",
    [
        (SlideStatus.BLANK, (), ["blank"]),
        (SlideStatus.NEEDS_LOOK, (), ["needs_look"]),
        (SlideStatus.ACCEPTED, (), []),
        (SlideStatus.ACCEPTED, (Flag.DUST,), ["dust", "needs_look"]),
    ],
)
def test_review_flags_preserve_evidence_after_acceptance(tmp_path, status, flags, expected):
    inputs = list(_inputs())
    inputs[2] = replace(inputs[2], status=status, flags=flags)
    assert json.loads(_fields(_write(tmp_path, inputs))["ReviewFlags"]) == expected


@pytest.mark.parametrize("fmt", ["jpeg", "tiff16", "png8"])
def test_export_hash_is_captured_before_render(tmp_path, monkeypatch, fmt):
    from types import SimpleNamespace

    from opticfilm_linux.batch.export import export_batch
    from opticfilm_linux.batch.store import BatchStore
    from opticfilm_linux.provenance import raw_region_sha256

    manifest, tray, _, slide, _ = _inputs()
    original = raw_region_sha256(slide)
    monkeypatch.setattr(
        "opticfilm_linux.batch.export.load_sweep", lambda _: SimpleNamespace(dpi=7200)
    )
    monkeypatch.setattr("opticfilm_linux.batch.export.split_slides", lambda _: [slide])

    def render(raw, options, dpi):
        raw.rgb[0, 0, 0] += 1
        return raw.rgb

    monkeypatch.setattr("opticfilm_linux.batch.export.enhance_slide", render)
    with BatchStore.create(tmp_path / "batch", manifest.name) as store:
        store.update(lambda _: replace(manifest, trays=(tray,)))
        path = export_batch(store, tmp_path / "out", fmt).written[0]
        if fmt == "jpeg":
            assert _fields(path)["RawSHA256"] == original
        assert store.snapshot().trays[0].slides[0].exports[0].raw_sha256 == original


def test_oversize_xmp_removes_exclusively_created_partial_file(tmp_path):
    from opticfilm_linux.provenance import JpegMetadata

    metadata = JpegMetadata(b"", b"x" * 65534, hashlib.sha256(b"raw").hexdigest())
    with pytest.raises(ValueError, match="XMP|APP1"):
        write_slide(np.zeros((2, 3, 3), np.uint16), OutputSpec(tmp_path, "jpeg", metadata=metadata))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failed_encode", [False, True])
def test_directory_swap_during_encode_cannot_publish_wrong_path(
    tmp_path, monkeypatch, failed_encode
):
    import opticfilm_linux.output as output

    section, moved, outside = tmp_path / "a", tmp_path / "old-a", tmp_path / "outside"
    section.mkdir()
    outside.mkdir()
    sentinel = outside / "001.jpg"
    sentinel.write_bytes(b"keep existing outside output")
    original_encode = output._encode

    def swapped(*args, **kwargs):
        section.rename(moved)
        section.symlink_to(outside, target_is_directory=True)
        if failed_encode:
            raise ValueError("encoder failed")
        original_encode(*args, **kwargs)

    monkeypatch.setattr(output, "_encode", swapped)
    with pytest.raises((OSError, ValueError)):
        write_slide(np.zeros((2, 3, 3), np.uint16), OutputSpec(section, "jpeg", position=1))
    assert sentinel.read_bytes() == b"keep existing outside output"
    assert list(outside.iterdir()) == [sentinel]
    assert list(moved.iterdir()) == []


def test_directory_swap_at_exclusive_create_stays_anchored(tmp_path, monkeypatch):
    import os

    import opticfilm_linux.output as output

    section, moved, outside = tmp_path / "a", tmp_path / "old-a", tmp_path / "outside"
    section.mkdir()
    outside.mkdir()
    original_open = output.os.open

    def swapped(path, flags, *args, **kwargs):
        if flags & os.O_CREAT:
            section.rename(moved)
            section.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(output.os, "open", swapped)
    with pytest.raises((OSError, ValueError)):
        write_slide(np.zeros((2, 3, 3), np.uint16), OutputSpec(section, "jpeg", position=1))
    assert list(outside.iterdir()) == []
    assert list(moved.iterdir()) == []


def test_encode_failure_does_not_unlink_replacement_file(tmp_path, monkeypatch):
    import opticfilm_linux.output as output

    original, renamed = tmp_path / "001.jpg", tmp_path / "moved.jpg"

    def replaced(*args, **kwargs):
        original.rename(renamed)
        original.write_bytes(b"competitor")
        raise ValueError("encoder failed")

    monkeypatch.setattr(output, "_encode", replaced)
    with pytest.raises(ValueError, match="encoder"):
        write_slide(np.zeros((2, 3, 3), np.uint16), OutputSpec(tmp_path, "jpeg", position=1))
    assert original.read_bytes() == b"competitor"


@pytest.mark.parametrize("name", ["Family\x01Album", "Family\x00\x0b\x1f\ud800\ufffe\uffffAlbum"])
def test_invalid_xml_characters_are_stripped_from_owner_names(tmp_path, name):
    inputs = list(_inputs())
    inputs[0] = replace(inputs[0], name=name, album=name)
    collection = name.replace("\0", "")  # Folder validation already refuses NUL.
    inputs[1] = replace(inputs[1], location=replace(inputs[1].location, collection=collection))
    fields = _fields(_write(tmp_path, inputs))
    assert fields["Batch"] == fields["Collection"] == "FamilyAlbum"
    assert inputs[0].name == inputs[0].album == name
    assert inputs[1].location.collection == collection


def test_valid_xml_whitespace_and_unicode_survive_owner_names(tmp_path):
    inputs = list(_inputs())
    name = "Family\t\n\rAlbum é & <雪> 😀"
    inputs[0] = replace(inputs[0], name=name, album=name)
    inputs[1] = replace(inputs[1], location=replace(inputs[1].location, collection=name))
    fields = _fields(_write(tmp_path, inputs))
    assert fields["Batch"] == fields["Collection"] == name.replace("\r", "\n")
