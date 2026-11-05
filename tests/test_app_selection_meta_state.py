"""The selection summary shows common-or-mixed metadata and the level each field comes from."""

from opticfilm_linux.app.selection import Selection
from opticfilm_linux.app.state import selection_fields
from opticfilm_linux.batch.metadata import MetaOverride
from opticfilm_linux.batch.model import BatchManifest, SlideRecord, TrayRecord, TrayStage


def _manifest(**batch):
    trays = (
        TrayRecord(
            1,
            TrayStage.REVIEW,
            slides=(SlideRecord(1), SlideRecord(2, meta=MetaOverride(description="own"))),
        ),
        TrayRecord(
            2, TrayStage.REVIEW, slides=(SlideRecord(1),), meta=MetaOverride(date="1980-05")
        ),
    )
    return BatchManifest(name="m", trays=trays, **batch)


def _summary(manifest, *keys):
    return selection_fields(manifest, Selection(frozenset(keys)))["selection_summary"]


def test_common_values_and_levels():
    summary = _summary(_manifest(description="Lake", date="1978"), (1, 1), (2, 1))
    assert dict(summary.meta) == {"description": "Lake", "date": None}
    assert dict(summary.meta_levels) == {"description": "batch", "date": None}
    one = _summary(_manifest(description="Lake", date="1978"), (2, 1))
    assert dict(one.meta) == {"description": "Lake", "date": "1980-05"}
    assert dict(one.meta_levels) == {"description": "batch", "date": "tray"}


def test_mixed_description_and_blank_date_are_distinct():
    summary = _summary(_manifest(description="Lake"), (1, 1), (1, 2))
    assert dict(summary.meta) == {"description": None, "date": ""}
    assert dict(summary.meta_levels) == {"description": None, "date": ""}


def test_no_selection_shows_the_batch_default_and_batch_values_are_always_there():
    manifest = _manifest(description="Lake", date="1978-07-14")
    summary = _summary(manifest)
    assert dict(summary.meta) == {"description": "Lake", "date": "1978-07"}
    assert dict(summary.meta_levels) == {"description": "batch", "date": "batch"}
    assert summary.meta_note == "day dropped from 1978-07-14"
    assert dict(_summary(manifest, (2, 1)).batch_meta) == dict(summary.meta)
    assert dict(_summary(None).meta) == {}


def test_meta_scope_tells_the_strip_what_it_would_edit():
    manifest = _manifest()
    assert _summary(manifest, (1, 1)).meta_scope == "slides"
    assert _summary(manifest).meta_scope == "batch"
    tray_only = selection_fields(manifest, Selection(trays=frozenset({2})))["selection_summary"]
    assert tray_only.meta_scope == "tray"
