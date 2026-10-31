"""Step the background Eject-button watcher aside while the app has the scanner open.

A systemd user unit can keep the driver's ``watch`` command running so the front-panel Eject
button works with no app open. Only one program may use the scanner, so the app stops that unit
before it opens the scanner and starts it again afterwards, but only if it was running. Every
failure here is soft except one: if the unit is still running after the stop, the app does not
open the scanner.
"""

import logging
import subprocess
from collections.abc import Callable, Sequence

UNIT = "opticfilm-eject-watch.service"
# The watcher is stopped with SIGINT and may be finishing a holder move, so allow it time.
SYSTEMCTL_TIMEOUT_S = 150.0
RUNNING_STATES = frozenset({"active", "activating", "reloading"})  # up, or waiting to retry
SETTLED_STATES = frozenset({"inactive", "failed"})

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


class EjectWatch:
    def __init__(self, runner: Runner = run_systemctl, unit: str = UNIT) -> None:
        self._run = runner
        self._unit = unit
        self._was_running = False

    def yield_to_app(self) -> None:
        """Stop the watcher if it is running. Raises ``WatchStillActive`` if it stays up."""
        self._was_running = False
        if self._state() not in RUNNING_STATES:
            return  # not installed, not running, or no systemctl: nothing to yield
        self._was_running = True
        self._systemctl("stop")
        after = self._state()
        if after not in SETTLED_STATES:
            raise WatchStillActive(
                f"The Eject-button watcher ({self._unit}) is still running, so the scanner "
                f"was not opened (state: {after or 'unknown'}). Stop it with "
                f"`systemctl --user stop {self._unit}` and try again."
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

    def _state(self) -> str | None:
        """The unit's state word, or ``None`` when it cannot be asked."""
        try:
            _code, out = self._run(["--user", "is-active", self._unit])
        except (OSError, subprocess.SubprocessError) as exc:
            log.info("cannot ask systemd about %s: %s", self._unit, exc)
            return None
        return out.strip() or None

    def _systemctl(self, verb: str) -> int | None:
        try:
            code, _out = self._run(["--user", verb, self._unit])
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("systemctl --user %s %s failed: %s", verb, self._unit, exc)
            return None
        if code != 0:
            log.warning("systemctl --user %s %s exited %d", verb, self._unit, code)
        return code
