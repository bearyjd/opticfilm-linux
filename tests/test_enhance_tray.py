import dataclasses
import threading

import numpy as np
import pytest
from PIL import Image

from opticfilm_linux.batch import enhance as enhance_module
from opticfilm_linux.batch.enhance import (
    RawRemoved,
    enhance_tray,
    options_for,
    reenhance_slide,
)
from opticfilm_linux.batch.model import (
    BatchDefaults,
    Flag,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.model import HolderKind, RawSweep
from opticfilm_linux.pipeline.split import split_slides
from opticfilm_linux.sweepstore import save_sweep
from synthetic import make_sweep, truth_boxes


def _store(tmp_path, sweep, stage=TrayStage.SCANNED, mode="positive") -> BatchStore:
    store = BatchStore.create(tmp_path / "batch", name="T", defaults=BatchDefaults(dpi=600))
    save_sweep(sweep, store.sweep_dir(1, 1))
    tray = TrayRecord(number=1, stage=stage, mode=mode)
    store.update(lambda m: dataclasses.replace(m, trays=(tray,)))
    return store


def _tray(store: BatchStore) -> TrayRecord:
    return store.snapshot().trays[0]


def _set_slide(store: BatchStore, position: int, **changes) -> None:
    def change(manifest):
        tray = manifest.trays[0]
        slides = tuple(
            dataclasses.replace(s, **changes) if s.position == position else s for s in tray.slides
        )
        return dataclasses.replace(manifest, trays=(dataclasses.replace(tray, slides=slides),))

    store.update(change)


def test_enhance_tray_reaches_review_with_four_slides_and_previews(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600, dust=12))
    enhance_tray(store, 1)
    tray = _tray(store)
    assert tray.stage is TrayStage.REVIEW and len(tray.slides) == 4
    for position in range(1, 5):
        assert store.preview_path(1, position, "before").is_file()
        assert store.preview_path(1, position, "after").is_file()


def test_unflagged_slides_are_auto_accepted(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600, dust=12))
    enhance_tray(store, 1)
    assert [s.status for s in _tray(store).slides] == [SlideStatus.ACCEPTED] * 4
    assert all(not s.owner_decided for s in _tray(store).slides)


def test_dark_slide_is_flagged_and_needs_a_look(tmp_path):
    sweep = make_sweep(dpi=600)
    box = truth_boxes(600)[1]
    rgb = sweep.rgb.copy()
    rgb[box.y : box.y + box.h] = np.uint16(0.03 * 65535)
    store = _store(tmp_path, dataclasses.replace(sweep, rgb=rgb))
    enhance_tray(store, 1)
    dark = _tray(store).slides[1]
    assert dark.status is SlideStatus.NEEDS_LOOK and Flag.TOO_DARK in dark.flags
    assert _tray(store).slides[0].status is SlideStatus.ACCEPTED


def test_blank_window_becomes_blank_record_without_previews(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600, filled=(True, False, True, True)))
    enhance_tray(store, 1)
    slides = _tray(store).slides
    assert [s.status for s in slides][1] is SlideStatus.BLANK
    assert not store.preview_path(1, 2, "after").exists()
    assert store.preview_path(1, 3, "after").is_file()


def test_owner_decision_and_settings_survive_re_enhancement(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600))
    enhance_tray(store, 1)
    settings = SlideSettings(rotation=90, brightness_ev=0.5)
    _set_slide(store, 2, status=SlideStatus.NEEDS_LOOK, owner_decided=True, settings=settings)
    enhance_tray(store, 1)
    kept = _tray(store).slides[1]
    assert kept.status is SlideStatus.NEEDS_LOOK and kept.owner_decided
    assert kept.settings == settings


def test_reenhance_slide_applies_rotation_to_the_after_preview(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600))
    enhance_tray(store, 1)
    _set_slide(store, 1, settings=SlideSettings(rotation=90), owner_decided=True)
    reenhance_slide(store, 1, 1)
    with Image.open(store.preview_path(1, 1, "before")) as before:
        with Image.open(store.preview_path(1, 1, "after")) as after:
            assert after.size == (before.size[1], before.size[0])


def test_reenhance_slide_regenerates_the_preview_file(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600))
    enhance_tray(store, 1)
    path = store.preview_path(1, 1, "after")
    old = path.read_bytes()
    _set_slide(store, 1, settings=SlideSettings(brightness_ev=1.0))
    reenhance_slide(store, 1, 1)
    assert path.read_bytes() != old


def test_corrupt_sweep_fails_the_tray_with_a_message(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600))
    rgb_file = store.sweep_dir(1, 1) / "rgb.npy"
    rgb_file.write_bytes(rgb_file.read_bytes()[: rgb_file.stat().st_size // 2])
    enhance_tray(store, 1)
    tray = _tray(store)
    assert tray.stage is TrayStage.FAILED and tray.error


def test_unsplittable_sweep_fails_the_tray(tmp_path):
    empty = RawSweep(
        rgb=np.zeros((5339, 827, 3), np.uint16), ir=None, dpi=600, holder=HolderKind.SLIDE4
    )
    store = _store(tmp_path, empty)
    enhance_tray(store, 1)
    assert _tray(store).stage is TrayStage.FAILED and "slide openings" in (_tray(store).error or "")


def test_negative_mode_tray_reaches_review(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600), mode="negative")
    enhance_tray(store, 1)
    assert _tray(store).stage is TrayStage.REVIEW


def test_options_for_prefers_slide_overrides_and_falls_back():
    defaults = BatchDefaults(grain_strength=0.3, sharpen_amount=0.5, color_strength=1.0)
    settings = SlideSettings(rotation=180, brightness_ev=-1.0, grain_strength=0.0)
    options = options_for(defaults, "negative", settings)
    assert options.grain_strength == 0.0 and options.sharpen_amount == 0.5
    assert options.color_strength == 1.0 and options.mode == "negative"
    assert (options.rotation, options.brightness_ev) == (180, -1.0)
    assert options.remove_dust is True and options.skip_blank is True


def test_progress_callback_reports_each_slide(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600))
    calls = []
    enhance_tray(store, 1, on_progress=lambda done, total: calls.append((done, total)))
    assert calls == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_enhancing_a_tray_that_is_still_scanning_is_refused(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600), stage=TrayStage.SCANNING)
    with pytest.raises(BatchError, match="scanning"):
        enhance_tray(store, 1)


