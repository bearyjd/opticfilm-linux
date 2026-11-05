import dataclasses
import json

import pytest

from opticfilm_linux.batch.manifest import ManifestFormatError, from_json, to_json
from opticfilm_linux.batch.model import (
    BatchManifest,
    ExportRecord,
    SlideRecord,
    TrayRecord,
    TrayStage,
)


def _data():
    record = ExportRecord("slide.jpg", 40, 32, 600, 600, "T001S1")
    slide = SlideRecord(1, exports=(record,))
    tray = TrayRecord(1, TrayStage.REVIEW, slides=(slide,))
    return json.loads(to_json(BatchManifest(name="Safety", trays=(tray,))))


@pytest.mark.parametrize("field", ["width", "height", "scan_dpi", "delivered_dpi"])
@pytest.mark.parametrize("value", [True, "40", 1.5, 0, -1, None, [], {}])
def test_receipt_dimensions_and_resolution_are_positive_integers(field, value):
    data = _data()
    data["trays"][0]["slides"][0]["exports"][0][field] = value
    with pytest.raises(ManifestFormatError, match=field):
        from_json(json.dumps(data))


@pytest.mark.parametrize("field", ["path", "scan_token", "export_path"])
@pytest.mark.parametrize("value", [True, 3, None, [], {}])
def test_receipt_text_fields_reject_non_strings(field, value):
    data = _data()
    data["trays"][0]["slides"][0]["exports"][0][field] = value
    with pytest.raises(ManifestFormatError, match=field):
        from_json(json.dumps(data))


@pytest.mark.parametrize("field", ["raw_sha256", "sha256", "render_signature", "meta_signature"])
@pytest.mark.parametrize("value", [True, 3, None, [], {}, "short", "z" * 64])
def test_receipt_hashes_are_empty_or_sha256_hex(field, value):
    data = _data()
    data["trays"][0]["slides"][0]["exports"][0][field] = value
    with pytest.raises(ManifestFormatError, match=field):
        from_json(json.dumps(data))


@pytest.mark.parametrize("value", [True, "4", 1.5, -1, None, [], {}])
def test_receipt_byte_count_is_a_nonnegative_integer(value):
    data = _data()
    data["trays"][0]["slides"][0]["exports"][0]["size_bytes"] = value
    with pytest.raises(ManifestFormatError, match="size_bytes"):
        from_json(json.dumps(data))


@pytest.mark.parametrize(
    "field,value",
    [
        ("exports", {}),
        ("exports", "bad"),
        ("exports", [None]),
        ("export_path", None),
        ("export_sha256", 42),
        ("export_size", "40"),
        ("export_size", [True, 32]),
        ("export_size", [40]),
        ("export_size", [0, 32]),
    ],
)
def test_slide_delivery_fields_are_validated(field, value):
    data = _data()
    data["trays"][0]["slides"][0][field] = value
    with pytest.raises(ManifestFormatError, match=field):
        from_json(json.dumps(data))


def test_legacy_receipt_optional_fields_remain_readable():
    data = _data()
    receipt = data["trays"][0]["slides"][0]["exports"][0]
    for field in ("raw_sha256", "export_path", "size_bytes", "sha256", "render_signature"):
        del receipt[field]
    loaded = from_json(json.dumps(data))
    expected = ExportRecord("slide.jpg", 40, 32, 600, 600, "T001S1")
    assert dataclasses.asdict(loaded.trays[0].slides[0].exports[0]) == dataclasses.asdict(expected)


def test_receipt_without_meta_signature_reserialises_byte_identical():
    text = to_json(from_json(json.dumps(_data())))
    assert "meta_signature" not in text
    assert to_json(from_json(text)) == text


def test_receipt_meta_signature_round_trips():
    data = _data()
    data["trays"][0]["slides"][0]["exports"][0]["meta_signature"] = "a" * 64
    manifest = from_json(json.dumps(data))
    assert manifest.trays[0].slides[0].exports[0].meta_signature == "a" * 64
    assert from_json(to_json(manifest)) == manifest


def test_receipt_with_unknown_keys_loads_and_ignores_them():
    data = _data()
    data["trays"][0]["slides"][0]["exports"][0]["from_a_newer_build"] = "x"
    loaded = from_json(json.dumps(data))
    assert (
        loaded.trays[0].slides[0].exports[0]
        == from_json(json.dumps(_data())).trays[0].slides[0].exports[0]
    )
