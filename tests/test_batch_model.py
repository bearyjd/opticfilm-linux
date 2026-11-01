import dataclasses

import pytest

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


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rotation": 45},
        {"brightness_ev": 3.0},
        {"brightness_ev": -2.5},
        {"color_strength": 1.5},
        {"color_strength": -0.1},
        {"grain_strength": -1.0},
        {"sharpen_amount": -0.1},
    ],
)
def test_settings_reject_out_of_range(kwargs):
    with pytest.raises(ValueError):
        SlideSettings(**kwargs)


def test_settings_accept_the_range_edges():
    SlideSettings(rotation=270, brightness_ev=2.0, color_strength=1.0)
    SlideSettings(rotation=0, brightness_ev=-2.0, color_strength=0.0, grain_strength=0.0)
    SlideSettings(sharpen_amount=0.0)


def test_defaults_mean_use_the_batch_setting():
    settings = SlideSettings()
    assert settings.color_strength is None and settings.grain_strength is None
    assert settings.sharpen_amount is None and settings.rotation == 0


def test_records_are_frozen():
    slide = SlideRecord(position=1)
    tray = TrayRecord(number=1, stage=TrayStage.SCANNED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        slide.status = SlideStatus.ACCEPTED  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        tray.stage = TrayStage.REVIEW  # type: ignore[misc]


def test_new_slide_record_starts_pending_and_undecided():
    slide = SlideRecord(position=2)
    assert slide.status is SlideStatus.PENDING and slide.flags == ()
    assert slide.owner_decided is False and slide.exported is False


def test_batch_defaults_match_new_batch_defaults():
    defaults = BatchDefaults()
    assert (defaults.dpi, defaults.infrared, defaults.mode) == (7200, True, "positive")
    assert (defaults.color_strength, defaults.grain_strength, defaults.sharpen_amount) == (
        1.0,
        0.3,
        0.5,
    )


def test_manifest_starts_empty():
    manifest = BatchManifest(name="Trips")
    assert manifest.trays == () and manifest.version == 1 and Flag.TOO_DARK == "too_dark"


def test_export_defaults_start_with_full_size_jpeg():
    defaults = BatchDefaults()
    assert (defaults.export_dir, defaults.export_format, defaults.jpeg_size) == ("", "jpeg", "full")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"export_dir": None},
        {"export_dir": 123},
        {"export_format": "gif"},
        {"export_format": None},
        {"jpeg_size": "half"},
        {"jpeg_size": 3600},
    ],
)
def test_bad_export_defaults_rejected(kwargs):
    with pytest.raises(ValueError):
        BatchDefaults(**kwargs)
