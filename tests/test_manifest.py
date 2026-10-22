import json

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
