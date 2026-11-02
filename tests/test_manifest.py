import json
from dataclasses import replace

import pytest

from opticfilm_linux.batch.manifest import ManifestFormatError, from_json, to_json
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    Flag,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)


def _manifest() -> BatchManifest:
    slides = (
        SlideRecord(position=1, status=SlideStatus.ACCEPTED),
        SlideRecord(
            position=2,
            status=SlideStatus.NEEDS_LOOK,
            flags=(Flag.TOO_DARK, Flag.DUST),
            settings=SlideSettings(rotation=90, brightness_ev=0.5, grain_strength=0.0),
            owner_decided=True,
            exported=True,
        ),
        SlideRecord(position=3, status=SlideStatus.BLANK),
    )
    trays = (
        TrayRecord(number=1, stage=TrayStage.REVIEW, slides=slides, sweep_version=2),
        TrayRecord(
            number=2, stage=TrayStage.FAILED, error="jam", raw_removed=True, mode="negative"
        ),
    )
    defaults = BatchDefaults(dpi=1200, infrared=False, grain_strength=0.1)
    return BatchManifest(
        name="Trips", album="Family", date="1975-06", defaults=defaults, trays=trays
    )


def test_manifest_roundtrip_with_trays_and_slides():
    manifest = _manifest()
    assert from_json(to_json(manifest)) == manifest


def test_json_uses_enum_values_not_names():
    text = to_json(_manifest())
    assert '"review"' in text and '"needs_look"' in text and "TrayStage" not in text


def test_unknown_manifest_version_raises():
    data = json.loads(to_json(_manifest()))
    data["version"] = 99
    with pytest.raises(ManifestFormatError, match="99"):
        from_json(json.dumps(data))


def test_garbage_json_raises_manifestformaterror():
    with pytest.raises(ManifestFormatError):
        from_json("{not json")


def test_missing_key_names_the_key():
    data = json.loads(to_json(_manifest()))
    del data["defaults"]
    with pytest.raises(ManifestFormatError, match="defaults"):
        from_json(json.dumps(data))


def test_wrong_type_raises_manifestformaterror():
    data = json.loads(to_json(_manifest()))
    data["trays"][0]["slides"][1]["settings"]["rotation"] = "ninety"
    with pytest.raises(ManifestFormatError):
        from_json(json.dumps(data))


def test_top_level_must_be_an_object():
    with pytest.raises(ManifestFormatError):
        from_json("[1, 2, 3]")


@pytest.mark.parametrize("fmt", ["tiff16", "png8", "jpeg"])
def test_export_defaults_roundtrip(fmt):
    manifest = _manifest()
    defaults = replace(
        manifest.defaults,
        export_dir="/photos/Family albums",
        export_format=fmt,
        jpeg_size="3600",
    )
    custom = replace(manifest, defaults=defaults)
    assert from_json(to_json(custom)) == custom
    assert (
        not {"export_dir", "export_format", "jpeg_size"}
        & json.loads(to_json(manifest))["defaults"].keys()
    )


@pytest.mark.parametrize("stored_dpi", [600, 3600, 7200])
def test_old_manifest_keeps_stored_dpi(stored_dpi):
    manifest = _manifest()
    custom = replace(manifest, defaults=replace(manifest.defaults, dpi=stored_dpi))
    data = json.loads(to_json(custom))
    for key in ("export_dir", "export_format", "jpeg_size"):
        data["defaults"].pop(key, None)
    loaded = from_json(json.dumps(data))
    assert loaded.defaults.dpi == stored_dpi
    assert (
        loaded.defaults.export_dir,
        loaded.defaults.export_format,
        loaded.defaults.jpeg_size,
    ) == ("", "jpeg", "full")
    assert from_json(to_json(loaded)) == loaded
    assert json.loads(to_json(loaded))["defaults"]["dpi"] == stored_dpi


@pytest.mark.parametrize(
    "key,value", [("export_dir", None), ("export_format", "gif"), ("jpeg_size", "half")]
)
def test_bad_manifest_export_defaults_rejected(key, value):
    data = json.loads(to_json(_manifest()))
    data["defaults"][key] = value
    with pytest.raises(ManifestFormatError, match=key):
        from_json(json.dumps(data))


def test_old_manifest_without_dpi_keeps_historical_3600():
    data = json.loads(to_json(_manifest()))
    del data["defaults"]["dpi"]
    loaded = from_json(json.dumps(data))
    assert loaded.defaults.dpi == 3600
    assert from_json(to_json(loaded)) == loaded
    assert json.loads(to_json(loaded))["defaults"]["dpi"] == 3600


def test_tray_scan_evidence_roundtrips_and_old_trays_remain_unknown():
    from opticfilm_linux.provenance import ScanEvidence

    evidence = ScanEvidence(
        "2026-11-01T12:00:00+00:00",
        12.0,
        "opticfilm-linux test",
        None,
        ('{"total_seconds":3}',) * 4,
    )
    original = _manifest()
    custom = replace(original, trays=(replace(original.trays[0], evidence=evidence),))
    assert from_json(to_json(custom)) == custom
    assert from_json(to_json(original)).trays[0].evidence is None
