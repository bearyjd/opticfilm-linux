"""Rewriting the ofscan XMP of an exported JPEG never re-encodes the image."""

import hashlib
import io
import os
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PIL import Image

from opticfilm_linux import jpeg_rewrite
from opticfilm_linux.jpeg_rewrite import (
    RewriteRefused,
    jpeg_segments,
    replace_xmp,
    rewrite_xmp_file,
    updated_packet,
)
from opticfilm_linux.output import _srgb_profile
from opticfilm_linux.provenance import XMP_HEADER, XMP_NAMESPACE, _xmp_packet

TECHNICAL = {"ScanDPI": "3600", "RawSHA256": "a" * 64}
ESTIMATE = {
    "Description": "Lake trip",
    "DescriptionSource": "batch",
    "PhotoDate": "1978",
    "PhotoDatePrecision": "year",
    "PhotoDateApproximate": "true",
    "PhotoDateSource": "owner-estimate",
    "PhotoDateLevel": "batch",
}


def _plain_jpeg() -> bytes:
    rng = np.random.default_rng(7)
    pixels = rng.integers(0, 256, (24, 32, 3), dtype=np.uint8)
    exif = Image.Exif()
    exif[0x0131] = "opticfilm-linux test"
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(
        buffer, "JPEG", quality=90, icc_profile=_srgb_profile(), exif=exif.tobytes()
    )
    return buffer.getvalue()


def _exported(fields=None) -> bytes:
    return replace_xmp(_plain_jpeg(), lambda _old: _xmp_packet(fields or {**TECHNICAL, **ESTIMATE}))


def _fields(data: bytes) -> dict:
    segments, _ = jpeg_segments(data)
    (packet,) = [s[4 + len(XMP_HEADER) :] for s in segments if s[4:].startswith(XMP_HEADER)]
    root = ET.fromstring(packet)
    prefix = "{" + XMP_NAMESPACE + "}"
    return {e.tag[len(prefix) :]: e.text or "" for e in root.iter() if e.tag.startswith(prefix)}


def _decoded(data: bytes):
    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("RGB")), image.info.get("icc_profile")


def _without_xmp(data: bytes) -> list[bytes]:
    return [s for s in jpeg_segments(data)[0] if not s[4:].startswith(XMP_HEADER)]


def test_inserting_xmp_keeps_every_other_segment_and_the_image_data():
    plain = _plain_jpeg()
    data = _exported()
    assert _without_xmp(data) == jpeg_segments(plain)[0]
    assert jpeg_segments(data)[1] == jpeg_segments(plain)[1]
    markers = [s[1] for s in jpeg_segments(data)[0]]
    exif_at = next(i for i, s in enumerate(jpeg_segments(data)[0]) if s[4:].startswith(b"Exif\0\0"))
    assert markers[exif_at + 1] == 0xE1  # the XMP follows the EXIF segment
    assert _fields(data) == {**TECHNICAL, **ESTIMATE}


def test_replacing_changes_only_the_estimate_fields():
    before = _exported()
    after = replace_xmp(before, lambda old: updated_packet(old, {"Description": "Fair"}))
    assert _fields(after) == {**TECHNICAL, "Description": "Fair"}
    assert _without_xmp(after) == _without_xmp(before)
    scan_before, scan_after = jpeg_segments(before)[1], jpeg_segments(after)[1]
    assert hashlib.sha256(scan_after).digest() == hashlib.sha256(scan_before).digest()
    pixels_before, icc_before = _decoded(before)
    pixels_after, icc_after = _decoded(after)
    assert np.array_equal(pixels_before, pixels_after)
    assert icc_after == icc_before == _srgb_profile()
    with Image.open(io.BytesIO(after)) as image:
        exif = image.getexif()
        assert 0x9004 not in exif.get_ifd(0x8769)  # DateTimeDigitized never added
        assert 0x9003 not in exif.get_ifd(0x8769) and 0x010E not in exif


def test_unchanged_metadata_gives_a_byte_identical_file():
    data = _exported()
    assert replace_xmp(data, lambda old: updated_packet(old, ESTIMATE)) == data


def test_clearing_removes_the_estimate_fields_and_keeps_the_rest():
    data = _exported()
    assert _fields(replace_xmp(data, lambda old: updated_packet(old, {}))) == TECHNICAL


def test_a_jpeg_without_xmp_gets_one_with_only_the_estimate():
    data = replace_xmp(_plain_jpeg(), lambda old: updated_packet(old, ESTIMATE))
    assert _fields(data) == ESTIMATE


def test_a_long_unicode_description_survives():
    text = "Æbleskiver på Ærø, 祖父の誕生日 " * 20
    text = text[:500]
    data = replace_xmp(_exported(), lambda old: updated_packet(old, {"Description": text}))
    assert _fields(data)["Description"] == text


@pytest.mark.parametrize(
    "packet",
    [
        b"<x:xmpmeta xmlns:x='adobe:ns:meta/'><other/></x:xmpmeta>",
        _xmp_packet({}).replace(
            b"</rdf:RDF>", b"<rdf:Description><dc:x xmlns:dc='d'/></rdf:Description></rdf:RDF>"
        ),
        b"not xml",
    ],
)
def test_xmp_that_is_not_ours_is_refused(packet):
    data = replace_xmp(_plain_jpeg(), lambda _old: packet)
    with pytest.raises(RewriteRefused):
        replace_xmp(data, lambda old: updated_packet(old, ESTIMATE))


