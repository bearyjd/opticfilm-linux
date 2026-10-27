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
import time
from pathlib import Path

from opticfilm_linux.scanner.base import ScanError

INTERRUPTED_TEXT = "interrupted: needs power-cycle + reload + rescan"
CONFIRM_COMMAND = "ofscan batch confirm-scanner --yes"
MARKER_FILE = "scanner-interrupted.json"


class ScannerRefused(ScanError):
    """A scanner command was refused before the driver started, so nothing failed."""


class ScannerInterrupted(ScannerRefused):
    """An earlier command was cut off; the owner has not yet confirmed the scanner was reset."""


class ScanInterrupted(ScanError):
    """This program stopped the driver mid-command: whatever it wrote is a partial payload."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"{INTERRUPTED_TEXT}: {detail}")


def state_dir() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return state / "opticfilm-linux"


def marker_path() -> Path:
    return state_dir() / MARKER_FILE


def is_pending() -> bool:
    """True when the mark is there, or when it cannot be checked at all."""
    try:
        os.stat(marker_path())
    except (FileNotFoundError, NotADirectoryError):  # the path cannot hold a mark
        return False
    except OSError:
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
        _write_marker(command)
    except OSError as exc:
        raise ScannerRefused(
            f"cannot record that a scanner command is starting ({exc}); refusing to start it"
        ) from exc


def clear() -> None:
    """The command ended by itself, or the owner confirmed the scanner was reset."""
    marker_path().unlink(missing_ok=True)


def _write_marker(command: str) -> None:
    path = marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(MARKER_FILE + ".tmp")
    record = {"command": command, "pid": os.getpid(), "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        with open(tmp, "w") as handle:
            handle.write(json.dumps(record))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _describe() -> str:
    try:
        record = json.loads(marker_path().read_text())
        return f"{record['command']} started {record['started']}"
    except (OSError, ValueError, KeyError, TypeError):
        return "details unreadable"
