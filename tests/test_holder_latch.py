"""Holder latch knowledge changes only after conclusive holder commands."""

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from opticfilm_linux.scanner import holder_latch as latch


@pytest.mark.parametrize("success, expected", [(True, "LATCHED"), (False, "UNKNOWN")])
def test_load_transition(success, expected):
    assert latch.after_load(success) is getattr(latch.HolderLatch, expected)


@pytest.mark.parametrize("outcome", ["ejected", "ready", "idle", "", None])
def test_eject_requires_confirmed_outcome(outcome):
    expected = latch.HolderLatch.RELEASED if outcome == "ejected" else latch.HolderLatch.UNKNOWN
    assert latch.after_eject(outcome) is expected


def test_invalidating_knowledge_is_unknown():
    assert latch.invalidate() is latch.HolderLatch.UNKNOWN


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path / "opticfilm-linux" / "holder-latch.json"


@pytest.mark.parametrize("value", list(latch.HolderLatch))
def test_round_trip_with_same_usb_identity(state_path, value):
    latch.save_latch(2, 5, value)
    assert latch.load_latch(2, 5) is value
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert list(state_path.parent.iterdir()) == [state_path]


@pytest.mark.parametrize("bus, address", [(2, 6), (3, 5), (3, 6)])
def test_different_usb_identity_never_restores_knowledge(state_path, bus, address):
    latch.save_latch(2, 5, latch.HolderLatch.LATCHED)
    assert latch.load_latch(bus, address) is latch.HolderLatch.UNKNOWN


@pytest.mark.parametrize("content", [None, "{broken", "[]", "null", "{}", '{"state": "other"}'])
def test_missing_or_corrupt_state_is_unknown(state_path, content):
    if content is not None:
        state_path.parent.mkdir()
        state_path.write_text(content)
    assert latch.load_latch(2, 5) is latch.HolderLatch.UNKNOWN


def test_relative_state_home_uses_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "relative")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert latch.latch_state_path() == tmp_path / ".local/state/opticfilm-linux/holder-latch.json"


def test_failed_atomic_replace_preserves_previous_record_and_removes_temp(state_path, monkeypatch):
    latch.save_latch(2, 5, latch.HolderLatch.LATCHED)

    def fail(*args):
        raise OSError("read-only")

    monkeypatch.setattr(os, "replace", fail)
    latch.save_latch(2, 5, latch.HolderLatch.RELEASED)
    assert latch.load_latch(2, 5) is latch.HolderLatch.LATCHED
    assert list(state_path.parent.iterdir()) == [state_path]


def test_write_is_atomic_and_private_before_rename(state_path, monkeypatch):
    replace = os.replace
    calls = []

    def check(source, destination):
        source = Path(source)
        assert source.parent == state_path.parent
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        assert json.loads(source.read_text())["state"] == "latched"
        calls.append(destination)
        replace(source, destination)

    monkeypatch.setattr(os, "replace", check)
    latch.save_latch(2, 5, latch.HolderLatch.LATCHED)
    assert calls == [state_path]


def test_inaccessible_state_never_raises(state_path):
    state_path.parent.write_text("not a directory")
    latch.save_latch(2, 5, latch.HolderLatch.LATCHED)
    assert latch.load_latch(2, 5) is latch.HolderLatch.UNKNOWN


def test_missing_home_never_raises(monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    def fail():
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "home", fail)
    latch.save_latch(2, 5, latch.HolderLatch.LATCHED)
    assert latch.load_latch(2, 5) is latch.HolderLatch.UNKNOWN


@pytest.fixture
def valid_record(state_path):
    latch.save_latch(1, 2, latch.HolderLatch.LATCHED)
    return state_path


def test_symlink_never_restores_known_state(valid_record):
    target = valid_record.with_suffix(".target")
    valid_record.rename(target)
    valid_record.symlink_to(target)
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN


@pytest.mark.parametrize("mode", [0o644, 0o400, 0o700, 0o1600])
def test_only_private_mode_is_accepted(valid_record, mode):
    valid_record.chmod(mode)
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN


