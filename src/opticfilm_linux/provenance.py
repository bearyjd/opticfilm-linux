"""Immutable scan evidence, independent of the desktop app and image models."""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image

from opticfilm_linux import __version__

if TYPE_CHECKING:
    from opticfilm_linux.batch.model import BatchManifest, SlideRecord, TrayRecord
    from opticfilm_linux.model import Slide
    from opticfilm_linux.pipeline.run import ProcessOptions

log = logging.getLogger(__name__)
_DIAGNOSTIC_NAME = re.compile(r"(?:\d{8}-\d{6}-)?tray-f([1-6])\.diag\.json\Z")


@dataclass(frozen=True)
class ScanEvidence:
    digitized_at: str
    duration_s: float
    software: str
    driver_revision: str | None
    diagnostics: tuple[str, ...]


def diagnostics_from_files(paths: list[Path], frames: int) -> tuple[str, ...]:
    """Retain one JSON object per frame; never choose between competing sidecars."""
    candidates: dict[int, list[Path]] = {}
    for path in paths:
        match = _DIAGNOSTIC_NAME.fullmatch(path.name)
        if match is not None and 1 <= (frame := int(match[1])) <= frames:
            candidates.setdefault(frame, []).append(path)
    result = []
    for frame in range(1, frames + 1):
        matches = candidates.get(frame, [])
        if len(matches) != 1:
            log.warning("missing or ambiguous scan diagnostics for frame %d", frame)
            result.append("{}")
            continue
        try:
            data = json.loads(matches[0].read_text())
            if not isinstance(data, dict):
                raise ValueError("diagnostics must be an object")
            declared_frames = [data["frame"]] if "frame" in data else []
            cli = data.get("cli")
            if isinstance(cli, dict) and "frame" in cli:
                declared_frames.append(cli["frame"])
            if any(type(value) is not int or value != frame for value in declared_frames):
                raise ValueError("diagnostics frame does not match its filename")
            result.append(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False))
        except (OSError, ValueError, RecursionError) as exc:
            log.warning("invalid scan diagnostics for frame %d: %s", frame, exc)
            result.append("{}")
    return tuple(result)


TONE_VERSION = "linear-black-point-v1"
XMP_NAMESPACE = "https://opticfilm-linux.org/ns/scan/1.0/"
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\0"


@dataclass(frozen=True)
class JpegMetadata:
    exif: bytes
    xmp: bytes
    raw_sha256: str


def raw_region_sha256(slide: "Slide") -> str:
    """Hash pre-enhancement RGB then IR with canonical framing and one row of memory.

    Regions may be strided views into memory-mapped sweeps. Converting one row at a
    time keeps both the digest and peak allocation independent of the sweep size.
    """
    digest = hashlib.sha256()
    for channel, plane in (("RGB", slide.rgb), ("IR", slide.ir)):
        if plane is None:
            digest.update(f"{channel}:absent\n".encode("ascii"))
            continue
        shape = ",".join(str(side) for side in plane.shape)
        digest.update(f"{channel}:uint16:{shape}:little-endian\n".encode("ascii"))
        for row in plane:
            digest.update(np.asarray(row, dtype="<u2").tobytes(order="C"))
    return digest.hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _owner_date(value: str | None) -> tuple[str, str] | None:
    """Validate owner precision without completing a partial historical date."""
    if not value or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        return None
    try:
        date.fromisoformat(
            value + ("-01-01" if len(value) == 4 else "-01" if len(value) == 7 else "")
        )
    except ValueError:
        return None
    return value, {4: "year", 7: "month", 10: "day"}[len(value)]


def _frame_diagnostic(tray: "TrayRecord", slot: int) -> dict:
    if tray.evidence is None or not 1 <= slot <= len(tray.evidence.diagnostics):
        return {}
    try:
        data = json.loads(tray.evidence.diagnostics[slot - 1])
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _exif_metadata(software: str, evidence: ScanEvidence | None, owner_date, delivered_dpi: int):
    exif = Image.Exif()
    exif[271], exif[272], exif[305] = "Plustek", "OpticFilm 135i", software
    exif[274], exif[282], exif[283], exif[296] = 1, delivered_dpi, delivered_dpi, 2
    exif_ifd = {}
    if evidence:
        try:
            scanned = datetime.fromisoformat(evidence.digitized_at)
            if scanned.utcoffset() is not None:
                scanned = scanned.astimezone(UTC)
                exif_ifd[36868] = scanned.strftime("%Y:%m:%d %H:%M:%S")
                exif_ifd[36882] = "+00:00"
        except ValueError:
            pass
    if owner_date and owner_date[1] == "day":
        exif_ifd[36867] = owner_date[0].replace("-", ":") + " 00:00:00"
    if exif_ifd:
        exif[34665] = exif_ifd
    return exif.tobytes()


