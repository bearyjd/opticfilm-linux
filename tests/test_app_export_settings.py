"""Batch export preferences are immutable and stay with the requested session."""

import os
import stat
import tempfile
import threading
from pathlib import Path

import pytest

from controller_helpers import make_controller, open_new
from opticfilm_linux.batch.store import BatchStore


def _drain(controller):
    controller._command.submit(lambda: None).result(10)
    controller._rebuild()


def _settings(state):
    return state.export_dir, state.export_format, state.jpeg_size


def test_batch_settings_survive_reopen_and_switch(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)
    a, b = tmp_path / "a", tmp_path / "b"
    out_a, out_b = tmp_path / "photos-a", tmp_path / "photos-b"
    try:
        open_new(c, a)
        c.set_export_settings(out_a, "jpeg", "3600")
        _drain(c)
        assert _settings(c.state()) == (str(out_a), "jpeg", "3600")
        c.new_batch(b, "B", "", None, 600, export_dir=str(out_b), export_format="png8")
        _drain(c)
        assert _settings(c.state()) == (str(out_b), "png8", "full")
        for directory, expected in (
            (a, (str(out_a), "jpeg", "3600")),
            (b, (str(out_b), "png8", "full")),
        ):
            c.open_batch(directory)
            _drain(c)
            assert _settings(c.state()) == expected
        with BatchStore.open(a, readonly=True) as store:
            assert store.snapshot().defaults.jpeg_size == "3600"
        c.close_batch()
        _drain(c)
        assert _settings(c.state()) == ("", "jpeg", "full")
    finally:
        c.shutdown()


@pytest.mark.parametrize("action", ["set_export_settings", "export"])
def test_queued_settings_remain_session_bound(sweep600_dir, tmp_path, action):
    c = make_controller(sweep600_dir)
    gate = threading.Event()
    try:
        open_new(c, tmp_path / "a")
        BatchStore.create(tmp_path / "b", "B").close()
        c._command.submit(lambda: gate.wait(10))
        c.open_batch(tmp_path / "b")
        getattr(c, action)(tmp_path / "a-photos", "jpeg", "3600")
        gate.set()
        _drain(c)
        assert c.state().batch_name == "B"
        assert _settings(c.state()) == ("", "jpeg", "full")
        assert not (tmp_path / "a-photos").exists()
    finally:
        gate.set()
        c.shutdown()


@pytest.mark.parametrize("action", ["set_export_settings", "export"])
@pytest.mark.parametrize("destination", ["self", "child", "symlink"])
def test_inside_batch_destination_is_rejected_before_persistence(
    sweep600_dir, tmp_path, action, destination
):
    c = make_controller(sweep600_dir)
    batch = tmp_path / "batch"
    try:
        open_new(c, batch)
        before = c._session.store.snapshot()
        link = tmp_path / "link"
        link.symlink_to(batch, target_is_directory=True)
        out = {"self": batch, "child": batch / "out", "symlink": link / "out"}[destination]
        getattr(c, action)(out, "jpeg", "3600")
        _drain(c)
        assert c.state().banner is not None
        assert "outside" in c.state().banner.detail
        assert c._session.store.snapshot() == before
    finally:
        c.shutdown()


@pytest.mark.parametrize("action", ["set_export_settings", "export"])
def test_original_readonly_request_cannot_write_after_reopen(sweep600_dir, tmp_path, action):
    c = make_controller(sweep600_dir)
    batch = tmp_path / "batch"
    gate = threading.Event()
    try:
        BatchStore.create(batch, "Readonly").close()
        c.open_batch(batch, readonly=True)
        _drain(c)
        c._command.submit(lambda: gate.wait(10))
        c.open_batch(batch)
        getattr(c, action)(tmp_path / "photos", "jpeg", "3600")
        gate.set()
        _drain(c)
        assert "read-only" in c.state().banner.detail
        assert _settings(c.state()) == ("", "jpeg", "full")
    finally:
        gate.set()
        c.shutdown()


@pytest.mark.parametrize("fmt,size", [("wrong", "full"), ("jpeg", "wrong")])
def test_invalid_settings_do_not_persist(sweep600_dir, tmp_path, fmt, size):
    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "batch")
        before = c._session.store.snapshot()
        c.set_export_settings(tmp_path / "photos", fmt, size)
        _drain(c)
        assert c.state().banner is not None
        assert c._session.store.snapshot() == before
    finally:
        c.shutdown()


def test_new_batch_rejects_internal_destination_without_creating(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)
    batch = tmp_path / "batch"
    try:
        c.new_batch(batch, "Invalid", "", None, 600, export_dir=str(batch / "photos"))
        _drain(c)
        assert c.state().banner is not None
        assert not batch.exists()
    finally:
        c.shutdown()


