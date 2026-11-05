"""Hands-free export of a tray whose fourth opening was skipped (3 of 4 slides)."""

import dataclasses
from types import SimpleNamespace

import numpy as np
import pytest

from opticfilm_linux.app.auto import AutoPipeline
from opticfilm_linux.batch import export as exports
from opticfilm_linux.batch.model import SlideRecord, SlideStatus, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.model import Box, Slide

FULL = (1, 2, 3, 4)
THREE = (1, 2, 3)


def _tray(number: int, positions: tuple[int, ...]) -> TrayRecord:
    skipped = tuple(p for p in FULL if p not in positions)
    slides = tuple(
        SlideRecord(p, status=SlideStatus.SKIPPED) if p in skipped else SlideRecord(p) for p in FULL
    )
    return TrayRecord(number, TrayStage.REVIEW, slides=slides, skipped=skipped)


def _slide(position: int) -> Slide:
    rgb = np.full((30 + position, 40 + position, 3), 30000, dtype=np.uint16)
    return Slide(position - 1, rgb, None, Box(0, 0, 40 + position, 30 + position))


@pytest.fixture
def make_store(tmp_path, monkeypatch):
    opened = []

    def make(*layouts: tuple[int, ...]) -> BatchStore:
        store = BatchStore.create(tmp_path / "batch", name="Auto")
        defaults = dataclasses.replace(
            store.snapshot().defaults, auto_export=True, export_dir=str(tmp_path / "album")
        )
        trays = tuple(_tray(n, layout) for n, layout in enumerate(layouts, start=1))
        store.update(lambda m: dataclasses.replace(m, defaults=defaults, trays=trays))
        positions = {}
        for n, layout in enumerate(layouts, start=1):
            folder = store.sweep_dir(n, 1)
            folder.mkdir(parents=True)
            (folder / "raw").write_bytes(b"valuable raw")
            positions[folder] = layout
        monkeypatch.setattr(
            exports,
            "load_sweep",
            lambda path: SimpleNamespace(dpi=7200, positions=positions[path]),
        )
        monkeypatch.setattr(
            exports, "split_positions", lambda sweep: {p: _slide(p) for p in sweep.positions}
        )
        monkeypatch.setattr(exports, "enhance_slide", lambda slide, *_: slide.rgb)
        opened.append(store)
        return store

    yield make
    for store in opened:
        store.close()


def _album(store: BatchStore) -> list[str]:
    return sorted(p.name for p in (store.directory.parent / "album").glob("*.jpg"))


def test_a_full_first_tray_is_still_held_for_the_owner(make_store):
    store = make_store(FULL)
    auto = AutoPipeline(store)
    assert auto.arm(1)
    auto.run(1)
    tray = store.snapshot().trays[0]
    assert tray.auto == "held" and not tray.raw_removed
    assert len(_album(store)) == 4


def test_a_three_slide_first_tray_is_held_then_released_and_freed(make_store):
    store = make_store(THREE)
    auto = AutoPipeline(store)
    assert auto.arm(1)
    auto.run(1)
    tray = store.snapshot().trays[0]
    assert tray.auto == "held" and tray.stage is TrayStage.EXPORTED and not tray.raw_removed
    assert len(_album(store)) == 3
    auto.confirm_free_held(1)
    assert store.snapshot().first_tray_released
    assert store.snapshot().trays[0].raw_removed
    assert store.snapshot().trays[0].slides[3].status is SlideStatus.SKIPPED


def test_a_three_slide_later_tray_verifies_and_frees_its_raw(make_store):
    store = make_store(FULL, THREE)
    auto = AutoPipeline(store)
    auto.arm(1)
    auto.run(1)
    auto.confirm_free_held(1)
    assert auto.arm(2)
    auto.run(2)
    tray = store.snapshot().trays[1]
    assert tray.auto == "freed" and tray.raw_removed and not tray.auto_error
    assert not store.sweep_dir(2, 1).exists()
    assert len(_album(store)) == 7
    assert not tray.slides[3].exported and not tray.slides[3].exports