def _review_flags(record: "SlideRecord") -> list[str]:
    flags = [str(flag) for flag in record.flags]
    if record.status == "blank":
        flags.append("blank")
    if record.flags or record.status == "needs_look":
        flags.append("needs_look")
    return flags


def _render_settings(options: "ProcessOptions") -> dict:
    return {
        key: getattr(options, key)
        for key in (
            "brightness_ev",
            "color_strength",
            "grain_strength",
            "sharpen_amount",
            "remove_dust",
            "rotation",
            "mirror",
        )
    }


def _technical_fields(
    tray: "TrayRecord", options: "ProcessOptions", software: str, scan_dpi: int, delivered_dpi: int
) -> dict:
    evidence, location = tray.evidence, tray.location
    return {
        "Version": "1",
        "ScannerMake": "Plustek",
        "ScannerModel": "OpticFilm 135i",
        "Software": software,
        "DriverRevision": evidence.driver_revision
        if evidence and evidence.driver_revision
        else "unknown",
        "DigitizedAt": evidence.digitized_at if evidence else "unknown",
        "ScanDPI": scan_dpi,
        "DeliveredDPI": delivered_dpi,
        "FilmMode": options.mode,
        "Holder": "strip6" if location and location.capacity == 6 else "slide4",
    }


def _location_fields(manifest: "BatchManifest", tray: "TrayRecord", record: "SlideRecord") -> dict:
    location = tray.location
    return {
        "Batch": manifest.name,
        "Collection": location.collection if location else "unknown",
        "Box": location.box if location else "unknown",
        "Section": location.section if location else "unknown",
        "Tray": tray.number,
        "Slot": record.position,
        "ScanToken": f"T{tray.number:03d}S{record.position}",
    }


def _diagnostic_fields(tray: "TrayRecord", record: "SlideRecord") -> dict:
    evidence = tray.evidence
    diagnostic = _frame_diagnostic(tray, record.position)
    return {
        "CalibrationIntegrity": diagnostic.get("cal_integrity") or "unknown",
        "GainCodes": _canonical(diagnostic["gain_codes"])
        if diagnostic.get("gain_codes") is not None
        else "unknown",
        "OffsetCodes": _canonical(diagnostic["offset_codes"])
        if diagnostic.get("offset_codes") is not None
        else "unknown",
        "ScanDurationSeconds": diagnostic.get("total_seconds")
        if diagnostic.get("total_seconds") is not None
        else "unknown",
        "TrayScanDurationSeconds": evidence.duration_s if evidence else "unknown",
    }


def _xml_text(value: object) -> str:
    """Strip characters XML 1.0 forbids; retain whitespace, Unicode, and owner text otherwise."""
    return "".join(
        char
        for char in str(value)
        if char in "\t\n\r"
        or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD
        or 0x10000 <= ord(char) <= 0x10FFFF
    )


def _xmp_packet(fields: dict) -> bytes:
    ET.register_namespace("ofscan", XMP_NAMESPACE)
    rdf_namespace = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    ET.register_namespace("rdf", rdf_namespace)
    root = ET.Element("{adobe:ns:meta/}xmpmeta")
    rdf = ET.SubElement(root, f"{{{rdf_namespace}}}RDF")
    description = ET.SubElement(
        rdf, f"{{{rdf_namespace}}}Description", {f"{{{rdf_namespace}}}about": ""}
    )
    for key, value in fields.items():
        ET.SubElement(description, f"{{{XMP_NAMESPACE}}}{key}").text = _xml_text(value)
    return ET.tostring(root, encoding="utf-8")


def jpeg_metadata(
    manifest: "BatchManifest",
    tray: "TrayRecord",
    record: "SlideRecord",
    slide: "Slide",
    options: "ProcessOptions",
    scan_dpi: int,
    delivered_dpi: int,
) -> JpegMetadata:
    """Encode technical provenance while leaving descriptive fields for the owner."""
    evidence = tray.evidence
    software = evidence.software if evidence else f"opticfilm-linux {__version__}"
    owner_date = _owner_date(manifest.date)
    raw_hash = raw_region_sha256(slide)
    fields = _technical_fields(tray, options, software, scan_dpi, delivered_dpi)
    fields.update(_location_fields(manifest, tray, record))
    fields.update(_diagnostic_fields(tray, record))
    fields.update(
        Settings=_canonical(_render_settings(options)),
        ToneVersion=TONE_VERSION,
        ReviewFlags=_canonical(_review_flags(record)),
        RawSHA256=raw_hash,
    )
    if owner_date:
        fields.update(
            PhotoDate=owner_date[0],
            PhotoDatePrecision=owner_date[1],
            PhotoDateSource="owner-entered",
        )
    return JpegMetadata(
        _exif_metadata(software, evidence, owner_date, delivered_dpi), _xmp_packet(fields), raw_hash
    )
