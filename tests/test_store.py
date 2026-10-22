import dataclasses
import threading

import pytest

from opticfilm_linux.batch.manifest import ManifestFormatError, from_json
from opticfilm_linux.batch.model import BatchManifest, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchError, BatchStore


def _store(tmp_path) -> BatchStore:
    return BatchStore.create(tmp_path / "batch", name="Trips", album="Family", date="1975-06")


def _add_tray(manifest: BatchManifest, stage: TrayStage = TrayStage.SCANNED) -> BatchManifest:
    tray = TrayRecord(number=len(manifest.trays) + 1, stage=stage)
    return dataclasses.replace(manifest, trays=manifest.trays + (tray,))


def test_create_then_open_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.update(_add_tray)
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
    _store(tmp_path)
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
    assert trays[0].stage is TrayStage.FAILED and trays[0].error == "scan was interrupted"
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
