"""An exported JPEG whose description or date is out of date is found by comparing signatures."""

import dataclasses

import pytest

from opticfilm_linux.batch import export, review
from opticfilm_linux.batch.meta_sync import (
    metadata_out_of_date,
    out_of_date_keys,
    update_exported_metadata,
)
from opticfilm_linux.batch.model import SlideStatus
from opticfilm_linux.provenance import _estimate_fields, estimate_signature
from test_export_safety import _delivered
from test_export_safety import delivery as delivery


def _stale(store):
    manifest = store.snapshot()
    tray = manifest.trays[0]
    return metadata_out_of_date(manifest, tray, tray.slides[0])


def _record(store):
    return store.snapshot().trays[0].slides[0].exports[-1]


def _describe_batch(store, text="Lake trip"):
    store.update(lambda m: dataclasses.replace(m, description=text))


def test_an_export_without_metadata_stores_no_signature_and_is_current(delivery):
    store, _ = _delivered(delivery)
    assert _record(store).meta_signature == ""
    assert not _stale(store)
    assert out_of_date_keys(store.snapshot()) == ()


def test_the_signature_is_taken_from_the_fields_written(delivery):
    _describe_batch(delivery[0])
    store, _ = _delivered(delivery)
    manifest = store.snapshot()
    tray = manifest.trays[0]
    fields = _estimate_fields(manifest, tray, tray.slides[0])
    assert fields["Description"] == "Lake trip"
    assert _record(store).meta_signature == estimate_signature(fields)
    assert len(_record(store).meta_signature) == 64
    assert not _stale(store)


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: review.edit_meta_many(s, [(1, 1)], {"description": "Lake"}),
        lambda s: review.edit_tray_meta(s, 1, {"date": "1980"}),
        lambda s: review.edit_batch_meta(s, {"description": "Later"}, scope="inheriting"),
        # "from now on" pins the old text on the tray: same words, but now from the tray level,
        # which the JPEG records as DescriptionSource, so the file is out of date too.
        lambda s: review.edit_batch_meta(s, {"description": "Later"}, scope="from_now_on"),
    ],
    ids=["slide", "tray", "batch", "batch-from-now-on"],
)
def test_every_kind_of_edit_marks_the_export_out_of_date_and_undo_clears_it(delivery, edit):
    _describe_batch(delivery[0])
    store, _ = _delivered(delivery)
    result = edit(store)
    assert _stale(store)
    assert out_of_date_keys(store.snapshot()) == ((1, 1),)
    review.restore_meta_many(store, result.before)
    assert not _stale(store)


def test_an_edit_made_while_the_tray_exports_is_caught(delivery, monkeypatch):
    """The hands-free race: export snapshots the manifest, the owner edits, then it marks."""
    store, _ = delivery
    _describe_batch(store)
    original = export.write_slide

    def edit_while_writing(*args, **kwargs):
        review.edit_tray_meta(store, 1, {"description": "Changed meanwhile"})
        return original(*args, **kwargs)

    monkeypatch.setattr(export, "write_slide", edit_while_writing)
    _delivered(delivery)
    slide = store.snapshot().trays[0].slides[0]
    assert slide.exported and slide.status is SlideStatus.ACCEPTED
    assert _stale(store)


def test_an_old_receipt_without_signature_is_out_of_date_only_with_metadata(delivery):
    store, _ = _delivered(delivery)
    assert not _stale(store)
    _describe_batch(store)
    assert _stale(store)


def test_only_accepted_exported_jpegs_can_be_out_of_date(delivery):
    store, _ = _delivered(delivery)
    _describe_batch(store)
    manifest = store.snapshot()
    tray = manifest.trays[0]
    slide = tray.slides[0]
    tiff = dataclasses.replace(slide.exports[-1], path="slide.tif")
    assert not metadata_out_of_date(manifest, tray, dataclasses.replace(slide, exports=(tiff,)))
    assert not metadata_out_of_date(manifest, tray, dataclasses.replace(slide, exported=False))
    blank = dataclasses.replace(slide, status=SlideStatus.BLANK)
    assert not metadata_out_of_date(manifest, tray, blank)
    assert not metadata_out_of_date(manifest, tray, dataclasses.replace(slide, exports=()))


def _jpeg(path):
    import numpy as np
    from PIL import Image

    with Image.open(path) as image:
        return np.asarray(image.convert("RGB")).copy(), image.info.get("icc_profile")


def _receipt_matches(store, path):
    import hashlib

    data = path.read_bytes()
    record = _record(store)
    slide = store.snapshot().trays[0].slides[0]
    assert (record.sha256, record.size_bytes) == (hashlib.sha256(data).hexdigest(), len(data))
    assert slide.export_sha256 == record.sha256


