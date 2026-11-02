import json
from dataclasses import fields, replace

import pytest

from opticfilm_linux.batch.manifest import ManifestFormatError, from_json, to_json
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    SlideRecord,
    SlideSettings,
    SlotOrientation,
    TrayRecord,
    TrayStage,
)


def _manifest():
    return BatchManifest(
        name="Safety", trays=(TrayRecord(1, TrayStage.REVIEW, slides=(SlideRecord(1),)),)
    )


@pytest.mark.parametrize("state", ["unknown", None, 3, [], {}])
def test_invalid_automatic_state_is_rejected(state):
    data = json.loads(to_json(_manifest()))
    data["trays"][0]["auto"] = state
    with pytest.raises(ManifestFormatError, match="automatic export state"):
        from_json(json.dumps(data))


@pytest.mark.parametrize("key", ["auto_failed", "auto_flagged", "cleanup_sweeps"])
@pytest.mark.parametrize("value", ["12", [True], [1.2], [-1], ["1"], None, [1, 1]])
def test_automatic_position_lists_are_validated(key, value):
    data = json.loads(to_json(_manifest()))
    data["trays"][0][key] = value
    with pytest.raises(ManifestFormatError, match=key):
        from_json(json.dumps(data))


@pytest.mark.parametrize("key", ["auto_failed", "auto_flagged"])
def test_automatic_slide_positions_reject_zero(key):
    data = json.loads(to_json(_manifest()))
    data["trays"][0][key] = [0]
    with pytest.raises(ManifestFormatError, match=key):
        from_json(json.dumps(data))


def test_zero_cleanup_sweep_roundtrips():
    manifest = _manifest()
    tray = replace(manifest.trays[0], cleanup_sweeps=(0, 1, 2))
    manifest = replace(manifest, trays=(tray,))
    assert from_json(to_json(manifest)) == manifest


BOOLEAN_RECORD_PATHS = (
    (BatchManifest, ()),
    (BatchDefaults, ("defaults",)),
    (BatchDefaults, ("released_render",)),
    (SlotOrientation, ("defaults", "slots", 0)),
    (SlotOrientation, ("released_render", "slots", 0)),
    (TrayRecord, ("trays", 0)),
    (SlideRecord, ("trays", 0, "slides", 0)),
    (SlideSettings, ("trays", 0, "slides", 0, "settings")),
)
BOOLEAN_PATHS = [
    (*path, field.name)
    for record, path in BOOLEAN_RECORD_PATHS
    for field in fields(record)
    if field.type in (bool, "bool")
]


def _boolean_manifest():
    defaults = BatchDefaults(slots=(SlotOrientation(1, rotation=90),))
    return replace(_manifest(), defaults=defaults, released_render=defaults)


@pytest.mark.parametrize("path", BOOLEAN_PATHS, ids=lambda path: ".".join(map(str, path)))
@pytest.mark.parametrize("value", ["false", "true", "", 0, 1, None, [], {}])
def test_every_manifest_boolean_rejects_non_boolean_values(path, value):
    data = json.loads(to_json(_boolean_manifest()))
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ManifestFormatError, match=path[-1]):
        from_json(json.dumps(data))


@pytest.mark.parametrize("path", BOOLEAN_PATHS, ids=lambda path: ".".join(map(str, path)))
@pytest.mark.parametrize("value", [False, True])
def test_every_manifest_boolean_accepts_boolean_values(path, value):
    data = json.loads(to_json(_boolean_manifest()))
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    loaded = json.loads(to_json(from_json(json.dumps(data))))
    for part in path[:-1]:
        loaded = loaded[part]
    assert loaded.get(path[-1], False) is value


def test_release_settings_and_automatic_acceptance_survive_restart():
    manifest = _manifest()
    slide = replace(manifest.trays[0].slides[0], auto_accepted=True)
    tray = replace(manifest.trays[0], slides=(slide,), cleanup_sweeps=(1, 2), auto="blocked")
    manifest = replace(manifest, trays=(tray,), released_render=BatchDefaults(jpeg_size="3600"))
    assert from_json(to_json(manifest)) == manifest


def test_freed_automatic_state_cannot_hide_available_raw():
    data = json.loads(to_json(_manifest()))
    data["trays"][0]["auto"] = "freed"
    with pytest.raises(ManifestFormatError, match="requires raw_removed"):
        from_json(json.dumps(data))
