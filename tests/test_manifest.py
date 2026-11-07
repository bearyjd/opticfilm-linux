import json
from dataclasses import replace

import pytest

from opticfilm_linux.batch.manifest import ManifestFormatError, from_json, to_json
from opticfilm_linux.batch.metadata import MetaOverride
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    ExportRecord,
    Flag,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayLocation,
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
def test_invalid_policy_value_is_rejected(key, value):
    data = json.loads(to_json(_manifest()))
    data["defaults"].update(auto_export=True, keep_raw=False)
    data["first_tray_released"] = True
    target = data if key == "first_tray_released" else data["defaults"]
    target[key] = value

    with pytest.raises(ManifestFormatError, match=key):
        from_json(json.dumps(data))


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


def test_collection_and_pipeline_receipts_roundtrip_together():
    receipt = ExportRecord(
        path="family/box-2/b/005.jpg",
        width=100,
        height=80,
        scan_dpi=7200,
        delivered_dpi=3600,
        scan_token="T001S1",
        raw_sha256="a" * 64,
        export_path="/photos/family/box-2/b/005.jpg",
        size_bytes=1234,
        sha256="b" * 64,
    )
    slide = SlideRecord(
        position=1,
        exported=True,
        export_path=receipt.export_path,
        export_size=(100, 80),
        export_sha256=receipt.sha256,
        exports=(receipt,),
    )
    tray = TrayRecord(
        number=1,
        stage=TrayStage.EXPORTED,
        slides=(slide,),
        auto="held",
        auto_error="check slide",
        auto_failed=(2,),
        auto_flagged=(3,),
        location=TrayLocation("family", 2, "b", 5, 4),
    )
    manifest = BatchManifest(
        name="Family",
        collection="family",
        box=2,
        section="b",
        section_size=100,
        defaults=BatchDefaults(auto_export=True, keep_raw=True),
        first_tray_released=True,
        trays=(tray,),
    )
    assert from_json(to_json(manifest)) == manifest

    data = json.loads(to_json(manifest))
    data["defaults"]["keep_raw"] = "false"
    with pytest.raises(ManifestFormatError, match="keep_raw"):
        from_json(json.dumps(data))


@pytest.mark.parametrize("value", [None, 42, [], {}])
def test_invalid_auto_error_type_rejected(value):
    data = json.loads(to_json(_manifest()))
    data["trays"][0]["auto_error"] = value
    with pytest.raises(ManifestFormatError, match="auto_error"):
        from_json(json.dumps(data))


def _plain_json() -> dict:
    return json.loads(to_json(_manifest()))


def test_unset_metadata_leaves_no_keys_and_bytes_are_identical():
    text = to_json(_manifest())
    assert "description" not in text and '"meta"' not in text
    assert to_json(from_json(text)) == text


def test_metadata_round_trips():
    manifest = _manifest()
    tray = replace(
        manifest.trays[0],
        meta=MetaOverride(description="Lake", date="1978"),
        slides=(
            replace(manifest.trays[0].slides[0], meta=MetaOverride(description="", date="1979-07")),
        )
        + manifest.trays[0].slides[1:],
    )
    manifest = replace(manifest, description="Trip", trays=(tray,) + manifest.trays[1:])
    back = from_json(to_json(manifest))
    assert back == manifest
    assert back.trays[0].slides[0].meta == MetaOverride(description="", date="1979-07")
    assert to_json(back) == to_json(manifest)


def test_partial_override_omits_unset_member():
    manifest = _manifest()
    tray = replace(manifest.trays[0], meta=MetaOverride(date="1978"))
    data = json.loads(to_json(replace(manifest, trays=(tray,))))
    assert data["trays"][0]["meta"] == {"date": "1978"}


@pytest.mark.parametrize("date", ["1978", "1978-07", "1978-07-14", "summer 78", None])
def test_legacy_date_shapes_load_and_round_trip_unchanged(date):
    data = _plain_json()
    data["date"] = date
    text = json.dumps(data, indent=2)
    manifest = from_json(text)
    assert manifest.date == date
    assert to_json(manifest) == text


