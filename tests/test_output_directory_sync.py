import errno
import os
from pathlib import Path

import numpy as np
import pytest

from opticfilm_linux import output


def test_existing_export_root_syncs_ancestors(tmp_path, monkeypatch):
    synced = []
    real_sync = os.fsync

    def sync(fd):
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if path.is_dir():
            synced.append(path)
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    path = output.write_slide(
        np.zeros((4, 4, 3), dtype=np.uint16),
        output.OutputSpec(tmp_path, "jpeg"),
        on_written=lambda *_: None,
    )
    assert path.is_file()
    assert synced == [tmp_path, *tmp_path.parents]


def test_nested_export_sync_includes_root_ancestors(tmp_path, monkeypatch):
    directory = tmp_path / "box-1" / "a"
    directory.mkdir(parents=True)
    synced = []
    monkeypatch.setattr(
        os, "fsync", lambda fd: synced.append(Path(os.readlink(f"/proc/self/fd/{fd}")))
    )
    output._sync_directories(directory, tmp_path)
    assert synced == [directory, *directory.parents]


@pytest.mark.parametrize("error", [errno.EINVAL, errno.ENOTSUP])
@pytest.mark.parametrize("nested", [False, True])
def test_unsupported_directory_sync_rejects_receipt(tmp_path, monkeypatch, error, nested):
    real_sync = os.fsync

    def sync(fd):
        if Path(os.readlink(f"/proc/self/fd/{fd}")).is_dir():
            raise OSError(error, "directory sync unsupported")
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    receipts = []
    directory = tmp_path / "new" / "export" if nested else tmp_path
    with pytest.raises(OSError, match="cannot guarantee a durable export") as raised:
        output.write_slide(
            np.zeros((4, 4, 3), dtype=np.uint16),
            output.OutputSpec(directory, "jpeg"),
            on_written=lambda *receipt: receipts.append(receipt),
        )
    assert raised.value.errno == error
    assert receipts == []
    assert not list(tmp_path.rglob("*.jpg"))


def test_directory_sync_failure_remains_required_on_retry(tmp_path, monkeypatch):
    directory = tmp_path / "export"
    real_sync = os.fsync
    failing = True
    receipts = []

    def sync(fd):
        if Path(os.readlink(f"/proc/self/fd/{fd}")) == tmp_path and failing:
            raise OSError(errno.EIO, "parent directory sync failed")
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    spec = output.OutputSpec(directory, "jpeg", export_root=directory)
    image = np.zeros((4, 4, 3), dtype=np.uint16)
    for _ in range(2):
        with pytest.raises(OSError, match="parent directory sync failed"):
            output.write_slide(image, spec, on_written=lambda *args: receipts.append(args))
        assert directory.is_dir()
        assert list(directory.iterdir()) == []
        assert receipts == []
    failing = False
    path = output.write_slide(image, spec, on_written=lambda *args: receipts.append(args))
    assert path.is_file() and len(receipts) == 1
