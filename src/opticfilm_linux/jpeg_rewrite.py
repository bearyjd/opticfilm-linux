"""Put new description and date fields into an exported JPEG without re-encoding the image.

Only our own ofscan XMP segment (APP1) changes; every other segment and the entropy-coded image
data are copied byte for byte, so EXIF (including DateTimeDigitized), the ICC profile and the
pixels stay exactly as exported. No standard description or date tag is ever written, and
extended XMP is not used. The technical ofscan fields already in the file are kept as they are,
because the raw scan they came from may have been freed since.

The file is replaced atomically: the new bytes go to a temporary file in the same directory with
the old file's permission bits, are fsynced, renamed over the old file, and the directory is
synced. A crash before the rename leaves the old file. The modification time becomes the time of
the rewrite, which is honest (the file did change) and lets photo indexers notice it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import stat
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from pathlib import Path

from opticfilm_linux.output import _sync_directories
from opticfilm_linux.provenance import XMP_HEADER, XMP_NAMESPACE, _xmp_packet

log = logging.getLogger(__name__)

SOI = b"\xff\xd8"
SOS = 0xDA
APP0, APP1 = 0xE0, 0xE1
MAX_APP1_PAYLOAD = 65533
_NO_LENGTH = frozenset({0x01, 0xD8, 0xD9, *range(0xD0, 0xD8)})
_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_OURS = "{" + XMP_NAMESPACE + "}"
ESTIMATE_KEYS = frozenset(
    {
        "Description",
        "DescriptionSource",
        "PhotoDate",
        "PhotoDatePrecision",
        "PhotoDateApproximate",
        "PhotoDateSource",
        "PhotoDateLevel",
    }
)


class RewriteRefused(ValueError):
    """The file is not one this program can safely change."""


def jpeg_segments(data: bytes) -> tuple[list[bytes], bytes]:
    """The marker segments between SOI and the scan, and everything from SOS on, verbatim."""
    if not data.startswith(SOI):
        raise RewriteRefused("not a JPEG file")
    segments: list[bytes] = []
    at = len(SOI)
    while True:
        if at + 4 > len(data) or data[at] != 0xFF or data[at + 1] in _NO_LENGTH | {0xFF}:
            raise RewriteRefused("damaged or unusual JPEG marker before the image data")
        if data[at + 1] == SOS:
            return segments, data[at:]
        end = at + 2 + int.from_bytes(data[at + 2 : at + 4], "big")
        if end < at + 4 or end > len(data):
            raise RewriteRefused("damaged JPEG segment length")
        segments.append(data[at:end])
        at = end


def _is_xmp(segment: bytes) -> bool:
    return segment[1] == APP1 and segment[4:].startswith(XMP_HEADER)


def _xmp_segment(packet: bytes) -> bytes:
    payload = XMP_HEADER + packet
    if len(payload) > MAX_APP1_PAYLOAD:
        raise ValueError(f"XMP APP1 payload exceeds {MAX_APP1_PAYLOAD} bytes")
    return bytes((0xFF, APP1)) + (len(payload) + 2).to_bytes(2, "big") + payload


def _insert_at(segments: list[bytes]) -> int:
    """After the leading JFIF and EXIF segments, where the export writer puts XMP."""
    at = 0
    while at < len(segments) and segments[at][1] in (APP0, APP1):
        at += 1
    return at


def replace_xmp(data: bytes, update: Callable[[bytes | None], bytes]) -> bytes:
    """``data`` with its one XMP packet replaced by ``update(old packet or None)``."""
    segments, scan = jpeg_segments(data)
    found = [i for i, segment in enumerate(segments) if _is_xmp(segment)]
    if len(found) > 1:
        raise RewriteRefused("the JPEG has more than one XMP segment")
    old = segments[found[0]][4 + len(XMP_HEADER) :] if found else None
    new = _xmp_segment(update(old))
    if found:
        kept = segments[: found[0]] + [new] + segments[found[0] + 1 :]
    else:
        at = _insert_at(segments)
        kept = segments[:at] + [new] + segments[at:]
    return SOI + b"".join(kept) + scan


def _our_fields(packet: bytes) -> dict[str, str]:
    """The ofscan fields of a packet this program wrote; anything else is refused, not dropped."""
    try:
        root = ET.fromstring(packet)
    except ET.ParseError as exc:
        raise RewriteRefused("the XMP of this JPEG is not readable") from exc
    rdf = list(root)
    descriptions = list(rdf[0]) if len(rdf) == 1 and rdf[0].tag == f"{_RDF}RDF" else []
    if root.tag != "{adobe:ns:meta/}xmpmeta" or len(descriptions) != 1:
        raise RewriteRefused("the XMP of this JPEG was not written by this program")
    (description,) = descriptions
    children = list(description)
    if (
        description.tag != f"{_RDF}Description"
        or set(description.attrib) - {f"{_RDF}about"}
        or any(not c.tag.startswith(_OURS) or len(c) or c.attrib for c in children)
    ):
        raise RewriteRefused("the XMP of this JPEG holds fields this program did not write")
    return {child.tag[len(_OURS) :]: child.text or "" for child in children}


def updated_packet(old: bytes | None, estimate: Mapping[str, str]) -> bytes:
    """The old packet's fields with the description and date fields replaced by ``estimate``."""
    kept = {k: v for k, v in _our_fields(old).items() if k not in ESTIMATE_KEYS} if old else {}
    return _xmp_packet({**kept, **estimate})


