"""Qt-free knowledge of the holder latch, saved per USB connection but never restored.

The latch has no sensor, so this is knowledge of what the app itself did, never a hardware
readiness check. The app uses it as a software gate: after a known eject (RELEASED) Scan is
refused until the owner confirms the holder is seated and latched; when the state is UNKNOWN
scanning is allowed with a warning. Only a successful load or a confirmed eject establishes a
known state. The record is written to disk, but a new session starts UNKNOWN and never
restores it, because the holder can be changed while the app is closed. Failed sessions,
failed moves, interruptions, disconnections and interrupted batches invalidate that knowledge.
"""

import json
import logging
import os
import stat
import tempfile
from enum import StrEnum
from pathlib import Path

from opticfilm_linux.scanner.interrupted import state_dir

log = logging.getLogger(__name__)
_MAX_RECORD_BYTES = 64 * 1024


class HolderLatch(StrEnum):
    LATCHED = "latched"
    RELEASED = "released"
    UNKNOWN = "unknown"


def after_load(success: bool) -> HolderLatch:
    """A completed load establishes that the holder is latched."""
    return HolderLatch.LATCHED if success else HolderLatch.UNKNOWN


def after_eject(outcome: str | None) -> HolderLatch:
    """Only the driver's explicit ejected outcome confirms release."""
    return HolderLatch.RELEASED if outcome == "ejected" else HolderLatch.UNKNOWN


def invalidate() -> HolderLatch:
    """Forget latch knowledge after a failed session, move or interruption."""
    return HolderLatch.UNKNOWN


def latch_state_path() -> Path:
    return state_dir() / "holder-latch.json"


def _usb_number(value: object) -> bool:
    return type(value) is int and 0 <= value <= 255


def load_latch(bus: int, address: int) -> HolderLatch:
    """Restore knowledge only for the exact USB bus/address; errors mean unknown."""
    try:
        if not (_usb_number(bus) and _usb_number(address)):
            return HolderLatch.UNKNOWN
        path = latch_state_path()
        before_open = path.lstat()
        if (
            not stat.S_ISREG(before_open.st_mode)
            or before_open.st_uid != os.getuid()
            or stat.S_IMODE(before_open.st_mode) != 0o600
        ):
            return HolderLatch.UNKNOWN
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb", buffering=0) as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > _MAX_RECORD_BYTES
            ):
                return HolderLatch.UNKNOWN
            # Reserve one byte of the total read budget for the EOF check.
            # A record that fills the entire budget is conservatively unknown.
            raw = stream.read(_MAX_RECORD_BYTES - 1)
            if stream.read(1) != b"":
                return HolderLatch.UNKNOWN
        record = json.loads(raw.decode("utf-8", errors="strict"))
        if not isinstance(record, dict):
            return HolderLatch.UNKNOWN
        if not (_usb_number(record.get("bus")) and _usb_number(record.get("address"))):
            return HolderLatch.UNKNOWN
        if record.get("bus") != bus or record.get("address") != address:
            return HolderLatch.UNKNOWN
        return HolderLatch(record["state"])
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, RecursionError):
        log.debug("Could not restore holder latch knowledge", exc_info=True)
        return HolderLatch.UNKNOWN


def save_latch(bus: int, address: int, state: HolderLatch) -> None:
    """Best-effort private atomic storage; persistence must never break scanning."""
    temporary: str | None = None
    try:
        if not (_usb_number(bus) and _usb_number(address)) or type(state) is not HolderLatch:
            return
        path = latch_state_path()
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            not stat.S_ISREG(existing.st_mode)
            or existing.st_uid != os.getuid()
            or stat.S_IMODE(existing.st_mode) != 0o600
        ):
            return
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix="holder-latch.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"bus": bus, "address": address, "state": state.value}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except (OSError, RuntimeError, ValueError, TypeError):
        log.warning("Could not persist holder latch knowledge", exc_info=True)
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
