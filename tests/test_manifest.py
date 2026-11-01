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


POLICY_FIELDS = ("auto_export", "keep_raw", "first_tray_released")
INVALID_POLICY_VALUES = ("false", "true", "", 0, 1, None, [], {})


@pytest.mark.parametrize("key", POLICY_FIELDS)
@pytest.mark.parametrize("value", INVALID_POLICY_VALUES)
def test_invalid_policy_value_loads_entire_safe_policy(key, value, caplog):
    data = json.loads(to_json(_manifest()))
    data["defaults"].update(auto_export=True, keep_raw=False)
    data["first_tray_released"] = True
    target = data if key == "first_tray_released" else data["defaults"]
    target[key] = value

    loaded = from_json(json.dumps(data))

    assert loaded.defaults.auto_export is False
    assert loaded.defaults.keep_raw is True
    assert loaded.first_tray_released is False
    assert any(key in record.message and record.levelname == "WARNING" for record in caplog.records)


@pytest.mark.parametrize("key", POLICY_FIELDS)
@pytest.mark.parametrize("value", INVALID_POLICY_VALUES)
def test_model_rejects_invalid_policy_boolean(key, value):
    with pytest.raises(ValueError, match=key):
        if key == "first_tray_released":
            BatchManifest(name="Trips", first_tray_released=value)
        else:
            BatchDefaults(**{key: value})


def test_legacy_manifest_without_policy_preserves_defaults(caplog):
    data = json.loads(to_json(_manifest()))
    for key in POLICY_FIELDS:
        target = data if key == "first_tray_released" else data["defaults"]
        target.pop(key, None)
    loaded = from_json(json.dumps(data))
    assert loaded.defaults.auto_export is False
    assert loaded.defaults.keep_raw is False
    assert loaded.first_tray_released is False
    assert not caplog.records


@pytest.mark.parametrize("auto_export", [False, True])
@pytest.mark.parametrize("keep_raw", [False, True])
@pytest.mark.parametrize("released", [False, True])
def test_boolean_policy_roundtrip(auto_export, keep_raw, released, caplog):
    manifest = replace(
        _manifest(),
        defaults=BatchDefaults(auto_export=auto_export, keep_raw=keep_raw),
        first_tray_released=released,
    )
    assert from_json(to_json(manifest)) == manifest
    assert not caplog.records
