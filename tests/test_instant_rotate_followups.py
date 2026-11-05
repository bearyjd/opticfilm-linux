"""Quick turns on batches without masters, bulk turns in the background, and their edges."""
# ruff: noqa: F811

import dataclasses
import logging
import threading

import numpy as np
import pytest
from PIL import Image

from opticfilm_linux.batch import enhance as enhance_module
from opticfilm_linux.batch import export as export_module
from opticfilm_linux.batch import runner as runner_module
from opticfilm_linux.batch.enhance import enhance_tray, reenhance_slide
from opticfilm_linux.batch.events import TrayFailed
from opticfilm_linux.batch.model import SlideStatus
from opticfilm_linux.batch.preview_transform import preview_transform, transform_preview
from opticfilm_linux.batch.previews import read_master, write_master, write_preview
from test_instant_rotate import (  # noqa: F401 - fixtures
    TIMEOUT,
    _assert_matches_full_render,
    _events,
    _keys,
    _orientation_of,
    _pixels,
    _shows_its_master_turned,
    _slide,
    _widen,
    renders,
    runner,
    store,
    template,
)


def _drop_masters(store):
    for path in store.directory.glob("trays/*/previews/*-master*.png"):
        path.unlink()


def _turn(store, runner, key, step=90):
    current = _slide(store, *key).settings
    runner.edit_slide(*key, dataclasses.replace(current, rotation=(current.rotation + step) % 360))


def _single_save(pixels, tmp_path):
    reference = tmp_path / "reference.jpg"
    write_preview(pixels, reference)
    with Image.open(reference) as image:
        return np.asarray(image.convert("RGB")).astype(np.float32)


# -- batches rendered before masters existed --------------------------------------------------


def test_a_batch_without_masters_turns_quickly_once_then_from_the_new_master(
    store, runner, renders, tmp_path
):
    _drop_masters(store)
    _turn(store, runner, (1, 2))
    assert renders == [] and not _slide(store, 1, 2).preview_stale  # no 19 s re-enhance
    master = read_master(store.master_path(1, 2))
    assert master is not None and master[1] == _orientation_of(store, 1, 2)
    assert np.array_equal(_pixels(store, 1, 2), _single_save(master[0], tmp_path))
    _turn(store, runner, (1, 2))
    assert renders == []
    pixels, rendered = master
    wanted = transform_preview(pixels, preview_transform(rendered, _orientation_of(store, 1, 2)))
    assert np.array_equal(_pixels(store, 1, 2), _single_save(wanted, tmp_path))
    _assert_matches_full_render(store, 1, 2)  # one extra JPEG generation, within tolerance


@pytest.mark.parametrize("damage", ["tag", "truncated"])
def test_an_unreadable_master_falls_back_to_the_jpeg_turn(store, runner, renders, damage):
    path = store.master_path(1, 3)
    if damage == "tag":
        pixels, _ = read_master(path)
        Image.fromarray(pixels).save(path, format="PNG")  # no orientation tag
    else:
        path.write_bytes(path.read_bytes()[:200])
    _turn(store, runner, (1, 3))
    assert renders == [] and not _slide(store, 1, 3).preview_stale
    assert read_master(path) is not None  # replaced by a good one from the turned picture


def test_bulk_turn_of_a_batch_without_masters_needs_no_re_enhance(store, runner, renders):
    _drop_masters(store)
    assert runner.set_batch_orientation(90, False) == ()
    assert runner.wait_idle(TIMEOUT)
    assert renders == []
    assert all(_shows_its_master_turned(store, key) for key in _keys(store))


# -- the master is optional -------------------------------------------------------------------


def test_a_failed_master_write_still_publishes_the_render(store, monkeypatch, caplog):
    def broken(rgb8, orientation, path):
        path.write_bytes(b"half")
        raise OSError("disk full")

    monkeypatch.setattr(enhance_module, "write_master", broken)
    _turn_settings = dataclasses.replace(_slide(store, 1, 2).settings, brightness_ev=0.5)
    store.update(
        lambda m: dataclasses.replace(
            m,
            trays=tuple(
                dataclasses.replace(
                    t,
                    slides=tuple(
                        dataclasses.replace(s, settings=_turn_settings, preview_stale=True)
                        if s.position == 2
                        else s
                        for s in t.slides
                    ),
                )
                if t.number == 1
                else t
                for t in m.trays
            ),
        )
    )
    with caplog.at_level(logging.WARNING, logger=enhance_module.log.name):
        reenhance_slide(store, 1, 2)
    assert not _slide(store, 1, 2).preview_stale
    assert not store.master_path(1, 2).exists()  # the old master would show the old render
    leftovers = [p.name for p in store.master_path(1, 2).parent.glob("02-*")]
    assert sorted(leftovers) == ["02-after.jpg", "02-before.jpg"]
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# -- disk accounting and rescans --------------------------------------------------------------


