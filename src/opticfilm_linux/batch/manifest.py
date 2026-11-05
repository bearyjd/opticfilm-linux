"""JSON (de)serialization of a batch manifest."""

import json
from dataclasses import asdict, fields
from typing import Any, get_args

from opticfilm_linux.batch.metadata import MetaOverride
from opticfilm_linux.batch.model import (
    AutoState,
    BatchDefaults,
    BatchManifest,
    EmptyConfirmation,
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
    if not data["description"]:
        del data["description"]
    _omit_tray_defaults(data)
    return json.dumps(data, indent=2)


def _omit_meta(record: dict[str, Any]) -> None:
    meta = {key: value for key, value in record["meta"].items() if value is not None}
    if meta:
        record["meta"] = meta
    else:
        del record["meta"]


def _omit_tray_defaults(data: dict[str, Any]) -> None:
    for tray in data["trays"]:
        if tray["evidence"] is None:
            del tray["evidence"]
        if tray["location"] is None:
            del tray["location"]
        _omit_meta(tray)
        for key, default in (
            ("cleanup_sweeps", ()),
            ("auto", ""),
            ("auto_error", ""),
            ("auto_failed", ()),
            ("auto_flagged", ()),
            ("skipped", ()),
        ):
            if tray[key] == default:
                tray.pop(key)
        for slide in tray["slides"]:
            _omit_meta(slide)
            if not slide["blank_fingerprint"]:
                slide.pop("blank_fingerprint")
            if slide["empty_confirmed"] is None:
                slide.pop("empty_confirmed")
            if not slide["auto_accepted"]:
                slide.pop("auto_accepted")
            if not slide["export_path"]:
                slide.pop("export_path")
            if not slide["export_size"]:
                slide.pop("export_size")
            if not slide["export_sha256"]:
                slide.pop("export_sha256")
            for record in slide["exports"]:
                if not record["meta_signature"]:
                    del record["meta_signature"]
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


def _receipt_text(data: dict[str, Any], key: str, *, digest: bool = False) -> None:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ManifestFormatError(f"{key} must be a string")
    if digest and value and (len(value) != 64 or set(value) - set("0123456789abcdefABCDEF")):
        raise ManifestFormatError(f"{key} must be empty or a SHA-256 hexadecimal digest")


def _export_record(data: Any) -> ExportRecord:
    if not isinstance(data, dict):
        raise ManifestFormatError("exports must contain JSON objects")
    for key in ("path", "scan_token", "export_path"):
        _receipt_text(data, key)
    for key in ("raw_sha256", "sha256", "render_signature", "meta_signature"):
        _receipt_text(data, key, digest=True)
    for key in ("width", "height", "scan_dpi", "delivered_dpi", "size_bytes"):
        minimum = 0 if key == "size_bytes" else 1
        value = data.get(key, 0)
        if type(value) is not int or value < minimum:
            raise ManifestFormatError(f"{key} must be an integer >= {minimum}")
    return ExportRecord(**data)


def _slide_delivery(data: dict[str, Any]) -> tuple[ExportRecord, ...]:
    _receipt_text(data, "export_path")
    _receipt_text(data, "export_sha256", digest=True)
    size = data.get("export_size", [])
    if (
        not isinstance(size, list)
        or (size and len(size) != 2)
        or any(type(value) is not int or value <= 0 for value in size)
    ):
        raise ManifestFormatError("export_size must be empty or two positive integers")
    exports = data.get("exports", [])
    if not isinstance(exports, list):
        raise ManifestFormatError("exports must be a list")
    return tuple(_export_record(record) for record in exports)


def _meta(data: dict[str, Any]) -> MetaOverride:
    meta = data.get("meta", {})
    if not isinstance(meta, dict):
        raise ManifestFormatError("meta must be an object")
    # Keys from a later build are ignored, and values are checked by resolve, not here.
    return MetaOverride(description=meta.get("description"), date=meta.get("date"))


def _slide(data: dict[str, Any]) -> SlideRecord:
    _validate_booleans(data, SlideRecord)
    _receipt_text(data, "blank_fingerprint", digest=True)
    exports = _slide_delivery(data)
    return SlideRecord(
        position=data["position"],
        status=SlideStatus(data["status"]),
        flags=tuple(Flag(f) for f in data["flags"]),
        settings=_settings(data["settings"]),
        owner_decided=data["owner_decided"],
        auto_accepted=data.get("auto_accepted", False),
        blank_fingerprint=data.get("blank_fingerprint", ""),
        empty_confirmed=_empty_confirmation(data.get("empty_confirmed")),
        exported=data["exported"],
        preview_stale=data.get("preview_stale", False),
        export_path=data.get("export_path", ""),
        export_size=tuple(data.get("export_size", ())),
        export_sha256=data.get("export_sha256", ""),
        exports=exports,
        meta=_meta(data),
    )


def _empty_confirmation(data: Any) -> EmptyConfirmation | None:
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) != {"sweep_version", "fingerprint"}:
        raise ManifestFormatError("empty_confirmed needs a sweep version and fingerprint")
    return EmptyConfirmation(**data)


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
    _receipt_text(data, "auto_error")
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
        meta=_meta(data),
        skipped=_skipped(data),
    )


def _skipped(data: dict[str, Any]) -> tuple[int, ...]:
    """Holder openings left out of the scan; absent in trays written before skipping existed."""
    value = data.get("skipped", [])
    if not isinstance(value, list):
        raise ManifestFormatError("skipped must be a list of holder openings")
    return tuple(value)


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
            description=data.get("description", ""),
        )
    except ManifestFormatError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestFormatError(f"invalid manifest: {type(exc).__name__}: {exc}") from exc
