"""An exported JPEG whose description or date is out of date is found by comparing signatures."""

import dataclasses

import pytest

from opticfilm_linux.batch import export, review
from opticfilm_linux.batch.meta_sync import metadata_out_of_date, out_of_date_keys
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