def test_freeing_space_counts_the_masters(store):
    store.update(
        lambda m: dataclasses.replace(
            m,
            trays=tuple(
                dataclasses.replace(
                    t,
                    slides=tuple(
                        dataclasses.replace(s, status=SlideStatus.ACCEPTED) for s in t.slides
                    ),
                )
                for t in m.trays
            ),
        )
    )
    keys = frozenset((1, s.position) for s in store.snapshot().trays[0].slides)
    export_module.export_batch(store, store.directory.parent / "out", "jpeg", keys=keys)
    masters = sum(p.stat().st_size for p in store.tray_dir(1).glob("previews/*-master*.png"))
    sweeps = sum(p.stat().st_size for p in store.tray_dir(1).glob("sweep-*/**/*") if p.is_file())
    assert masters > 0
    estimate = {t.number: t.bytes for t in export_module.freeable_trays(store)}
    assert estimate[1] == sweeps + masters
    assert export_module.cleanup_raw(store, 1) == sweeps + masters


def test_a_rescan_with_fewer_slides_drops_the_masters_of_missing_positions(store):
    stray = store.master_path(1, 9)
    write_master(np.zeros((4, 6, 3), np.uint8), (0, False), stray)
    enhance_tray(store, 1)
    assert not stray.exists()
    assert all(store.master_path(1, s.position).is_file() for s in store.snapshot().trays[0].slides)


# -- the background turn job ------------------------------------------------------------------


def test_a_crashed_turn_job_reports_no_tray_failure_and_re_renders(
    store, runner, renders, monkeypatch, caplog
):
    def crash(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_turn_group", crash)
    with caplog.at_level(logging.WARNING, logger=runner_module.log.name):
        runner.set_batch_orientation(90, False)
        assert runner.wait_idle(TIMEOUT)
    assert not any(isinstance(e, TrayFailed) for e in _events(runner))
    assert sorted(renders) == sorted(_keys(store))  # the normal re-render took over
    assert not any(s.preview_stale for t in store.snapshot().trays for s in t.slides)
    assert any("boom" in r.getMessage() or r.exc_info for r in caplog.records)


def test_overlapping_batch_turns_end_with_the_newest_orientation(store, runner, monkeypatch):
    gate, real = threading.Event(), runner._turn_group
    monkeypatch.setattr(runner, "_turn_group", lambda *a: gate.wait(TIMEOUT) and real(*a))
    try:
        runner.set_batch_orientation(90, False)
        runner.set_batch_orientation(180, True)
    finally:
        gate.set()
    assert runner.wait_idle(TIMEOUT)
    defaults = store.snapshot().defaults
    assert (defaults.rotation, defaults.mirror) == (180, True)
    assert not any(s.preview_stale for t in store.snapshot().trays for s in t.slides)
    assert all(_shows_its_master_turned(store, key) for key in _keys(store))


# -- bulk edits and undo in the background ----------------------------------------------------


def _count_updates(store, monkeypatch):
    caller, counts = threading.get_ident(), {"caller": 0, "worker": 0}
    real = store.update

    def counting(change):
        counts["caller" if threading.get_ident() == caller else "worker"] += 1
        return real(change)

    monkeypatch.setattr(store, "update", counting)
    return counts


def test_select_all_rotate_and_undo_return_at_once(store, runner, renders, monkeypatch):
    keys = _widen(store)
    original = {k: _slide(store, *k).settings for k in keys}
    counts = _count_updates(store, monkeypatch)
    gate, real = threading.Event(), runner._turn_group
    monkeypatch.setattr(runner, "_turn_group", lambda *a: gate.wait(TIMEOUT) and real(*a))
    try:
        result = runner.edit_many(keys, {"rotation": 90})
        assert counts["caller"] == 1 and not result.skipped  # one write, nothing turned yet
        assert all(_slide(store, *k).settings.rotation == 90 for k in keys)
        assert all(_slide(store, *k).preview_stale for k in keys)
        runner._enhance_pool.submit(lambda: None).result()
        assert renders == []  # slides waiting to be turned are not re-enhanced
    finally:
        gate.set()
    assert runner.wait_idle(TIMEOUT) and renders == []
    assert all(_shows_its_master_turned(store, key) for key in keys)
    gate.clear()
    try:
        runner.restore_many(result.before)
        assert counts["caller"] == 2
        assert all(_slide(store, *k).settings == original[k] for k in keys)
    finally:
        gate.set()
    assert runner.wait_idle(TIMEOUT) and renders == []
    assert not any(s.preview_stale for t in store.snapshot().trays for s in t.slides)
    assert all(_shows_its_master_turned(store, key) for key in keys)
