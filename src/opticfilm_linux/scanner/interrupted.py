"""A durable mark that a scanner command may not have finished.

It is written before the driver starts and removed only when the driver ended by itself, so a
scan or holder move cut off mid-run (Ctrl-C, a closed terminal, a killed or crashed program)
leaves it behind: the scanner's state is then unknown. While it exists no scan or holder move
starts, in any program, until the owner confirms the scanner was power-cycled and the holder
loaded again. Fails closed: a mark that cannot be checked counts as present, and a command whose
mark cannot be written is never started.
"""

import json
import os
import tempfile
import time
from pathlib import Path

from opticfilm_linux.scanner.base import ScanError

INTERRUPTED_TEXT = "interrupted: needs power-cycle + reload + rescan"
CONFIRM_COMMAND = "ofscan batch confirm-scanner --yes"
MARKER_FILE = "scanner-interrupted.json"
MARKER_MAX_BYTES = 4096  # the mark is a few dozen bytes; anything larger is not ours
DETAIL_MAX_CHARS = 80


class ScannerRefused(ScanError):
    """A scanner command was refused before the driver started, so nothing failed."""


class ScannerInterrupted(ScannerRefused):
    """An earlier command was cut off; the owner has not yet confirmed the scanner was reset."""


class ScanInterrupted(ScanError):
    """This program stopped the driver mid-command: whatever it wrote is a partial payload."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"{INTERRUPTED_TEXT}: {detail}")


def state_dir() -> Path:
    """$XDG_STATE_HOME/opticfilm-linux; a relative XDG_STATE_HOME is invalid and ignored."""
    configured = os.environ.get("XDG_STATE_HOME")
    if configured and os.path.isabs(configured):
        return Path(configured) / "opticfilm-linux"
    return Path.home() / ".local" / "state" / "opticfilm-linux"


def marker_path() -> Path:
    return state_dir() / MARKER_FILE


def is_pending() -> bool:
    """True when the mark is there, or when it cannot be checked at all."""
    try:
        os.stat(marker_path())
    except (FileNotFoundError, NotADirectoryError):  # the path cannot hold a mark
        return False
    except (OSError, RuntimeError):  # RuntimeError: no home folder to find the mark in
        return True
    return True


def refusal_text() -> str:
    return (
        f"the scanner's last command was cut off ({_describe()}), so its state is unknown: "
        f"{INTERRUPTED_TEXT}. Power-cycle the scanner (off until the lights go out, then on), "
        f'load the holder again, then confirm with "{CONFIRM_COMMAND}" (in the app: Confirm '
        "scanner reset) and rescan the interrupted tray"
    )


def require_clear() -> None:
    if is_pending():
        raise ScannerInterrupted(refusal_text())


def begin(command: str) -> None:
    """Write the mark before a driver command starts; refuse the command if it cannot be."""
    try:
        _write_marker({"command": command, "pid": os.getpid(), "started": _now()})
    except (OSError, RuntimeError) as exc:
        raise ScannerRefused(
            f"cannot record that a scanner command is starting ({exc}); refusing to start it. "
            f"Check that {_where()} is a writable folder"
        ) from exc


def note_driver(command: str, group: int) -> None:
    """Add the driver's process group to the mark, so a confirm can wait for an orphan."""
    _write_marker({"command": command, "pid": os.getpid(), "started": _now(), "group": group})


def driver_group() -> int | None:
    """The process group of the cut-off command's driver, if the mark recorded one."""
    group = _read().get("group")
    return group if isinstance(group, int) and group > 1 else None


def clear() -> None:
    """The command ended by itself, or the owner confirmed the scanner was reset."""
    marker_path().unlink(missing_ok=True)


def _write_marker(record: dict) -> None:
    path = marker_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=MARKER_FILE + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(json.dumps(record))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)  # the rename itself survives a power loss
    finally:
        os.close(directory)


def _read() -> dict:
    try:
        with open(marker_path(), "rb") as handle:
            record = json.loads(handle.read(MARKER_MAX_BYTES))
    except (OSError, RuntimeError, ValueError, RecursionError):
        return {}
    return record if isinstance(record, dict) else {}


def _describe() -> str:
    record = _read()
    command, started = record.get("command"), record.get("started")
    if not (isinstance(command, str) and isinstance(started, str)):
        return "details unreadable"
    text = f"{command} started {started}"[:DETAIL_MAX_CHARS]
    return "".join(c for c in text if c.isprintable())


def _where() -> str:
    try:
        return str(state_dir())
    except RuntimeError:
        return "the state folder (no home folder found)"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
