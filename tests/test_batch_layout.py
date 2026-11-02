"""Position assignments survive review, exports and rescans."""

import json
from dataclasses import replace

import pytest

from opticfilm_linux.batch.layout import allocate_location, section_directory, slide_number
from opticfilm_linux.batch.manifest import from_json, to_json
from opticfilm_linux.batch.model import BatchManifest, TrayLocation, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchError


def test_positions_are_load_order_not_export_order(tmp_path):
    manifest = BatchManifest("Family", collection="family", box=2)
    first = allocate_location(manifest, 4)
    assert first == TrayLocation("family", 2, "a", 1, 4)
    tray = TrayRecord(1, TrayStage.EXPORTED, location=first)
    second = allocate_location(replace(manifest, trays=(tray,)), 4)
    assert slide_number(second, 3) == 7
    assert section_directory(tmp_path, second) == tmp_path / "family" / "box-2" / "a"


@pytest.mark.parametrize("size,capacity", [(10, 4), (140, 4), (10, 6)])
def test_partial_blank_and_rescan_preserve_positions(size, capacity):
    manifest = BatchManifest("Family", collection="family", section_size=size)
    trays = []
    for number in range(1, (size + capacity - 1) // capacity + 1):
        location = allocate_location(replace(manifest, trays=tuple(trays)), capacity)
        assert location.start == 1 + (number - 1) * capacity
        tray = TrayRecord(number, TrayStage.FAILED, location=location)
        assert replace(tray, sweep_version=2).location == location
        trays.append(tray)
    with pytest.raises(BatchError, match="section"):
        allocate_location(replace(manifest, trays=tuple(trays)), capacity)
    assert from_json(to_json(replace(manifest, trays=tuple(trays)))).trays == tuple(trays)


def test_layout_validation_and_legacy_roundtrip():
    legacy = BatchManifest("Old", trays=(TrayRecord(1, TrayStage.REVIEW),))
    data = json.loads(to_json(legacy))
    assert not {"collection", "box", "section", "section_size"} & data.keys()
    assert "location" not in data["trays"][0]
    assert from_json(json.dumps(data)) == legacy
    assert allocate_location(legacy, 4) is None
    for collection in ("../family", ".", "..", "a/b", "a\\b", "a\0b"):
        with pytest.raises(ValueError):
            TrayLocation(collection, 1, "a", 1, 4)
    for section in ("001", "g", "00", "100", "A", "a/b"):
        with pytest.raises(ValueError):
            BatchManifest("Bad", section=section)
    for box in (0, True, 1.0):
        with pytest.raises(ValueError):
            TrayLocation("family", box, "a", 1, 4)
    for size in (0, 1000, True):
        with pytest.raises(ValueError):
            BatchManifest("Bad", section_size=size)
    for position in (0, 5, True):
        with pytest.raises(ValueError):
            slide_number(TrayLocation("family", 1, "99", 1, 4), position)


@pytest.mark.parametrize("section,expected", [("a", "b"), ("01", "02"), ("98", "99"), ("e", "f")])
def test_section_rollover_and_old_tray_export(tmp_path, section, expected):
    from opticfilm_linux.batch.layout import advance_section
    from opticfilm_linux.batch.store import BatchStore

    store = BatchStore.create(tmp_path / "batch", "T", collection="family", box=2, section=section)
    location = allocate_location(store.snapshot(), 4)
    store.update(lambda m: replace(m, trays=(TrayRecord(1, TrayStage.REVIEW, location=location),)))
    advance_section(store, next_box=False)
    assert (store.snapshot().box, store.snapshot().section) == (2, expected)
    assert store.snapshot().trays[0].location == location
    assert allocate_location(store.snapshot(), 4).start == 1
    advance_section(store, next_box=True)
    assert (store.snapshot().box, store.snapshot().section) == (
        3,
        "a" if section.isalpha() else "01",
    )
    store.close()


@pytest.mark.parametrize("section", ["f", "99"])
def test_final_section_refuses_rollover_without_changing_manifest(tmp_path, section):
    from opticfilm_linux.batch.layout import advance_section
    from opticfilm_linux.batch.store import BatchStore

    store = BatchStore.create(tmp_path / "batch", "T", collection="family", section=section)
    before = store.snapshot()
    with pytest.raises(BatchError, match="[Ff]inish box"):
        advance_section(store, next_box=False)
    assert store.snapshot() == before
    store.close()
