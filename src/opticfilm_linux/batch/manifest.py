"""JSON (de)serialization of a batch manifest."""

import json
import logging
from dataclasses import asdict
from typing import Any

from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    Flag,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    SlotOrientation,
    TrayRecord,
    TrayStage,
)

MANIFEST_VERSION = 1
logger = logging.getLogger(__name__)


class ManifestFormatError(ValueError):
    """The manifest is unreadable, from a newer version, or has a missing or bad field."""


def to_json(manifest: BatchManifest) -> str:
    data = asdict(manifest)
    omitted_defaults = {
        "slots": (),
        "export_dir": "",
        "export_format": "jpeg",
        "jpeg_size": "full",
        "auto_export": False,
        "keep_raw": False,
    }
    data["defaults"] = {
        key: value
        for key, value in data["defaults"].items()
        if key not in omitted_defaults or value != omitted_defaults[key]
    }
    if not manifest.first_tray_released:
        data.pop("first_tray_released")
    for tray in data["trays"]:
        for key, default in (
            ("auto", ""),
            ("auto_error", ""),
            ("auto_failed", ()),
            ("auto_flagged", ()),
        ):
            if tray[key] == default:
                tray.pop(key)
        for slide in tray["slides"]:
            if not slide["export_path"]:
                slide.pop("export_path")
            if not slide["export_size"]:
                slide.pop("export_size")
            if not slide["export_sha256"]:
                slide.pop("export_sha256")
    return json.dumps(data, indent=2)


def _settings(data: dict[str, Any]) -> SlideSettings:
    return SlideSettings(**data)


def _slide(data: dict[str, Any]) -> SlideRecord:
    return SlideRecord(
        position=data["position"],
        status=SlideStatus(data["status"]),
        flags=tuple(Flag(f) for f in data["flags"]),
        settings=_settings(data["settings"]),
        owner_decided=data["owner_decided"],
        exported=data["exported"],
        preview_stale=data.get("preview_stale", False),
        export_path=data.get("export_path", ""),
        export_size=tuple(data.get("export_size", ())),
        export_sha256=data.get("export_sha256", ""),
    )


def _tray(data: dict[str, Any]) -> TrayRecord:
    return TrayRecord(
        number=data["number"],
        stage=TrayStage(data["stage"]),
        mode=data["mode"],
        sweep_version=data["sweep_version"],
        slides=tuple(_slide(s) for s in data["slides"]),
        error=data["error"],
        raw_removed=data["raw_removed"],
        auto=data.get("auto", ""),
        auto_error=data.get("auto_error", ""),
        auto_failed=tuple(data.get("auto_failed", ())),
        auto_flagged=tuple(data.get("auto_flagged", ())),
    )


SLOT_KEYS = {"position", "rotation", "mirror"}


def _slot(data: Any) -> SlotOrientation:
    if not isinstance(data, dict) or set(data) != SLOT_KEYS:
        raise ManifestFormatError(f"a slot orientation needs exactly {sorted(SLOT_KEYS)}")
    return SlotOrientation(**data)


def _defaults(data: dict[str, Any]) -> BatchDefaults:
    slots = data.get("slots", [])  # absent in batches made before slot orientations
    if not isinstance(slots, list):
        raise ManifestFormatError("slots must be a list")
    return BatchDefaults(**{"dpi": 3600, **data, "slots": tuple(_slot(s) for s in slots)})


def _safe_policy(data: dict[str, Any]) -> None:
    defaults = data["defaults"]
    if not isinstance(defaults, dict):
        raise ManifestFormatError("defaults must be a JSON object")
    fields = (
        (defaults, "auto_export"),
        (defaults, "keep_raw"),
        (data, "first_tray_released"),
    )
    invalid = [key for source, key in fields if key in source and type(source[key]) is not bool]
    if invalid:
        logger.warning(
            "Invalid boolean policy fields %s; applying safe batch policy", ", ".join(invalid)
        )
        defaults.update(auto_export=False, keep_raw=True)
        data["first_tray_released"] = False


def from_json(text: str) -> BatchManifest:
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ManifestFormatError("manifest must be a JSON object")
        if data.get("version") != MANIFEST_VERSION:
            raise ManifestFormatError(f"unsupported manifest version {data.get('version')!r}")
        _safe_policy(data)
        return BatchManifest(
            name=data["name"],
            album=data["album"],
            date=data["date"],
            defaults=_defaults(data["defaults"]),
            trays=tuple(_tray(t) for t in data["trays"]),
            version=data["version"],
            first_tray_released=data.get("first_tray_released", False),
        )
    except ManifestFormatError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestFormatError(f"invalid manifest: {type(exc).__name__}: {exc}") from exc