@pytest.mark.parametrize(
    ("where", "key", "value"),
    [
        ("tray", "meta", "oops"),
        ("slide", "meta", ["x"]),
    ],
)
def test_bad_metadata_types_raise_manifest_format_error(where, key, value):
    data = _plain_json()
    target = {"top": data, "tray": data["trays"][0], "slide": data["trays"][0]["slides"][0]}[where]
    target[key] = value
    with pytest.raises(ManifestFormatError):
        from_json(json.dumps(data))


def test_load_is_lenient_about_description_and_date_values():
    data = _plain_json()
    data["description"] = "x" * 600
    data["date"] = 1978
    data["trays"][0]["meta"] = {"description": 5, "date": ["x"]}
    data["trays"][0]["slides"][0]["meta"] = {"description": "y" * 501}
    manifest = from_json(json.dumps(data))
    assert manifest.description == "x" * 600
    assert manifest.trays[0].slides[0].meta.description == "y" * 501
    assert manifest.date == 1978


def test_unknown_meta_keys_are_ignored_and_dropped():
    data = _plain_json()
    data["trays"][0]["meta"] = {"date": "1978", "future": 1}
    manifest = from_json(json.dumps(data))
    assert manifest.trays[0].meta == MetaOverride(date="1978")
    assert json.loads(to_json(manifest))["trays"][0]["meta"] == {"date": "1978"}


# A tray written before skipped openings existed: no "skipped" key anywhere.
OLD_TRAY_JSON = """{
  "name": "Old",
  "album": "",
  "date": null,
  "defaults": {
    "dpi": 7200,
    "infrared": true,
    "mode": "positive",
    "skip_blank": true,
    "remove_dust": true,
    "color_strength": 1.0,
    "grain_strength": 0.3,
    "sharpen_amount": 0.5,
    "rotation": 0,
    "mirror": false
  },
  "trays": [
    {
      "number": 1,
      "stage": "review",
      "mode": "positive",
      "sweep_version": 1,
      "slides": [
        {
          "position": 1,
          "status": "accepted",
          "flags": [],
          "settings": {
            "rotation": 0,
            "brightness_ev": 0.0,
            "color_strength": null,
            "grain_strength": null,
            "sharpen_amount": null,
            "mirror": false
          },
          "owner_decided": false,
          "exported": false,
          "preview_stale": false
        }
      ],
      "error": null,
      "raw_removed": false
    }
  ],
  "version": 1
}"""


def test_a_manifest_without_skipped_loads_and_reserialises_byte_identical():
    manifest = from_json(OLD_TRAY_JSON)
    assert manifest.trays[0].skipped == ()
    assert to_json(manifest) == OLD_TRAY_JSON


def test_skipped_openings_round_trip_and_stay_version_1():
    tray = TrayRecord(
        number=1,
        stage=TrayStage.REVIEW,
        slides=(
            SlideRecord(position=1, status=SlideStatus.ACCEPTED),
            SlideRecord(position=4, status=SlideStatus.SKIPPED),
        ),
        skipped=(4,),
    )
    manifest = BatchManifest(name="T", trays=(tray,))
    data = json.loads(to_json(manifest))
    assert data["version"] == 1
    assert data["trays"][0]["skipped"] == [4]
    assert data["trays"][0]["slides"][1]["status"] == "skipped"
    assert from_json(to_json(manifest)) == manifest


@pytest.mark.parametrize("value", [[1, 2, 3, 4], [0], [5], [3, 1], [2, 2], [True], "4", [4.0]])
def test_bad_skipped_openings_are_refused(value):
    data = json.loads(OLD_TRAY_JSON)
    data["trays"][0]["skipped"] = value
    with pytest.raises(ManifestFormatError, match="skipped"):
        from_json(json.dumps(data))


def test_a_skipped_slide_outside_the_skipped_openings_is_refused():
    data = json.loads(OLD_TRAY_JSON)
    data["trays"][0]["slides"][0]["status"] = "skipped"
    with pytest.raises(ManifestFormatError, match="skipped"):
        from_json(json.dumps(data))


@pytest.mark.parametrize("stage", list(TrayStage))
def test_a_skipped_opening_always_has_a_skipped_slide_record(stage):
    with pytest.raises(ValueError, match="skipped"):
        TrayRecord(number=1, stage=stage, skipped=(4,))
    data = json.loads(OLD_TRAY_JSON)
    data["trays"][0].update(stage=stage.value, skipped=[4])
    with pytest.raises(ManifestFormatError, match="skipped"):
        from_json(json.dumps(data))
