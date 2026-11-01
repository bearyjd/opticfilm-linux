"""JSON (de)serialization of a batch manifest."""

import json
from dataclasses import asdict
from typing import Any

from opticfilm_linux.batch.model import (
    BatchDefaults,
    BatchManifest,
    ExportRecord,
    Flag,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    SlotOrientation,
    TrayLocation,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.provenance import ScanEvidence

MANIFEST_VERSION = 1


class ManifestFormatError(ValueError):
    """The manifest is unreadable, from a newer version, or has a missing or bad field."""


def to_json(manifest: BatchManifest) -> str:
    data = asdict(manifest)
    omitted_defaults = {"slots": (), "export_dir": "", "export_format": "jpeg", "jpeg_size": "full"}
    data["defaults"] = {
        key: value
        for key, value in data["defaults"].items()
        if key not in omitted_defaults or value != omitted_defaults[key]
    }
    for key, default in {"collection": "", "box": 1, "section": "a", "section_size": 140}.items():
        if data[key] == default:
            del data[key]
    for tray in data["trays"]:
        if tray["evidence"] is None:
            del tray["evidence"]
        if tray["location"] is None:
            del tray["location"]
        for slide in tray["slides"]:
            if not slide["exports"]:
                del slide["exports"]
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
        exports=tuple(ExportRecord(**record) for record in data.get("exports", [])),
    )


def _evidence(data: dict[str, Any] | None) -> ScanEvidence | None:
    if data is None:
        return None
    return ScanEvidence(**{**data, "diagnostics": tuple(data["diagnostics"])})


def _tray(data: dict[str, Any]) -> TrayRecord:
    return TrayRecord(
        number=data["number"],
        stage=TrayStage(data["stage"]),
        mode=data["mode"],
        sweep_version=data["sweep_version"],
        slides=tuple(_slide(s) for s in data["slides"]),
        error=data["error"],
        raw_removed=data["raw_removed"],
        location=TrayLocation(**data["location"]) if data.get("location") is not None else None,
        evidence=_evidence(data.get("evidence")),
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


def from_json(text: str) -> BatchManifest:
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ManifestFormatError("manifest must be a JSON object")
        if data.get("version") != MANIFEST_VERSION:
            raise ManifestFormatError(f"unsupported manifest version {data.get('version')!r}")
        return BatchManifest(
            name=data["name"],
            album=data["album"],
            date=data["date"],
            defaults=_defaults(data["defaults"]),
            trays=tuple(_tray(t) for t in data["trays"]),
            version=data["version"],
            collection=data.get("collection", ""),
            box=data.get("box", 1),
            section=data.get("section", "a"),
            section_size=data.get("section_size", 140),
        )
    except ManifestFormatError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestFormatError(f"invalid manifest: {type(exc).__name__}: {exc}") from exc
