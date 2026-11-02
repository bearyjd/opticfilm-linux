import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from opticfilm_linux.batch import export
from opticfilm_linux.batch.model import Flag, SlideStatus
from test_export_safety import _assert_raw_kept, _delivered
from test_export_safety import delivery as delivery


def test_interrupted_cleanup_rehashes_current_raw(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    store.update_tray(1, raw_removed=True, cleanup_sweeps=(1,))
    original = export.split_slides

    def altered(sweep):
        slides = original(sweep)
        return [dataclasses.replace(slides[0], rgb=np.zeros_like(slides[0].rgb))]

    monkeypatch.setattr(export, "split_slides", altered)
    with pytest.raises(export.CleanupRefused, match="current raw scan"):
        export.cleanup_raw(store, 1)
    assert store.sweep_dir(1, 1).exists()


def test_interrupted_cleanup_trusts_proof_once_current_is_gone(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    older = store.sweep_dir(1, 0)
    older.mkdir()
    export.shutil.rmtree(store.sweep_dir(1, 1))
    store.update_tray(1, raw_removed=True, cleanup_sweeps=(0, 1))
    monkeypatch.setattr(export, "load_sweep", lambda _: pytest.fail("current raw is gone"))
    export.cleanup_raw(store, 1)
    assert not older.exists()


def test_postcommit_refusal_undoes_its_first_tray_release(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, auto_export=True))
    )
    original = store.update

    def add_sweep(change):
        manifest = original(change)
        if manifest.trays[0].raw_removed:
            store.sweep_dir(1, 2).mkdir()
        return manifest

    monkeypatch.setattr(store, "update", add_sweep)
    with pytest.raises(export.CleanupRefused, match="changed meanwhile"):
        export.cleanup_raw(store, 1, release_first=True)
    manifest = store.snapshot()
    assert not manifest.first_tray_released
    assert manifest.released_render is None
    _assert_raw_kept(store)


def test_cleanup_checks_flags_added_after_auto_acceptance(delivery):
    store, _ = _delivered(delivery)
    slide = store.snapshot().trays[0].slides[0]
    store.update_tray(
        1, slides=(dataclasses.replace(slide, auto_accepted=True, flags=(Flag.TOO_DARK,)),)
    )
    with pytest.raises(export.CleanupRefused, match="owner review"):
        export.cleanup_raw(store, 1)
    _assert_raw_kept(store)


def test_release_requires_verified_first_tray(delivery):
    store, _ = delivery
    with pytest.raises(export.BatchError, match="not fully exported"):
        export.release_first_tray(store, 1)
    assert not store.snapshot().first_tray_released


def test_release_rejects_freed_first_tray_as_new_settings_evidence(delivery):
    store, _ = _delivered(delivery)
    export.cleanup_raw(store, 1)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, jpeg_size="3600"))
    )
    with pytest.raises(export.CleanupRefused, match="freed first tray"):
        export.release_first_tray(store, 1)
    assert not store.snapshot().first_tray_released


def test_release_checks_current_render_signature(delivery):
    store, _ = _delivered(delivery)
    store.update(
        lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, jpeg_size="3600"))
    )
    with pytest.raises(export.CleanupRefused, match="settings differ"):
        export.release_first_tray(store, 1)
    assert not store.snapshot().first_tray_released


def test_release_requires_live_flags_to_be_reviewed(delivery):
    store, _ = _delivered(delivery)
    slide = store.snapshot().trays[0].slides[0]
    store.update_tray(1, slides=(dataclasses.replace(slide, flags=(Flag.TOO_DARK,)),))
    with pytest.raises(export.CleanupRefused, match="owner review"):
        export.release_first_tray(store, 1)
    assert not store.snapshot().first_tray_released


def test_simultaneous_cleanup_serializes_and_counts_once(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    entered, competing = threading.Event(), threading.Event()
    proceed = threading.Event()
    lock = threading.Lock()
    original = export.shutil.rmtree
    calls = []

    class ObservedLock:
        def __enter__(self):
            if entered.is_set():
                competing.set()
            lock.acquire()

        def __exit__(self, *_):
            lock.release()

    def delete(path):
        calls.append(path)
        entered.set()
        assert proceed.wait(5)
        original(path)

    monkeypatch.setattr(export, "_CLEANUP_LOCK", ObservedLock())
    monkeypatch.setattr(export.shutil, "rmtree", delete)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(export.cleanup_raw, store, 1)
        assert entered.wait(5)
        second = pool.submit(export.cleanup_raw, store, 1)
        try:
            assert competing.wait(5)
        finally:
            proceed.set()
        assert first.result() > 0
        assert second.result() == 0
    assert len(calls) == 1


def test_release_rejects_all_blank_tray_without_jpeg_evidence(delivery):
    store, _ = _delivered(delivery)
    slide = store.snapshot().trays[0].slides[0]
    store.update_tray(1, slides=(dataclasses.replace(slide, status=SlideStatus.BLANK),))
    with pytest.raises(export.CleanupRefused, match="no verified JPEGs"):
        export.release_first_tray(store, 1)
    assert not store.snapshot().first_tray_released


def test_failed_postcommit_reset_cannot_turn_refusal_into_cleanup(delivery, monkeypatch):
    store, _ = _delivered(delivery)
    original_update, original_split = store.update, export.split_slides

    def altered(sweep):
        slides = original_split(sweep)
        return [dataclasses.replace(slides[0], rgb=np.zeros_like(slides[0].rgb))]

    def fill_disk(change):
        if store.snapshot().trays[0].raw_removed:
            raise OSError("no space for cleanup reset")
        manifest = original_update(change)
        if manifest.trays[0].raw_removed:
            monkeypatch.setattr(export, "split_slides", altered)
        return manifest

    monkeypatch.setattr(store, "update", fill_disk)
    with pytest.raises(OSError, match="no space"):
        export.cleanup_raw(store, 1)
    assert store.snapshot().trays[0].raw_removed
    monkeypatch.setattr(store, "update", original_update)
    with pytest.raises(export.CleanupRefused, match="current raw scan"):
        export.cleanup_raw(store, 1)
    assert store.sweep_dir(1, 1).exists()