def test_two_xmp_segments_are_refused():
    once = _exported()
    segments, scan = jpeg_segments(once)
    xmp = next(s for s in segments if s[4:].startswith(XMP_HEADER))
    twice = b"\xff\xd8" + b"".join(segments) + xmp + scan
    with pytest.raises(RewriteRefused, match="more than one"):
        replace_xmp(twice, lambda old: updated_packet(old, ESTIMATE))


def test_an_oversized_packet_is_refused():
    with pytest.raises(ValueError, match="65533"):
        replace_xmp(_plain_jpeg(), lambda _old: b"x" * 70000)


def test_damaged_data_is_refused():
    with pytest.raises(RewriteRefused):
        jpeg_segments(b"\xff\xd8\xff\xe1\x00")
    with pytest.raises(RewriteRefused):
        jpeg_segments(b"GIF89a")


def _on_disk(tmp_path, mode=0o644):
    path = tmp_path / "001.jpg"
    data = _exported()
    path.write_bytes(data)
    path.chmod(mode)
    return path, hashlib.sha256(data).hexdigest(), len(data)


def test_rewriting_a_file_returns_its_new_receipt_and_keeps_the_mode(tmp_path):
    path, digest, size = _on_disk(tmp_path, 0o600)
    new_digest, new_size = rewrite_xmp_file(path, digest, size, {"PhotoDate": "1980"})
    data = path.read_bytes()
    assert (new_digest, new_size) == (hashlib.sha256(data).hexdigest(), len(data))
    assert _fields(data) == {**TECHNICAL, "PhotoDate": "1980"}
    assert path.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["001.jpg"]


def test_an_unchanged_file_is_not_written(tmp_path, monkeypatch):
    path, digest, size = _on_disk(tmp_path)
    monkeypatch.setattr(os, "replace", lambda *a: pytest.fail("rewrote an unchanged file"))
    assert rewrite_xmp_file(path, digest, size, ESTIMATE) == (digest, size)


@pytest.mark.parametrize("change", ["edited", "size"])
def test_a_file_the_owner_changed_is_never_overwritten(tmp_path, change):
    path, digest, size = _on_disk(tmp_path)
    if change == "edited":
        data = bytearray(path.read_bytes())
        data[-3] ^= 0xFF
        path.write_bytes(bytes(data))
    before = path.read_bytes()
    with pytest.raises(RewriteRefused, match="changed"):
        rewrite_xmp_file(path, digest, size + (change == "size"), ESTIMATE | {"PhotoDate": "1"})
    assert path.read_bytes() == before


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        rewrite_xmp_file(tmp_path / "gone.jpg", "0" * 64, 10, ESTIMATE)


def test_a_symlink_is_refused(tmp_path):
    path, digest, size = _on_disk(tmp_path)
    link = tmp_path / "link.jpg"
    link.symlink_to(path)
    with pytest.raises(OSError):
        rewrite_xmp_file(link, digest, size, {})


def test_a_crash_before_the_rename_leaves_the_old_file(tmp_path, monkeypatch):
    path, digest, size = _on_disk(tmp_path)
    before = path.read_bytes()

    def crash(*_args):
        raise OSError("power cut")

    monkeypatch.setattr(jpeg_rewrite.os, "replace", crash)
    with pytest.raises(OSError, match="power cut"):
        rewrite_xmp_file(path, digest, size, {"Description": "Fair"})
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["001.jpg"]


def test_a_file_that_already_has_the_fields_but_not_the_receipts_hash_is_refused(tmp_path):
    path, digest, size = _on_disk(tmp_path)
    new_digest, _ = rewrite_xmp_file(path, digest, size, {"PhotoDate": "1980"})
    # the receipt still holds the old digest (a crash between the rename and the receipt save,
    # or another program that wrote the same fields): the receipt is not renewed to match
    with pytest.raises(RewriteRefused, match="changed since"):
        rewrite_xmp_file(path, digest, size, {"PhotoDate": "1980"})
    assert hashlib.sha256(path.read_bytes()).hexdigest() == new_digest


def test_a_file_that_matches_its_receipt_and_is_current_is_left_alone(tmp_path):
    path, digest, size = _on_disk(tmp_path)
    new_digest, new_size = rewrite_xmp_file(path, digest, size, {"PhotoDate": "1980"})
    before = path.read_bytes()
    assert rewrite_xmp_file(path, new_digest, new_size, {"PhotoDate": "1980"}) == (
        new_digest,
        new_size,
    )
    assert path.read_bytes() == before


def test_a_failed_directory_sync_after_the_rename_is_not_fatal(tmp_path, monkeypatch):
    path, digest, size = _on_disk(tmp_path)

    def broken(*_args, **_kwargs):
        raise OSError("sync failed")

    monkeypatch.setattr(jpeg_rewrite, "_sync_directories", broken)
    new_digest, new_size = rewrite_xmp_file(path, digest, size, {"PhotoDate": "1980"})
    data = path.read_bytes()
    assert (new_digest, new_size) == (hashlib.sha256(data).hexdigest(), len(data))
    assert _fields(data)["PhotoDate"] == "1980"


def test_a_read_only_file_is_refused(tmp_path):
    path, digest, size = _on_disk(tmp_path, 0o444)
    before = path.read_bytes()
    with pytest.raises(RewriteRefused, match="read-only"):
        rewrite_xmp_file(path, digest, size, {"PhotoDate": "1980"})
    assert path.read_bytes() == before
