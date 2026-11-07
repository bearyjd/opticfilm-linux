"""Workflow switches that can be saved in the preferences (auto-eject, skip-empty, Load & scan).

An explicit environment variable (``OPTICFILM_AUTO_EJECT``, ``OPTICFILM_SKIP_EMPTY``,
``OPTICFILM_LOAD_AND_SCAN``) always
wins over the saved value and locks the switch in the app. Unset means "use the saved value",
which is off on a fresh profile. ``OPTICFILM_SKIP_GAPS`` stays environment-only.
"""

from __future__ import annotations

from opticfilm_linux.app.prefs import (
    AUTO_EJECT_KEY,
    LOAD_AND_SCAN_KEY,
    SKIP_EMPTY_KEY,
    Prefs,
    read_switch,
)
from opticfilm_linux.app.state import Banner
from opticfilm_linux.model import ALL_POSITIONS
from opticfilm_linux.scanner.select import (
    AUTO_EJECT_ENV,
    LOAD_AND_SCAN_ENV,
    SKIP_EMPTY_ENV,
    env_switch,
    skip_gaps_enabled,
)

ENV_NAMES = {
    "auto_eject": AUTO_EJECT_ENV,
    "skip_empty": SKIP_EMPTY_ENV,
    "load_and_scan": LOAD_AND_SCAN_ENV,
}
SAVED_KEYS = {
    "auto_eject": AUTO_EJECT_KEY,
    "skip_empty": SKIP_EMPTY_KEY,
    "load_and_scan": LOAD_AND_SCAN_KEY,
}


OPENINGS_DROPPED = "The chosen openings were cleared: the next scan covers all four."


def resolve_switch(env: bool | None, pref: bool) -> bool:
    """The environment decides when it is set (either way); otherwise the saved value."""
    return pref if env is None else env


def startup_switches(prefs: Prefs) -> dict:
    """``Controller`` keyword arguments for the saved switches and the environment."""
    envs = {name: env_switch(env) for name, env in ENV_NAMES.items()}
    values = {
        name: resolve_switch(env, read_switch(prefs, SAVED_KEYS[name]))
        for name, env in envs.items()
    }
    return {
        **values,
        "skip_gaps": skip_gaps_enabled(),
        "env_locked": frozenset(name for name, env in envs.items() if env is not None),
    }


def locked_text(name: str) -> str:
    return f"{ENV_NAMES[name]} is set, so it decides this. Unset it and restart to change it here."


class WorkflowSettings:
    """Mixed into ``Controller``, whose lock and ``_set`` it uses."""

    def _init_workflow_settings(self, env_locked: frozenset[str]) -> None:
        self._env_locked = env_locked
        self._eject_at_start = False  # the auto-eject setting when the running scan was started

    @property
    def env_locked(self) -> frozenset[str]:
        return self._env_locked

    def set_auto_eject(self, on: bool) -> bool:
        """Applies to the next finished scan. False: refused (an environment variable decides)."""
        with self._lock:
            if self._refuse_locked("auto_eject"):
                return False
            self._auto_eject = on
            return True

    def set_skip_empty(self, on: bool) -> bool:
        """Applies from the next Scan press; a scan already started keeps the openings it took."""
        with self._lock:
            if self._refuse_locked("skip_empty"):
                return False
            self._skip_empty = on
            self._skip_gaps = on and self._gaps_requested
            if self._scan_openings != ALL_POSITIONS:  # a choice made under the old setting
                self._banner = Banner(OPENINGS_DROPPED)
            self._scan_openings = ALL_POSITIONS
            return True

    def _snapshot_auto_eject(self) -> None:
        """Called under the lock when a scan is reserved: the setting that scan starts with."""
        self._eject_at_start = self._auto_eject

    def _refuse_locked(self, name: str) -> bool:
        """Called under the lock."""
        if name not in self._env_locked:
            return False
        self._banner = Banner(locked_text(name))
        return True
