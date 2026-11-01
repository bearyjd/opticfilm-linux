import dataclasses
import shutil
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from opticfilm_linux.batch import export
from opticfilm_linux.batch.model import (
    BatchDefaults,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    SlotOrientation,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchError, BatchStore
from test_export import _mark
from test_export import template as template


@pytest.fixture
def store(tmp_path, template):
    shutil.copytree(template, tmp_path / "b")
    with BatchStore.open(tmp_path / "b") as opened:
        yield opened


def test_clear_exported_changes_only_chosen_accepted_slides_atomically(store, monkeypatch):
    for number in (1, 2):
        store.update_tray(number, stage=TrayStage.EXPORTED)
        for position in range(1, 5):
            _mark(store, number, position, exported=True)
    _mark(store, 1, 2, status=SlideStatus.NEEDS_LOOK)
    before = store.snapshot()
    calls = []
    real = store.update

    def update(change):
        calls.append(True)
        return real(change)

    monkeypatch.setattr(store, "update", update)
    export.clear_exported(store, frozenset({(1, 1), (1, 2)}))
    after = store.snapshot()
    assert len(calls) == 1
    assert after.trays[0].stage is TrayStage.REVIEW
    assert after.trays[0].slides[0] == dataclasses.replace(
        before.trays[0].slides[0], exported=False
    )
    assert after.trays[0].slides[1:] == before.trays[0].slides[1:]
    assert after.trays[1] == before.trays[1]


def test_clear_exported_refuses_freed_raw_without_partial_changes(store):
    store.update_tray(2, stage=TrayStage.EXPORTED, raw_removed=True)
    _mark(store, 1, 1, exported=True)
    before = store.snapshot()
    with pytest.raises(BatchError, match="raw scans.*removed"):
        export.clear_exported(store, frozenset({(1, 1), (2, 1)}))
    assert store.snapshot() == before


def test_clear_exported_defaults_to_all_accepted_and_preserves_other_choices(store):
    _mark(store, 1, 1, status=SlideStatus.NEEDS_LOOK, exported=True)
    _mark(store, 1, 2, exported=True)
    export.clear_exported(store)
    assert store.snapshot().trays[0].slides[0].exported
    assert not store.snapshot().trays[0].slides[1].exported


def test_export_keys_filter_limits_writes_and_progress(store, tmp_path):
    updates = []
    result = export.export_batch(
        store, tmp_path / "out", keys=frozenset({(1, 2), (2, 3)}), on_progress=updates.append
    )
    assert len(result.written) == 2
    assert updates[-1].done == updates[-1].total == 2
    marked = {
        (t.number, s.position) for t in store.snapshot().trays for s in t.slides if s.exported
    }
    assert marked == {(1, 2), (2, 3)}


def test_empty_export_keys_never_means_all(store, tmp_path):
    result = export.export_batch(store, tmp_path / "out", keys=frozenset())
    assert result.written == ()


def test_reexport_keeps_old_numbered_files(store, tmp_path):
    first = export.export_batch(store, tmp_path / "out")
    original = {p: p.read_bytes() for p in first.written}
    export.clear_exported(store)
    second = export.export_batch(store, tmp_path / "out")
    assert len(second.written) == len(first.written) == 8
    assert set(first.written).isdisjoint(second.written)
    assert all(p.read_bytes() == content for p, content in original.items())


def test_export_preferences_changed_during_write_do_not_stale_the_tray(
    store, tmp_path, monkeypatch
):
    original = export.write_slide
    writes = []

    def write(*args, **kwargs):
        path = original(*args, **kwargs)
        writes.append(path)
        if len(writes) == 1:
            store.update(
                lambda manifest: dataclasses.replace(
                    manifest,
                    defaults=dataclasses.replace(
                        manifest.defaults,
                        export_dir=str(tmp_path / "later"),
                        export_format="png8",
                        jpeg_size="3600",
                    ),
                )
            )
        return path

    monkeypatch.setattr(export, "write_slide", write)
    result = export.export_batch(store, tmp_path / "original", "jpeg", jpeg_size="full")
    assert len(result.written) == 8
    assert all(slide.exported for tray in store.snapshot().trays for slide in tray.slides)
    assert all(path.parent == tmp_path / "original" and path.suffix == ".jpg" for path in writes)
    assert not (tmp_path / "later").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"rotation": 90},
        {"mirror": True},
        {"slots": (SlotOrientation(1, 90),)},
        {"color_strength": 0.5},
    ],
)
def test_render_defaults_changed_during_write_still_stale_the_tray(store, changes):
    before = store.snapshot()
    tray = before.trays[0]
    record = tray.slides[0]
    store.update(
        lambda manifest: dataclasses.replace(
            manifest,
            defaults=dataclasses.replace(manifest.defaults, **changes),
        )
    )
    outcome = export._mark_exported(
        store,
        tray.number,
        record.position,
        tray.sweep_version,
        tray.mode,
        record.settings,
        before.defaults,
    )
    assert outcome == "stale_tray"
    assert not store.snapshot().trays[0].slides[0].exported


