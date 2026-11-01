from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from opticfilm_linux.batch.export import clear_exported, export_batch
from opticfilm_linux.batch.manifest import from_json, to_json
from opticfilm_linux.batch.model import (
    SlideRecord,
    SlideStatus,
    TrayLocation,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.model import Slide
from opticfilm_linux.output import OutputSpec, write_slide


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = BatchStore.create(tmp_path / "batch", "Family")
    trays = tuple(
        TrayRecord(
            n,
            TrayStage.REVIEW,
            slides=tuple(
                SlideRecord(p, status=SlideStatus.BLANK if p == 2 else SlideStatus.ACCEPTED)
                for p in range(1, 5)
            ),
            location=TrayLocation("family", 1, "a", 1 + (n - 1) * 4, 4),
        )
        for n in (1, 2)
    )
    store.update(lambda m: replace(m, collection="family", trays=trays))
    monkeypatch.setattr(
        "opticfilm_linux.batch.export.load_sweep", lambda _: SimpleNamespace(dpi=7200)
    )
    monkeypatch.setattr(
        "opticfilm_linux.batch.export.split_slides",
        lambda _: [
            Slide(p, np.zeros((9, 11, 3), np.uint16), None, (0, 0, 11, 9)) for p in range(4)
        ],
    )
    monkeypatch.setattr(
        "opticfilm_linux.batch.export.enhance_slide", lambda *args: np.zeros((9, 11, 3), np.uint16)
    )
    yield store
    store.close()


def test_heirloom_positions_leave_blank_gaps(store, tmp_path):
    out = tmp_path / "out"
    second = export_batch(store, out, "jpeg", keys=frozenset((2, p) for p in (1, 3, 4)))
    first = export_batch(store, out, "jpeg", keys=frozenset((1, p) for p in (1, 3, 4)))
    assert {p.name for p in first.written} == {"001.jpg", "003.jpg", "004.jpg"}
    assert {p.name for p in second.written} == {"005.jpg", "007.jpg", "008.jpg"}
    assert first.written[0].parent == out / "family" / "box-1" / "a"
    record = store.snapshot().trays[0].slides[0].exports[-1]
    assert record.path == "family/box-1/a/001.jpg"
    assert (record.width, record.height, record.scan_dpi, record.delivered_dpi) == (
        11,
        9,
        7200,
        7200,
    )
    assert record.scan_token == "T001S1"
    assert from_json(to_json(store.snapshot())) == store.snapshot()


def test_reexport_collision_is_exclusive(store, tmp_path):
    out = tmp_path / "out"
    chosen = frozenset({(1, 1)})
    first = export_batch(store, out, "jpeg", keys=chosen)
    original = first.written[0].read_bytes()
    clear_exported(store, chosen)
    repeat = export_batch(store, out, "jpeg", keys=chosen)
    assert repeat.written[0].name == "141.jpg"
    assert first.written[0].read_bytes() == original
    assert len(store.snapshot().trays[0].slides[0].exports) == 2


def test_encode_failure_and_symlink_cannot_escape(store, tmp_path, monkeypatch):
    out = tmp_path / "out"
    section = out / "family" / "box-1" / "a"
    section.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    section.symlink_to(outside, target_is_directory=True)
    with pytest.raises(BatchError, match="symlink"):
        export_batch(store, out, "jpeg")
    assert list(outside.iterdir()) == []
    section.unlink()

    def broken(*args, **kwargs):
        raise RuntimeError("encoder failed")

    monkeypatch.setattr("opticfilm_linux.output._encode", broken)
    with pytest.raises(RuntimeError, match="encoder"):
        export_batch(store, out, "jpeg")
    assert not (section / "001.jpg").exists()
    assert not store.snapshot().trays[0].slides[0].exported


def test_partial_holder_accepted_beyond_section_size_is_refused(store, tmp_path):
    store.update(lambda m: replace(m, section_size=6))
    with pytest.raises(BatchError, match="section size"):
        export_batch(store, tmp_path / "out", "jpeg", keys=frozenset({(2, 3)}))


def test_position_collision_retry_and_exhaustion(tmp_path, monkeypatch):
    import os

    image = np.zeros((4, 4, 3), np.uint16)
    first = tmp_path / "001.jpg"

    original_open = os.open

    def competing(path, flags, *args, **kwargs):
        if path == first.name and flags & os.O_CREAT and not first.exists():
            first.write_bytes(b"competitor")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("opticfilm_linux.output.os.open", competing)
    path = write_slide(image, OutputSpec(tmp_path, "jpeg", position=1, sequence_stride=140))
    assert path.name == "141.jpg" and first.read_bytes() == b"competitor"
    for number in range(281, 1000, 140):
        (tmp_path / f"{number:03d}.jpg").write_bytes(b"keep")
    with pytest.raises(ValueError, match="999"):
        write_slide(image, OutputSpec(tmp_path, "jpeg", position=1, sequence_stride=140))


def test_closed_section_export_retains_saved_path(store, tmp_path):
    from opticfilm_linux.batch.layout import advance_section

    advance_section(store, next_box=False)
    assert store.snapshot().section == "b"
    result = export_batch(store, tmp_path / "out", "jpeg", keys=frozenset({(1, 1)}))
    assert result.written[0] == tmp_path / "out" / "family" / "box-1" / "a" / "001.jpg"