@pytest.mark.parametrize(
    "field, value",
    [("st_uid", -1), ("st_mode", stat.S_IFIFO | 0o600), ("st_mode", stat.S_IFREG | 0o644)],
)
def test_only_owned_regular_files_are_accepted(valid_record, monkeypatch, field, value):
    real_fstat = os.fstat

    def changed(descriptor):
        metadata = real_fstat(descriptor)
        fields = {key: getattr(metadata, key) for key in ("st_uid", "st_mode", "st_size")}
        fields[field] = value
        return SimpleNamespace(**fields)

    monkeypatch.setattr(os, "fstat", changed)
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN


def test_open_is_nonblocking_and_does_not_follow_symlinks(valid_record, monkeypatch):
    real_open = os.open
    flags_seen = []

    def checked(path, flags, *args, **kwargs):
        flags_seen.append(flags)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", checked)
    assert latch.load_latch(1, 2) is latch.HolderLatch.LATCHED
    assert len(flags_seen) == 1
    assert flags_seen[0] & os.O_NOFOLLOW
    assert flags_seen[0] & os.O_NONBLOCK


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-8-sig"])
def test_only_strict_utf8_without_bom_is_accepted(valid_record, encoding):
    record = valid_record.read_text()
    valid_record.write_bytes(record.encode(encoding))
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN


@pytest.mark.parametrize(
    "size, expected", [(65535, "LATCHED"), (65536, "UNKNOWN"), (65537, "UNKNOWN")]
)
def test_read_requires_eof_within_64_kib(valid_record, size, expected):
    record = valid_record.read_bytes()
    valid_record.write_bytes(record + b" " * (size - len(record)))
    assert latch.load_latch(1, 2) is getattr(latch.HolderLatch, expected)


@pytest.mark.parametrize("field", ["bus", "address"])
@pytest.mark.parametrize("value", [True, 1.0, False, 2.0, -1, 256, "1", None])
def test_usb_record_fields_require_bounded_exact_ints(valid_record, field, value):
    record = json.loads(valid_record.read_text())
    record[field] = value
    valid_record.write_text(json.dumps(record))
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN


@pytest.mark.parametrize("identity", [(True, 2), (1.0, 2), (1, 2.0), (1, True), (-1, 2), (256, 2)])
def test_caller_identity_requires_bounded_exact_ints(valid_record, identity):
    assert latch.load_latch(*identity) is latch.HolderLatch.UNKNOWN


@pytest.mark.parametrize("identity", [(True, 2), (1.0, 2), (1, -1), (1, 256)])
def test_invalid_identity_is_not_saved(state_path, identity):
    latch.save_latch(*identity, latch.HolderLatch.LATCHED)
    assert not state_path.exists()


def test_fifo_is_rejected_without_blocking(valid_record):
    valid_record.unlink()
    os.mkfifo(valid_record, 0o600)
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN


def test_eof_probe_rejects_growth_after_initial_stat(valid_record, monkeypatch):
    real_fstat = os.fstat

    def stale_size(descriptor):
        metadata = real_fstat(descriptor)
        return SimpleNamespace(st_mode=metadata.st_mode, st_uid=metadata.st_uid, st_size=60)

    monkeypatch.setattr(os, "fstat", stale_size)
    valid_record.write_bytes(valid_record.read_bytes().ljust(65537, b" "))
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN


@pytest.mark.parametrize("identity", [(0, 0), (255, 255)])
def test_usb_boundary_values_round_trip(state_path, identity):
    latch.save_latch(*identity, latch.HolderLatch.LATCHED)
    assert latch.load_latch(*identity) is latch.HolderLatch.LATCHED


@pytest.mark.parametrize("identity", [(-1, 2), (256, 2), (1, -1), (1, 256)])
def test_even_matching_invalid_usb_numbers_are_unknown(valid_record, identity):
    valid_record.write_text(json.dumps(dict(bus=identity[0], address=identity[1], state="latched")))
    assert latch.load_latch(*identity) is latch.HolderLatch.UNKNOWN


@pytest.mark.parametrize("state", ["latched", True, 1, None])
def test_save_requires_latch_enum(state_path, state):
    latch.save_latch(1, 2, state)
    assert not state_path.exists()


def test_oversized_file_is_rejected_before_reading(valid_record, monkeypatch):
    valid_record.write_bytes(valid_record.read_bytes().ljust(65537, b" "))
    real_fdopen = os.fdopen

    @contextmanager
    def no_read(*args, **kwargs):
        with real_fdopen(*args, **kwargs) as stream:

            class NoRead:
                def fileno(self):
                    return stream.fileno()

                def read(self, size):
                    raise AssertionError("oversized file must not be read")

            yield NoRead()

    monkeypatch.setattr(os, "fdopen", no_read)
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN


