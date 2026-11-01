import dataclasses
import os
import threading

import pytest

from opticfilm_linux.batch.manifest import ManifestFormatError, from_json
from opticfilm_linux.batch.model import BatchDefaults, BatchManifest, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchError, BatchStore


def _store(tmp_path) -> BatchStore:
    return BatchStore.create(tmp_path / "batch", name="Trips", album="Family", date="1975-06")


def _add_tray(manifest: BatchManifest, stage: TrayStage = TrayStage.SCANNED) -> BatchManifest:
    tray = TrayRecord(number=len(manifest.trays) + 1, stage=stage)
    return dataclasses.replace(manifest, trays=manifest.trays + (tray,))


def test_create_then_open_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.update(_add_tray)
    store.close()
    reopened = BatchStore.open(tmp_path / "batch")
    assert reopened.snapshot() == store.snapshot()
    assert reopened.snapshot().album == "Family"


def test_create_twice_raises_batcherror(tmp_path):
    _store(tmp_path)
    with pytest.raises(BatchError, match="already"):
        _store(tmp_path)


def test_open_missing_raises_batcherror(tmp_path):
    with pytest.raises(BatchError, match="not found"):
        BatchStore.open(tmp_path / "nope")


def test_open_corrupt_manifest_raises_manifestformaterror(tmp_path):
    _store(tmp_path).close()
    (tmp_path / "batch" / "batch.json").write_text("{garbage")
    with pytest.raises(ManifestFormatError):
        BatchStore.open(tmp_path / "batch")


