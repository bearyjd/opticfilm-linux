import errno
import os
from pathlib import Path

import numpy as np
import pytest

from opticfilm_linux import output


def test_existing_export_root_does_not_sync_ancestors(tmp_path, monkeypatch):
    synced = []
    real_sync = os.fsync

    def sync(fd):
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if path.is_dir():
            synced.append(path)
            if path != tmp_path:
                raise PermissionError("export parent cannot be synced")
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    path = output.write_slide(
        np.zeros((4, 4, 3), dtype=np.uint16),
        output.OutputSpec(tmp_path, "jpeg"),
        on_written=lambda *_: None,
    )
    assert path.is_file()
    assert synced == [tmp_path]


def test_nested_export_sync_stops_at_root(tmp_path, monkeypatch):
    directory = tmp_path / "box-1" / "a"
    directory.mkdir(parents=True)
    synced = []
    monkeypatch.setattr(
        os, "fsync", lambda fd: synced.append(Path(os.readlink(f"/proc/self/fd/{fd}")))
    )
    output._sync_directories(directory, tmp_path)
    assert synced == [directory, directory.parent, tmp_path]


@pytest.mark.parametrize("error", [errno.EINVAL, errno.ENOTSUP])
def test_unsupported_directory_sync_allows_durable_file_write(tmp_path, monkeypatch, error):
    real_sync = os.fsync

    def sync(fd):
        if Path(os.readlink(f"/proc/self/fd/{fd}")).is_dir():
            raise OSError(error, "directory sync unsupported")
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    receipts = []
    path = output.write_slide(
        np.zeros((4, 4, 3), dtype=np.uint16),
        output.OutputSpec(tmp_path, "jpeg"),
        on_written=lambda *receipt: receipts.append(receipt),
    )
    assert path.is_file() and len(receipts) == 1
