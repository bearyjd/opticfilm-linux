"""DUST is advisory: flagged slides export automatically and are only listed."""

import dataclasses

import numpy as np
import pytest

from opticfilm_linux.app.state import _auto_fields
from opticfilm_linux.batch import export as exports
from opticfilm_linux.batch import review
from opticfilm_linux.batch.manifest import from_json, to_json
from opticfilm_linux.batch.model import (
    Flag,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
    advisory_only,
)
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.model import Box, Slide
from test_app_auto import pipeline


@pytest.fixture
def store(tmp_path, monkeypatch):
    opened = BatchStore.create(tmp_path / "batch", name="Advisory")
    defaults = dataclasses.replace(
        opened.snapshot().defaults, auto_export=True, export_dir=str(tmp_path / "album")
    )
    slides = tuple(SlideRecord(i, flags=(Flag.DUST,)) for i in (1, 2, 3))
    opened.update(
        lambda m: dataclasses.replace(
            m, defaults=defaults, trays=(TrayRecord(1, TrayStage.REVIEW, slides=slides),)
        )
    )
    folder = opened.sweep_dir(1, 1)
    folder.mkdir(parents=True)
    (folder / "raw").write_bytes(b"valuable raw")
    monkeypatch.setattr(exports, "load_sweep", lambda _: type("Sweep", (), {"dpi": 7200})())
    cut = {
        i: Slide(
            i - 1,
            np.full((30 + i, 40 + i, 3), 30000, dtype=np.uint16),
            None,
            Box(0, 0, 40 + i, 30 + i),
        )
        for i in (1, 2, 3)
    }
    monkeypatch.setattr(exports, "split_positions", lambda _: cut)
    monkeypatch.setattr(exports, "enhance_slide", lambda slide, *_: slide.rgb)
    yield opened
    opened.close()


def _set_flags(store, flags, status=SlideStatus.PENDING):
    tray = store.snapshot().trays[0]
    store.update_tray(
        1, slides=tuple(dataclasses.replace(s, flags=flags, status=status) for s in tray.slides)
    )


def _run(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    return auto


def test_advisory_only_is_dust_only():
    assert advisory_only((Flag.DUST,))
    assert not advisory_only(())
    assert not advisory_only((Flag.DUST, Flag.TOO_DARK))
    assert not advisory_only((Flag.CLIPPING,))


def test_dust_tray_exports_without_owner_and_is_listed(store):
    _run(store)
    tray = store.snapshot().trays[0]
    assert all(s.exported and s.status is SlideStatus.ACCEPTED for s in tray.slides)
    assert all(s.auto_accepted and not s.owner_decided for s in tray.slides)
    assert tray.auto == "held" and tray.auto_error == "Waiting for first-tray raw removal approval"
    assert tray.auto_flagged == (1, 2, 3)
    assert all(s.flags == (Flag.DUST,) for s in tray.slides)
    fields = _auto_fields(store.snapshot(), False)
    assert fields["auto_flagged"] == 3 and "3 flagged" in fields["auto_status"]
    assert fields["held_tray"] == 1  # one-time raw-free approval still offered


def test_first_tray_approval_frees_raw_and_flagged_list_survives(store):
    auto = _run(store)
    auto.confirm_free_held(1)
    tray = store.snapshot().trays[0]
    assert tray.raw_removed and store.snapshot().first_tray_released
    assert tray.auto_flagged == (1, 2, 3)
    assert all(s.flags == (Flag.DUST,) for s in tray.slides)


def test_keep_raw_keeps_raw_of_flagged_slides(store):
    store.update(
        lambda m: dataclasses.replace(
            m,
            first_tray_released=True,
            released_render=exports._release_defaults(m.defaults),
            defaults=dataclasses.replace(m.defaults, keep_raw=True),
        )
    )
    _run(store)
    tray = store.snapshot().trays[0]
    assert not tray.raw_removed and store.sweep_dir(1, 1).exists()
    assert all(s.exported for s in tray.slides)


@pytest.mark.parametrize("flags", [(Flag.TOO_DARK,), (Flag.DUST, Flag.CLIPPING)])
def test_other_flags_still_hold_the_tray(store, flags):
    _set_flags(store, flags, SlideStatus.NEEDS_LOOK)
    _run(store)
    tray = store.snapshot().trays[0]
    assert tray.auto == "blocked" and not any(s.exported for s in tray.slides)


def test_blank_frame_still_holds_tray(store):
    tray = store.snapshot().trays[0]
    blank = dataclasses.replace(tray.slides[0], status=SlideStatus.BLANK, flags=())
    store.update_tray(1, slides=(blank, *tray.slides[1:]))
    auto = _run(store)
    auto.confirm_free_held(1)
    tray = store.snapshot().trays[0]
    assert tray.auto != "freed" and not tray.raw_removed
    assert not store.snapshot().first_tray_released
    assert tray.slides[0].status is SlideStatus.BLANK


def test_existing_needs_look_dust_slides_are_accepted_automatically(store):
    _set_flags(store, (Flag.DUST,), SlideStatus.NEEDS_LOOK)
    _run(store)
    tray = store.snapshot().trays[0]
    assert all(s.exported and s.auto_accepted for s in tray.slides)


def test_flagged_slide_can_be_edited_and_marked(store):
    _run(store)
    review.apply_slide_settings(store, 1, 2, SlideSettings(rotation=90))
    slide = store.snapshot().trays[0].slides[1]
    assert slide.owner_decided and not slide.auto_accepted and not slide.exported
    assert slide.flags == (Flag.DUST,)
    review.set_slide_status(store, 1, 3, SlideStatus.NEEDS_LOOK)
    assert store.snapshot().trays[0].slides[2].status is SlideStatus.NEEDS_LOOK


def test_manifest_round_trip_has_no_new_field(store):
    _run(store)
    manifest = store.snapshot()
    assert from_json(to_json(manifest)) == manifest