def test_updating_rewrites_the_xmp_and_renews_the_receipt(delivery):
    import numpy as np

    store, path = _delivered(delivery)
    before_pixels, before_icc = _jpeg(path)
    old = _record(store)
    review.edit_tray_meta(store, 1, {"description": "Fair", "date": "1981-06"})
    progress = []
    result = update_exported_metadata(store, on_progress=progress.append)
    assert result.updated == ((1, 1),) and result.failed == ()
    assert [(p.done, p.total) for p in progress] == [(1, 1)]
    assert b"<ofscan:Description>Fair</ofscan:Description>" in path.read_bytes()
    assert b"<ofscan:PhotoDate>1981-06</ofscan:PhotoDate>" in path.read_bytes()
    after_pixels, after_icc = _jpeg(path)
    assert np.array_equal(before_pixels, after_pixels) and before_icc == after_icc
    _receipt_matches(store, path)
    new = _record(store)
    assert dataclasses.replace(new, sha256=old.sha256, size_bytes=old.size_bytes) == (
        dataclasses.replace(old, meta_signature=new.meta_signature)
    )
    assert len(store.snapshot().trays[0].slides[0].exports) == 1
    assert not _stale(store)
    export.verify_tray_export(store, 1)  # a rewritten JPEG still verifies
    assert update_exported_metadata(store).updated == ()
    assert export.cleanup_raw(store, 1) > 0  # the renewed receipt passes the cleanup checks


def test_a_freshly_exported_file_is_rewritten_byte_identical_when_nothing_changed(delivery):
    from opticfilm_linux.jpeg_rewrite import replace_xmp, updated_packet

    _describe_batch(delivery[0])
    store, path = _delivered(delivery)
    manifest = store.snapshot()
    tray = manifest.trays[0]
    fields = _estimate_fields(manifest, tray, tray.slides[0])
    data = path.read_bytes()
    assert replace_xmp(data, lambda old: updated_packet(old, fields)) == data


def test_a_raw_freed_tray_can_still_be_updated(delivery):
    store, path = _delivered(delivery)
    assert export.cleanup_raw(store, 1) > 0
    review.edit_meta_many(store, [(1, 1)], {"description": "After freeing"})
    assert update_exported_metadata(store).updated == ((1, 1),)
    _receipt_matches(store, path)
    assert not _stale(store)


def test_an_edited_or_missing_file_is_reported_and_never_written(delivery):
    store, path = _delivered(delivery)
    review.edit_tray_meta(store, 1, {"date": "1980"})
    path.write_bytes(path.read_bytes() + b"owner edit")
    edited = path.read_bytes()
    old = _record(store)
    result = update_exported_metadata(store)
    assert result.updated == () and [k for k, _ in result.failed] == [(1, 1)]
    assert "changed since it was exported" in result.failed[0][1]
    assert path.read_bytes() == edited and _record(store) == old and _stale(store)
    path.unlink()
    (failure,) = update_exported_metadata(store).failed
    assert failure[0] == (1, 1)


def test_an_edit_during_the_update_leaves_the_slide_out_of_date(delivery, monkeypatch):
    from opticfilm_linux.batch import meta_sync

    store, _ = _delivered(delivery)
    review.edit_tray_meta(store, 1, {"date": "1980"})
    original = meta_sync.rewrite_xmp_file

    def edit_meanwhile(*args):
        review.edit_tray_meta(store, 1, {"date": "1982"})
        return original(*args)

    monkeypatch.setattr(meta_sync, "rewrite_xmp_file", edit_meanwhile)
    assert update_exported_metadata(store).updated == ((1, 1),)
    assert _stale(store)  # the file says 1980, the batch now says 1982


def test_a_cancelled_update_stops_before_the_next_file(delivery):
    import threading

    store, _ = _delivered(delivery)
    review.edit_tray_meta(store, 1, {"date": "1980"})
    cancel = threading.Event()
    cancel.set()
    assert update_exported_metadata(store, cancel=cancel).updated == ()
    assert _stale(store)


def test_a_section_layout_export_is_updated_in_place(delivery):
    from opticfilm_linux.batch.model import TrayLocation

    store, directory = delivery
    location = TrayLocation("family", 1, "a", 1, 4)
    store.update(
        lambda m: dataclasses.replace(
            m, trays=(dataclasses.replace(m.trays[0], location=location),)
        )
    )
    _, path = _delivered(delivery)
    assert path.parent != directory  # box-N/section/NNN.jpg
    review.edit_tray_meta(store, 1, {"description": "Box one"})
    assert update_exported_metadata(store).updated == ((1, 1),)
    assert b"Box one" in path.read_bytes()
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]
    _receipt_matches(store, path)
