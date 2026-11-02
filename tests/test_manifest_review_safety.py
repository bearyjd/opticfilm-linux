import json
from dataclasses import replace

import pytest

from opticfilm_linux.batch.manifest import ManifestFormatError, from_json, to_json
from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    SlideRecord,
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
@pytest.mark.parametrize("value", ["12", [True], [1.2], [0], [-1], ["1"], None, [1, 1]])
def test_automatic_position_lists_are_validated(key, value):
    data = json.loads(to_json(_manifest()))
    data["trays"][0][key] = value
    with pytest.raises(ManifestFormatError, match=key):
        from_json(json.dumps(data))


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