@pytest.mark.parametrize("action", ["set_export_settings", "export", "new_batch"])
@pytest.mark.parametrize(
    "destination", ["file", "file_parent", "device", "readonly", "readonly_parent"]
)
def test_unusable_destination_is_rejected_before_persistence(
    sweep600_dir, tmp_path, monkeypatch, action, destination
):
    c = make_controller(sweep600_dir)
    out = tmp_path / "photos"
    protected = None
    try:
        open_new(c, tmp_path / "batch")
        before = c._session.store.snapshot()
        if destination in ("file", "file_parent", "device"):
            out.write_text("keep this file")
            if destination == "file_parent":
                out = out / "nested"
            elif destination == "device":
                original_stat = Path.stat

                def device_stat(path, *args, **kwargs):
                    result = original_stat(path, *args, **kwargs)
                    if path == out:
                        return os.stat_result((stat.S_IFCHR | 0o666, *result[1:]))
                    return result

                monkeypatch.setattr(Path, "stat", device_stat)
            expected_error = "not a directory"
        else:
            out.mkdir()
            protected = out
            out.chmod(0o555)
            if destination == "readonly_parent":
                out = out / "nested" / "photos"
            expected_error = "not writable"

        if action == "new_batch":
            c.new_batch(tmp_path / "new-batch", "Invalid", "", None, 600, export_dir=str(out))
        else:
            getattr(c, action)(out, "jpeg", "3600")
        _drain(c)

        assert c.state().banner is not None
        assert expected_error in c.state().banner.detail
        assert c._session.store.snapshot() == before
        assert not (tmp_path / "new-batch").exists()
        if destination in ("file_parent", "readonly_parent"):
            assert not out.exists()
        elif destination in ("file", "device"):
            assert out.read_text() == "keep this file"
    finally:
        if protected is not None:
            protected.chmod(0o755)
        c.shutdown()


@pytest.mark.parametrize("failure", ["access", "probe"])
def test_destination_write_failure_is_rejected_before_persistence(
    sweep600_dir, tmp_path, monkeypatch, failure
):
    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "batch")
        before = c._session.store.snapshot()
        if failure == "access":
            monkeypatch.setattr(os, "access", lambda *args: False)
        else:

            def deny_probe(*args, **kwargs):
                raise PermissionError("write denied by filesystem")

            monkeypatch.setattr(tempfile, "TemporaryFile", deny_probe)
        c.set_export_settings(tmp_path / "photos", "jpeg", "full")
        _drain(c)
        assert c.state().banner is not None
        assert "not writable" in c.state().banner.detail
        assert c._session.store.snapshot() == before
        assert not (tmp_path / "photos").exists()
    finally:
        c.shutdown()


def test_malformed_destination_is_rejected_before_persistence(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "batch")
        before = c._session.store.snapshot()
        c.set_export_settings(tmp_path / "bad\0folder", "jpeg", "full")
        _drain(c)
        assert c.state().banner is not None
        assert "export folder" in c.state().banner.detail
        assert c._session.store.snapshot() == before
    finally:
        c.shutdown()


@pytest.mark.parametrize("nested", [False, True])
def test_broken_symlink_destination_is_rejected_before_persistence(sweep600_dir, tmp_path, nested):
    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "batch")
        before = c._session.store.snapshot()
        link = tmp_path / "broken-link"
        link.symlink_to(tmp_path / "missing", target_is_directory=True)
        out = link / "photos" if nested else link
        c.set_export_settings(out, "jpeg", "full")
        _drain(c)
        assert c.state().banner is not None
        assert "broken symbolic link" in c.state().banner.detail
        assert c._session.store.snapshot() == before
        assert not (tmp_path / "missing").exists()
    finally:
        c.shutdown()


@pytest.mark.parametrize("existing", [False, True])
def test_writable_destination_is_saved_without_creating_files(sweep600_dir, tmp_path, existing):
    c = make_controller(sweep600_dir)
    out = tmp_path / "photos" / "nested"
    if existing:
        out.mkdir(parents=True)
    try:
        open_new(c, tmp_path / "batch")
        c.set_export_settings(out, "jpeg", "3600")
        _drain(c)
        assert c.state().banner is None
        assert _settings(c.state()) == (str(out), "jpeg", "3600")
        if existing:
            assert list(out.iterdir()) == []
        else:
            assert not out.parent.exists()
    finally:
        c.shutdown()


def test_export_captures_settings_before_later_edit(sweep600_dir, tmp_path, monkeypatch):
    c = make_controller(sweep600_dir)
    gate = threading.Event()
    calls = []
    monkeypatch.setattr(
        "opticfilm_linux.app.controller.run_export", lambda *a, **kw: calls.append((a, kw))
    )
    try:
        open_new(c, tmp_path / "batch")
        c._jobs.submit(lambda: gate.wait(10))
        c.export(tmp_path / "first", "jpeg", "3600")
        _drain(c)
        assert _settings(c.state()) == (str(tmp_path / "first"), "jpeg", "3600")
        c.set_export_settings(tmp_path / "later", "png8", "full")
        _drain(c)
        assert _settings(c.state()) == (str(tmp_path / "later"), "png8", "full")
        gate.set()
        c._jobs.submit(lambda: None).result(10)
        assert calls[0][0][1:3] == (tmp_path / "first", "jpeg")
        assert calls[0][1]["jpeg_size"] == "3600"
    finally:
        gate.set()
        c.shutdown()