def test_update_writes_atomically_when_replace_fails(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.update(_add_tray)
    before = store.snapshot()

    def broken_replace(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr("opticfilm_linux.batch.store.os.replace", broken_replace)
    with pytest.raises(OSError):
        store.update(_add_tray)
    assert store.snapshot() == before
    on_disk = from_json((tmp_path / "batch" / "batch.json").read_text())
    assert on_disk == before
    assert not (tmp_path / "batch" / "batch.json.tmp").exists()


def test_update_is_not_applied_when_change_raises(tmp_path):
    store = _store(tmp_path)

    def failing(_manifest):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        store.update(failing)
    assert store.snapshot().trays == ()


def test_concurrent_updates_lose_nothing(tmp_path):
    store = _store(tmp_path)

    def worker():
        for _ in range(50):
            store.update(_add_tray)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(store.snapshot().trays) == 400
    assert len(from_json((tmp_path / "batch" / "batch.json").read_text()).trays) == 400


def test_path_helpers_format(tmp_path):
    store = _store(tmp_path)
    root = tmp_path / "batch"
    assert store.directory == root
    assert store.tray_dir(1) == root / "trays" / "0001"
    assert store.sweep_dir(1, 2) == root / "trays" / "0001" / "sweep-2"
    assert store.sweep_tmp_dir(1, 2) == root / "trays" / "0001" / "sweep-2.tmp"
    assert (
        store.preview_path(3, 2, "after") == root / "trays" / "0003" / "previews" / "02-after.jpg"
    )
    assert store.preview_path(3, 2, "before").name == "02-before.jpg"


def test_recover_interrupted_scan_marks_failed_and_removes_tmp(tmp_path):
    store = _store(tmp_path)
    store.update(lambda m: _add_tray(m, TrayStage.SCANNING))
    store.update(lambda m: _add_tray(m, TrayStage.REVIEW))
    tmp = store.sweep_tmp_dir(1, 1)
    tmp.mkdir(parents=True)
    (tmp / "rgb.npy").write_bytes(b"half a sweep")
    finished = store.sweep_dir(2, 1)
    finished.mkdir(parents=True)
    (finished / "rgb.npy").write_bytes(b"complete")
    store.recover_interrupted()
    trays = store.snapshot().trays
    assert (
        trays[0].stage is TrayStage.FAILED
        and trays[0].error == "interrupted: needs power-cycle + reload + rescan"
    )
    assert not tmp.exists()
    assert (finished / "rgb.npy").read_bytes() == b"complete"
    assert trays[1].stage is TrayStage.REVIEW


def test_recover_enhancing_becomes_scanned_and_is_returned(tmp_path):
    store = _store(tmp_path)
    store.update(lambda m: _add_tray(m, TrayStage.ENHANCING))
    assert store.recover_interrupted() == (1,)
    assert store.snapshot().trays[0].stage is TrayStage.SCANNED


def test_recover_returns_scanned_trays_in_order(tmp_path):
    store = _store(tmp_path)
    for stage in (TrayStage.SCANNED, TrayStage.REVIEW, TrayStage.ENHANCING, TrayStage.SCANNED):
        store.update(lambda m, s=stage: _add_tray(m, s))
    assert store.recover_interrupted() == (1, 3, 4)


def test_update_tray_changes_only_the_named_tray(tmp_path):
    store = _store(tmp_path)
    store.update(_add_tray)
    store.update(_add_tray)
    store.update_tray(2, stage=TrayStage.REVIEW, error="x")
    trays = store.snapshot().trays
    assert trays[0].stage is TrayStage.SCANNED and trays[0].error is None
    assert trays[1].stage is TrayStage.REVIEW and trays[1].error == "x"


def test_update_tray_unknown_number_raises_batcherror(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(BatchError, match="no tray 3"):
        store.update_tray(3, stage=TrayStage.REVIEW)


def test_update_that_changes_nothing_writes_nothing(tmp_path, monkeypatch):
    store = _store(tmp_path)
    calls = []
    real = os.replace
    monkeypatch.setattr(
        "opticfilm_linux.batch.store.os.replace", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    store.update(lambda manifest: manifest)
    assert calls == []


def test_update_tray_if_applies_only_when_the_condition_holds(tmp_path):
    store = _store(tmp_path)
    store.update(_add_tray)
    assert store.update_tray_if(1, lambda t: t.stage is TrayStage.SCANNED, stage=TrayStage.REVIEW)
    assert not store.update_tray_if(
        1, lambda t: t.stage is TrayStage.SCANNED, stage=TrayStage.FAILED
    )
    assert store.snapshot().trays[0].stage is TrayStage.REVIEW


def test_update_tray_if_unknown_tray_raises(tmp_path):
    with pytest.raises(BatchError, match="no tray 2"):
        _store(tmp_path).update_tray_if(2, lambda t: True, stage=TrayStage.REVIEW)


def test_second_writer_is_refused_while_a_batch_is_open(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(BatchError, match="in use"):
        BatchStore.open(tmp_path / "batch")
    store.close()
    BatchStore.open(tmp_path / "batch").close()


def test_read_only_open_works_beside_a_writer_and_sees_the_latest(tmp_path):
    store = _store(tmp_path)
    store.update(_add_tray)
    reader = BatchStore.open(tmp_path / "batch", readonly=True)
    assert len(reader.snapshot().trays) == 1
    store.update(_add_tray)
    assert len(BatchStore.open(tmp_path / "batch", readonly=True).snapshot().trays) == 2
    with pytest.raises(BatchError, match="read-only"):
        reader.update(_add_tray)
    with pytest.raises(BatchError, match="read-only"):
        reader.recover_interrupted()


def test_context_manager_releases_the_lock(tmp_path):
    with _store(tmp_path):
        pass
    BatchStore.open(tmp_path / "batch").close()


@pytest.mark.parametrize("dpi", [7200, 600])
def test_create_reopen_preserves_new_and_explicit_dpi(tmp_path, dpi):
    defaults = None if dpi == 7200 else BatchDefaults(dpi=dpi)
    directory = tmp_path / "batch"
    store = BatchStore.create(directory, name="Trips", defaults=defaults)
    assert store.snapshot().defaults.dpi == dpi
    store.close()
    reopened = BatchStore.open(directory)
    try:
        assert reopened.snapshot().defaults.dpi == dpi
    finally:
        reopened.close()