@pytest.fixture
def jpeg_store(tmp_path, monkeypatch):
    rgb = np.random.default_rng(12).integers(0, 65536, (105, 107, 3), dtype=np.uint16)
    monkeypatch.setattr(export, "split_slides", lambda sweep: [rgb])

    def enhance(slide, options, dpi):
        return np.rot90(slide[:99, :101], options.rotation // 90)

    monkeypatch.setattr(export, "enhance_slide", enhance)
    with BatchStore.create(tmp_path / "jpeg-batch", name="JPEG") as opened:
        tray = TrayRecord(
            number=1,
            stage=TrayStage.REVIEW,
            slides=(SlideRecord(position=1, status=SlideStatus.ACCEPTED),),
        )
        opened.update(lambda manifest: dataclasses.replace(manifest, trays=(tray,)))
        yield opened


@pytest.mark.parametrize("dpi", [600, 3600, 7200])
@pytest.mark.parametrize("size", ["full", "3600"])
@pytest.mark.parametrize("rotation", [0, 90])
def test_jpeg_export_uses_sweep_dpi_after_crop_and_rotation(
    jpeg_store, tmp_path, monkeypatch, dpi, size, rotation
):
    monkeypatch.setattr(export, "load_sweep", lambda directory: SimpleNamespace(dpi=dpi))
    jpeg_store.update(
        lambda manifest: dataclasses.replace(manifest, defaults=BatchDefaults(dpi=3600))
    )
    _mark(jpeg_store, 1, 1, settings=SlideSettings(rotation=rotation))
    result = export.export_batch(jpeg_store, tmp_path / "out", "jpeg", jpeg_size=size)
    dimensions = (101, 99) if rotation == 0 else (99, 101)
    divisor = max(1, dpi / 3600) if size == "3600" else 1
    expected = tuple(int(side / divisor + 0.5) for side in dimensions)
    with Image.open(result.written[0]) as image:
        assert image.size == expected
        delivered = min(dpi, 3600) if size == "3600" else dpi
        assert image.getexif()[282] == image.getexif()[283] == delivered
        assert image.getexif()[296] == 2
        assert image.info["icc_profile"]
    assert jpeg_store.snapshot().trays[0].slides[0].exported


def test_jpeg_export_defaults_to_full_with_actual_sweep_dpi(jpeg_store, tmp_path, monkeypatch):
    monkeypatch.setattr(export, "load_sweep", lambda directory: SimpleNamespace(dpi=600))
    result = export.export_batch(jpeg_store, tmp_path / "out", "jpeg")
    with Image.open(result.written[0]) as image:
        assert image.size == (101, 99)
        assert image.getexif()[282] == image.getexif()[283] == 600
