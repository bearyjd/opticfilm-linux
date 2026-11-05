"""The holder openings the next scan covers (``OPTICFILM_SKIP_EMPTY``, ``OPTICFILM_SKIP_GAPS``).

Both switches are off by default, and then the choice can never leave all four openings, so a
scan runs exactly today's driver command. The choice is for one tray only: it goes back to all
four once a scan has been handed to the runner (owner decision QA2).
"""

from __future__ import annotations

from opticfilm_linux.app.state import Banner
from opticfilm_linux.model import ALL_POSITIONS, check_positions
from opticfilm_linux.scanner.select import (
    SKIP_EMPTY_ENV,
    SKIP_GAPS_ENV,
    skip_empty_enabled,
    skip_gaps_enabled,
)

SKIP_EMPTY_OFF = (
    f"Choosing openings is switched off. Set {SKIP_EMPTY_ENV}=1 and restart the app to use it."
)
NO_OPENINGS = "Choose at least one opening. If the holder is empty, eject it instead of scanning."
GAPS_OFF = (
    "Only the first openings can be scanned (for example 1 to 3). "
    f"Set {SKIP_GAPS_ENV}=1 and restart the app to leave out an opening in between."
)


def skipped_openings(openings: tuple[int, ...]) -> tuple[int, ...]:
    """The openings left out of a scan of ``openings``; the runner takes these, not the scanned."""
    return tuple(p for p in ALL_POSITIONS if p not in openings)


class ScanOpeningsCommands:
    """Mixed into ``Controller``, whose lock and ``_set`` it uses."""

    def _init_scan_openings(self, skip_empty: bool | None, skip_gaps: bool | None) -> None:
        self._skip_empty = skip_empty_enabled() if skip_empty is None else skip_empty
        gaps = skip_gaps_enabled() if skip_gaps is None else skip_gaps
        self._skip_gaps = self._skip_empty and gaps  # gaps refine the choice; alone they do nothing
        self._scan_openings = ALL_POSITIONS

    @property
    def skip_empty_enabled(self) -> bool:
        return self._skip_empty

    @property
    def skip_gaps_enabled(self) -> bool:
        return self._skip_gaps

    def set_scan_openings(self, openings: tuple[int, ...]) -> None:
        """Choose the openings of the next scan; a choice the switches do not allow is refused."""
        refusal = self._openings_refusal(openings)
        if refusal is not None:
            self._set(_banner=Banner(refusal))
            return
        self._set(_scan_openings=openings)

    def _openings_refusal(self, openings: tuple[int, ...]) -> str | None:
        if not self._skip_empty:
            return SKIP_EMPTY_OFF
        if not openings:
            return NO_OPENINGS
        try:
            check_positions(openings)
        except ValueError as error:
            return str(error)
        if not self._skip_gaps and openings != ALL_POSITIONS[: len(openings)]:
            return GAPS_OFF
        return None

    def _next_scan_skipped(self) -> tuple[int, ...]:
        """Called under the controller lock when a scan is requested."""
        return skipped_openings(self._scan_openings) if self._skip_empty else ()

    def _scan_openings_used(self) -> None:
        """A scan was handed to the runner: the next tray starts again from all four openings."""
        self._set(_scan_openings=ALL_POSITIONS)
