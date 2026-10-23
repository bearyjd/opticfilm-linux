"""JSON (de)serialization of a batch manifest."""

import json
from dataclasses import asdict
from typing import Any

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

MANIFEST_VERSION = 1


class ManifestFormatError(ValueError):
    """The manifest is unreadable, from a newer version, or has a missing or bad field."""


def to_json(manifest: BatchManifest) -> str:
    return json.dumps(asdict(manifest), indent=2)


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
    )


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
            defaults=BatchDefaults(**data["defaults"]),
            trays=tuple(_tray(t) for t in data["trays"]),
            version=data["version"],
        )
    except ManifestFormatError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestFormatError(f"invalid manifest: {type(exc).__name__}: {exc}") from exc