def test_read_budget_even_when_file_grows(valid_record, monkeypatch):
    real_fdopen, real_fstat = os.fdopen, os.fstat
    reads = []

    @contextmanager
    def measured(*args, **kwargs):
        assert kwargs["buffering"] == 0
        with real_fdopen(*args, **kwargs) as stream:

            class Measured:
                def fileno(self):
                    return stream.fileno()

                def read(self, size):
                    reads.append(size)
                    return stream.read(size)

            yield Measured()

    def stale_size(descriptor):
        metadata = real_fstat(descriptor)
        return SimpleNamespace(st_mode=metadata.st_mode, st_uid=metadata.st_uid, st_size=60)

    valid_record.write_bytes(valid_record.read_bytes().ljust(70000, b" "))
    monkeypatch.setattr(os, "fdopen", measured)
    monkeypatch.setattr(os, "fstat", stale_size)
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN
    assert sum(reads) <= 65536


def test_invalid_utf8_is_not_silently_discarded(valid_record):
    valid_record.write_bytes(b"\xff" + valid_record.read_bytes())
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "public", "foreign"])
def test_save_refuses_hostile_existing_destination(valid_record, monkeypatch, kind):
    if kind == "symlink":
        target = valid_record.with_suffix(".target")
        valid_record.rename(target)
        valid_record.symlink_to(target)
    elif kind == "fifo":
        valid_record.unlink()
        os.mkfifo(valid_record, 0o600)
    elif kind == "directory":
        valid_record.unlink()
        valid_record.mkdir()
    elif kind == "public":
        valid_record.chmod(0o644)
    else:
        real_lstat = Path.lstat

        def foreign(path):
            metadata = real_lstat(path)
            return SimpleNamespace(st_mode=metadata.st_mode, st_uid=-1)

        monkeypatch.setattr(Path, "lstat", foreign)
    before = valid_record.lstat()
    latch.save_latch(1, 2, latch.HolderLatch.RELEASED)
    after = valid_record.lstat()
    assert (after.st_uid, after.st_mode) == (before.st_uid, before.st_mode)
    if kind in ("public", "foreign"):
        assert json.loads(valid_record.read_text())["state"] == "latched"


@pytest.mark.parametrize(
    "field, value",
    [
        ("st_mode", stat.S_IFIFO | 0o600),
        ("st_mode", stat.S_IFCHR | 0o600),
        ("st_mode", stat.S_IFLNK | 0o600),
        ("st_mode", stat.S_IFREG | 0o644),
        ("st_uid", -1),
    ],
)
def test_hostile_path_is_rejected_before_open(valid_record, monkeypatch, field, value):
    real_lstat = Path.lstat

    def hostile(path):
        metadata = real_lstat(path)
        fields = dict(st_mode=metadata.st_mode, st_uid=metadata.st_uid)
        fields[field] = value
        return SimpleNamespace(**fields)

    def forbidden_open(*args, **kwargs):
        raise AssertionError("must not open a hostile path")

    monkeypatch.setattr(Path, "lstat", hostile)
    monkeypatch.setattr(os, "open", forbidden_open)
    assert latch.load_latch(1, 2) is latch.HolderLatch.UNKNOWN


def test_saved_mode_is_exact_even_when_creation_loses_owner_permissions(state_path, monkeypatch):
    state_path.parent.mkdir()
    real_mkstemp = latch.tempfile.mkstemp

    def restrictive_creation(*args, **kwargs):
        descriptor, temporary = real_mkstemp(*args, **kwargs)
        os.fchmod(descriptor, 0)
        return descriptor, temporary

    monkeypatch.setattr(latch.tempfile, "mkstemp", restrictive_creation)
    latch.save_latch(1, 2, latch.HolderLatch.LATCHED)
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert latch.load_latch(1, 2) is latch.HolderLatch.LATCHED


def test_failed_mode_change_closes_descriptor_and_removes_temporary(state_path, monkeypatch):
    descriptors = []

    def fail(descriptor, mode):
        descriptors.append(descriptor)
        raise OSError("cannot set private mode")

    monkeypatch.setattr(os, "fchmod", fail)
    latch.save_latch(1, 2, latch.HolderLatch.LATCHED)
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert not state_path.exists()
    assert list(state_path.parent.iterdir()) == []