def _read_exactly(path: Path) -> tuple[bytes, int, tuple[int, int, int]]:
    """The file's bytes, its permission bits and its identity (inode, size, mtime_ns)."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise RewriteRefused("the export is not a regular file")
        identity = (opened.st_ino, opened.st_size, opened.st_mtime_ns)
        return handle.read(), stat.S_IMODE(opened.st_mode), identity


def fingerprint(path: Path) -> tuple[str, int]:
    """The sha256 and size of the file as it is now."""
    data, _, _ = _read_exactly(path)
    return hashlib.sha256(data).hexdigest(), len(data)


def _replace(path: Path, data: bytes, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(temporary, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)  # the umask must not change the export's mode
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    try:
        _sync_directories(path.parent, required=False)
    except OSError as exc:  # the rename is done; the bytes are in place, so this is not a failure
        log.warning("directory sync after rewriting %s failed: %s", path, exc)


TEMPORARY_MAX_AGE_S = 60.0


def clean_stale_temporaries(path: Path, now: float | None = None) -> int:
    """Remove the temporary files an interrupted rewrite of ``path`` left beside it: exactly
    ``.<name>.<12 hex digits>.tmp``, older than a minute. Returns how many were removed."""
    pattern = re.compile(r"\." + re.escape(path.name) + r"\.[0-9a-f]{12}\.tmp")
    now = time.time() if now is None else now
    removed = 0
    try:
        names = os.listdir(path.parent)
    except OSError:
        return 0
    for name in names:
        if not pattern.fullmatch(name):
            continue
        candidate = path.parent / name
        try:
            status = candidate.lstat()
            if stat.S_ISREG(status.st_mode) and now - status.st_mtime > TEMPORARY_MAX_AGE_S:
                candidate.unlink()
                removed += 1
        except OSError as exc:
            log.warning("could not remove the stale temporary file %s: %s", candidate, exc)
    return removed


def rewrite_xmp_file(
    path: Path,
    sha256: str,
    size_bytes: int,
    estimate: Mapping[str, str],
    *,
    pending: tuple[str, int] = ("", 0),
    before_replace: Callable[[str, int], None] | None = None,
) -> tuple[str, int]:
    """Rewrite the description and date fields of the JPEG at ``path``, which must still be
    exactly the file the export wrote (``sha256``, ``size_bytes``), or the file an earlier
    rewrite was about to put there (``pending``, recorded by ``before_replace`` just before
    its rename); returns the new ``(sha256, size_bytes)``.

    ``before_replace(new_sha256, new_size)`` is called after every check and before the
    rename, so the caller can record what the file will become: if the process dies between
    the rename and saving the new receipt, the next pass recognises the file as its own work.
    """
    data, mode, identity = _read_exactly(path)
    digest = hashlib.sha256(data).hexdigest()
    ours = (digest, len(data)) == (sha256, size_bytes)
    ours_pending = bool(pending[0]) and (digest, len(data)) == pending
    # the receipt is checked first, also when the fields are already current: a file that
    # another program changed must never have its receipt renewed to match it
    if not (ours or ours_pending):
        raise RewriteRefused("the file changed since it was exported; it was left untouched")
    new = replace_xmp(data, lambda old: updated_packet(old, estimate))
    if new == data:  # nothing to write; the caller renews the receipt
        return digest, len(data)
    if not mode & stat.S_IWUSR:
        raise RewriteRefused("the export is read-only; it was left untouched")
    new_digest = hashlib.sha256(new).hexdigest()
    if before_replace is not None:
        before_replace(new_digest, len(new))
    # recording the plan took time: the file must still be the one that was checked
    now = os.lstat(path)
    if (now.st_ino, now.st_size, now.st_mtime_ns) != identity:
        raise RewriteRefused("the file changed while it was being rewritten; it was left as it is")
    _replace(path, new, mode)
    return new_digest, len(new)
