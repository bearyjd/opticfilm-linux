"""Step the background Eject-button watcher aside while the app has the scanner open.

A systemd user unit can keep the driver's ``watch`` command running so the front-panel Eject
button works with no app open. Only one program may use the scanner, so the app stops that unit
before it opens the scanner and starts it again afterwards, but only if it was running. Every
failure here is soft except one: if the unit is still running after the stop, or an
``of135i watch`` process is still alive anywhere (the unit's stop can leave one behind, and a
hand-started one is the same hazard), the app does not open the scanner. The app never kills
anything: ``watch`` must only ever be stopped with SIGINT.
"""

import logging
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

UNIT = "opticfilm-eject-watch.service"
# The watcher is stopped with SIGINT and may be finishing a holder move, so allow it time.
SYSTEMCTL_TIMEOUT_S = 150.0
SETTLE_POLLS = 125  # one second apart: a little over TimeoutStopSec in the unit
RUNNING_STATES = frozenset({"active", "activating", "reloading"})  # up, or waiting to retry
SETTLED_STATES = frozenset({"inactive", "failed"})  # `failed` still needs the process check
PROC = Path("/proc")
DRIVER_PROGRAM = "of135i"
WATCH_COMMAND = "watch"

log = logging.getLogger(__name__)

# Runs ``systemctl`` with these arguments and returns (exit code, standard output). It may raise
# OSError (no systemctl) or subprocess.TimeoutExpired.
Runner = Callable[[Sequence[str]], tuple[int, str]]


class WatchStillActive(Exception):
    """The watcher did not stop, so the scanner must not be opened."""


def run_systemctl(args: Sequence[str]) -> tuple[int, str]:
    done = subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        timeout=SYSTEMCTL_TIMEOUT_S,
        check=False,
    )
    return done.returncode, done.stdout


def watch_process_running(proc_root: Path = PROC) -> bool:
    """Whether any process is running ``of135i watch``, found by reading ``/proc/*/cmdline``.

    A container that shares the host's process namespace (distrobox does by default) shows up
    here too. Entries that vanish or cannot be read are skipped.
    """
    try:
        entries = [entry for entry in proc_root.iterdir() if entry.name.isdigit()]
    except OSError as exc:
        log.warning("cannot list %s to look for a running watcher: %s", proc_root, exc)
        return False
    return any(_runs_watch(entry) for entry in entries)


def _runs_watch(entry: Path) -> bool:
    try:
        raw = (entry / "cmdline").read_bytes()
    except OSError:
        return False  # gone already, or not ours to read
    args = raw.decode(errors="replace").split("\0")
    for index, arg in enumerate(args):
        if arg == DRIVER_PROGRAM or arg.endswith("/" + DRIVER_PROGRAM):
            return WATCH_COMMAND in args[index + 1 :]
    return False


class EjectWatch:
    def __init__(
        self,
        runner: Runner = run_systemctl,
        unit: str = UNIT,
        process_check: Callable[[], bool] = watch_process_running,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._run = runner
        self._unit = unit
        self._process_check = process_check
        self._sleep = sleep
        self._was_running = False

    def yield_to_app(self) -> None:
        """Stop the watcher if it is running. Raises ``WatchStillActive`` if it stays up."""
        state = self._state()
        asked = state is not None
        if state in RUNNING_STATES:
            self._was_running = True  # stays set across calls until ``restore``
            self._systemctl("stop")
            state = self._state()
        if asked and self._wait_settled(state) not in SETTLED_STATES:
            raise WatchStillActive(
                f"The Eject-button watcher ({self._unit}) is still running, so the scanner "
                f"was not opened. Stop it with `systemctl --user stop {self._unit}` and try again."
            )
        if self._process_check():
            raise WatchStillActive(
                "An `of135i watch` process is still running, so the scanner was not opened. "
                "End it with Ctrl-C in its terminal (never kill it) and try again."
            )

    def restore(self) -> str | None:
        """Start the watcher again if it was running. Returns a problem text, never raises."""
        if not self._was_running:
            return None
        self._was_running = False
        if self._systemctl("start") == 0:
            return None
        return (
            "The Eject button watcher did not start again. Start it with "
            f"`systemctl --user start {self._unit}`."
        )

    def _wait_settled(self, state: str | None) -> str | None:
        """Poll until the unit is down (or failed), for at most about ``SETTLE_POLLS`` seconds."""
        for _ in range(SETTLE_POLLS):
            if state in SETTLED_STATES:
                break
            self._sleep(1.0)
            state = self._state()
        return state

    def _state(self) -> str | None:
        """The unit's state word, or ``None`` when it cannot be asked."""
        try:
            code, out = self._run(["--user", "is-active", self._unit])
        except FileNotFoundError:
            log.info("systemctl is not installed; no Eject-button watcher to step aside")
            return None
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("cannot ask systemd about %s: %s", self._unit, exc)
            return None
        state = out.strip()
        if not state:
            log.warning(
                "systemd gave no answer about %s (exit %d): no user session bus?", self._unit, code
            )
        return state or None

    def _systemctl(self, verb: str) -> int | None:
        try:
            code, _out = self._run(["--user", verb, self._unit])
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("systemctl --user %s %s failed: %s", verb, self._unit, exc)
            return None
        if code != 0:
            log.warning("systemctl --user %s %s exited %d", verb, self._unit, code)
        return code