def test_enhancing_after_raw_was_removed_raises(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600))
    store.update(
        lambda m: dataclasses.replace(m, trays=(dataclasses.replace(m.trays[0], raw_removed=True),))
    )
    with pytest.raises(RawRemoved):
        enhance_tray(store, 1)
    with pytest.raises(RawRemoved):
        reenhance_slide(store, 1, 1)


def test_reenhance_of_an_unknown_slide_raises(tmp_path):
    store = _store(tmp_path, make_sweep(dpi=600))
    enhance_tray(store, 1)
    with pytest.raises(BatchError, match="slide 9"):
        reenhance_slide(store, 1, 9)
    assert SlideRecord(position=1).position == 1


def _gate_enhance(monkeypatch):
    """Make the first enhance_slide call wait, so a test can act in the middle of enhancement."""
    started, release = threading.Event(), threading.Event()
    real = enhance_module.enhance_slide
    first = threading.Lock()

    def gated(*args, **kwargs):
        if first.acquire(blocking=False):
            started.set()
            assert release.wait(60)
        return real(*args, **kwargs)

    monkeypatch.setattr(enhance_module, "enhance_slide", gated)
    return started, release


def _run_gated(target, *args):
    worker = threading.Thread(target=target, args=args)
    worker.start()
    return worker


def test_settings_changed_during_re_enhancement_are_not_lost(tmp_path, monkeypatch):
    store = _store(tmp_path, make_sweep(dpi=600))
    enhance_tray(store, 1)
    started, release = _gate_enhance(monkeypatch)
    worker = _run_gated(reenhance_slide, store, 1, 1)
    assert started.wait(60)
    newer = SlideSettings(brightness_ev=1.5)
    _set_slide(store, 1, settings=newer, owner_decided=True)
    release.set()
    worker.join(60)
    assert _tray(store).slides[0].settings == newer


def test_status_chosen_during_re_enhancement_is_kept(tmp_path, monkeypatch):
    store = _store(tmp_path, make_sweep(dpi=600))
    enhance_tray(store, 1)
    started, release = _gate_enhance(monkeypatch)
    worker = _run_gated(reenhance_slide, store, 1, 1)
    assert started.wait(60)
    _set_slide(store, 1, status=SlideStatus.NEEDS_LOOK, owner_decided=True)
    release.set()
    worker.join(60)
    kept = _tray(store).slides[0]
    assert kept.status is SlideStatus.NEEDS_LOOK and kept.owner_decided


def test_exported_mark_survives_a_stale_re_enhancement(tmp_path, monkeypatch):
    store = _store(tmp_path, make_sweep(dpi=600))
    enhance_tray(store, 1)
    started, release = _gate_enhance(monkeypatch)
    worker = _run_gated(reenhance_slide, store, 1, 1)
    assert started.wait(60)
    _set_slide(store, 1, exported=True)
    release.set()
    worker.join(60)
    assert _tray(store).slides[0].exported


def test_a_rescan_during_enhancement_discards_the_stale_results(tmp_path, monkeypatch):
    store = _store(tmp_path, make_sweep(dpi=600))
    started, release = _gate_enhance(monkeypatch)
    worker = _run_gated(enhance_tray, store, 1)
    assert started.wait(60)
    store.update_tray(1, sweep_version=2, slides=(), stage=TrayStage.SCANNING)
    release.set()
    worker.join(60)
    tray = _tray(store)
    assert tray.stage is TrayStage.SCANNING and tray.slides == () and tray.sweep_version == 2


def test_a_mode_change_during_enhancement_discards_the_stale_results(tmp_path, monkeypatch):
    store = _store(tmp_path, make_sweep(dpi=600))
    started, release = _gate_enhance(monkeypatch)
    worker = _run_gated(enhance_tray, store, 1)
    assert started.wait(60)
    store.update_tray(1, mode="negative")
    release.set()
    worker.join(60)
    assert _tray(store).slides == ()  # a newer enhancement job (for the new mode) will fill it


def test_dust_flag_sees_the_slides_raw_colour_image(tmp_path, monkeypatch):
    """The flag's mask must use the unrotated raw colour image, registered to the infrared."""
    seen = []
    real = enhance_module.compute_flags

    def spy(enhanced, ir, dpi, visible=None):
        seen.append(visible)
        return real(enhanced, ir, dpi, visible=visible)

    monkeypatch.setattr(enhance_module, "compute_flags", spy)
    sweep = make_sweep(dpi=600, dust=12)
    enhance_tray(_store(tmp_path, sweep), 1)
    raw = [slide.rgb for slide in split_slides(sweep)]
    assert len(seen) == 4
    assert all(any(np.array_equal(v, r) for r in raw) for v in seen)
