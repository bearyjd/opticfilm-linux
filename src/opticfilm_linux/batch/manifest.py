"""JSON (de)serialization of a batch manifest."""

import json
from dataclasses import asdict, fields
from typing import Any, get_args

from opticfilm_linux.batch.model import (
    AutoState,
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
    if manifest.released_render is None:
        data.pop("released_render")
    if not manifest.first_tray_released:
        data.pop("first_tray_released")
    for key, default in {"collection": "", "box": 1, "section": "a", "section_size": 140}.items():
        if data[key] == default:
            del data[key]
    _omit_tray_defaults(data)
    return json.dumps(data, indent=2)


def _omit_tray_defaults(data: dict[str, Any]) -> None:
    for tray in data["trays"]:
        if tray["evidence"] is None:
            del tray["evidence"]
        if tray["location"] is None:
            del tray["location"]
        for key, default in (
            ("cleanup_sweeps", ()),
            ("auto", ""),
            ("auto_error", ""),
            ("auto_failed", ()),
            ("auto_flagged", ()),
        ):
            if tray[key] == default:
                tray.pop(key)
        for slide in tray["slides"]:
            if not slide["auto_accepted"]:
                slide.pop("auto_accepted")
            if not slide["export_path"]:
                slide.pop("export_path")
            if not slide["export_size"]:
                slide.pop("export_size")
            if not slide["export_sha256"]:
                slide.pop("export_sha256")
            if not slide["exports"]:
                del slide["exports"]


def _validate_booleans(data: dict[str, Any], record_type: type) -> None:
    if not isinstance(data, dict):
        raise ManifestFormatError(f"{record_type.__name__} must be a JSON object")
    for field in fields(record_type):
        if field.type in (bool, "bool") and field.name in data:
            if type(data[field.name]) is not bool:
                raise ManifestFormatError(f"{field.name} must be a boolean")


def _settings(data: dict[str, Any]) -> SlideSettings:
    _validate_booleans(data, SlideSettings)
    return SlideSettings(**data)


def _slide(data: dict[str, Any]) -> SlideRecord:
    _validate_booleans(data, SlideRecord)
    return SlideRecord(
        position=data["position"],
        status=SlideStatus(data["status"]),
        flags=tuple(Flag(f) for f in data["flags"]),
        settings=_settings(data["settings"]),
        owner_decided=data["owner_decided"],
        auto_accepted=data.get("auto_accepted", False),
        exported=data["exported"],
        preview_stale=data.get("preview_stale", False),
        export_path=data.get("export_path", ""),
        export_size=tuple(data.get("export_size", ())),
        export_sha256=data.get("export_sha256", ""),
        exports=tuple(ExportRecord(**record) for record in data.get("exports", [])),
    )


def _evidence(data: dict[str, Any] | None) -> ScanEvidence | None:
    if data is None:
        return None
    return ScanEvidence(**{**data, "diagnostics": tuple(data["diagnostics"])})


def _integer_list(data: dict[str, Any], key: str, minimum: int = 1) -> tuple[int, ...]:
    positions = data.get(key, [])
    if not isinstance(positions, (list, tuple)) or any(
        type(value) is not int or value < minimum for value in positions
    ):
        raise ManifestFormatError(f"{key} must be a list of integers >= {minimum}")
    if len(set(positions)) != len(positions):
        raise ManifestFormatError(f"{key} must contain unique positions")
    return tuple(positions)


def _tray(data: dict[str, Any]) -> TrayRecord:
    _validate_booleans(data, TrayRecord)
    if data.get("auto", "") not in get_args(AutoState):
        raise ManifestFormatError("invalid automatic export state")
    if data.get("auto") == "freed" and data["raw_removed"] is not True:
        raise ManifestFormatError("freed automatic state requires raw_removed")
    return TrayRecord(
        number=data["number"],
        stage=TrayStage(data["stage"]),
        mode=data["mode"],
        sweep_version=data["sweep_version"],
        slides=tuple(_slide(s) for s in data["slides"]),
        error=data["error"],
        raw_removed=data["raw_removed"],
        cleanup_sweeps=_integer_list(data, "cleanup_sweeps", minimum=0),
        auto=data.get("auto", ""),
        auto_error=data.get("auto_error", ""),
        auto_failed=_integer_list(data, "auto_failed"),
        auto_flagged=_integer_list(data, "auto_flagged"),
        location=TrayLocation(**data["location"]) if data.get("location") is not None else None,
        evidence=_evidence(data.get("evidence")),
    )


SLOT_KEYS = {"position", "rotation", "mirror"}


def _slot(data: Any) -> SlotOrientation:
    if not isinstance(data, dict) or set(data) != SLOT_KEYS:
        raise ManifestFormatError(f"a slot orientation needs exactly {sorted(SLOT_KEYS)}")
    _validate_booleans(data, SlotOrientation)
    return SlotOrientation(**data)


def _defaults(data: dict[str, Any]) -> BatchDefaults:
    _validate_booleans(data, BatchDefaults)
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
        _validate_booleans(data, BatchManifest)
        return BatchManifest(
            name=data["name"],
            album=data["album"],
            date=data["date"],
            defaults=_defaults(data["defaults"]),
            trays=tuple(_tray(t) for t in data["trays"]),
            version=data["version"],
            first_tray_released=data.get("first_tray_released", False),
            released_render=_defaults(data["released_render"])
            if data.get("released_render")
            else None,
            collection=data.get("collection", ""),
            box=data.get("box", 1),
            section=data.get("section", "a"),
            section_size=data.get("section_size", 140),
        )
    except ManifestFormatError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestFormatError(f"invalid manifest: {type(exc).__name__}: {exc}") from exc
