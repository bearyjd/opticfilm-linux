"""Window sizing policy in logical pixels, without a Qt dependency.

Every function is fail-safe: it never returns a zero or negative size, whatever
the screen, grid unit or saved settings report. A saved maximized state wins
over the first-run small-screen rule: the first-run rule only applies when
nothing is saved.
"""

import math
from dataclasses import dataclass

DEFAULT_GRID_UNIT = 18
# Room kept around a restored size so the frame and title bar stay on the work area.
SAVED_MARGIN_W = 16
SAVED_MARGIN_H = 48


@dataclass(frozen=True)
class SavedGeometry:
    width: int
    height: int
    maximized: bool


def safe_grid_unit(value: object) -> float:
    """Use the default when the grid unit is missing, non-numeric, non-finite or <= 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_GRID_UNIT
    return float(value) if math.isfinite(value) and value > 0 else DEFAULT_GRID_UNIT


def _positive(value: object) -> int:
    try:
        return max(1, int(value))  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return 1


def minimum_size(avail_w: int, avail_h: int, gu: object) -> tuple[int, int]:
    unit = safe_grid_unit(gu)
    return (
        max(1, min(round(40 * unit), _positive(avail_w))),
        max(1, min(round(30 * unit), _positive(avail_h))),
    )


def initial_geometry(
    avail_w: int, avail_h: int, gu: object, saved: SavedGeometry | None
) -> tuple[int, int, bool, int, int]:
    """Fit the initial or remembered window to its target screen's work area."""
    avail_w, avail_h, unit = _positive(avail_w), _positive(avail_h), safe_grid_unit(gu)
    min_w, min_h = minimum_size(avail_w, avail_h, unit)
    if saved is not None:
        width = min(max(_positive(saved.width), min_w), max(avail_w - SAVED_MARGIN_W, min_w))
        height = min(max(_positive(saved.height), min_h), max(avail_h - SAVED_MARGIN_H, min_h))
        return width, height, saved.maximized, min_w, min_h
    width = min(max(int(0.85 * avail_w), min_w), round(100 * unit))
    height = min(max(int(0.85 * avail_h), min_h), round(60 * unit))
    maximized = avail_w < 70 * unit or avail_h < 45 * unit
    return width, height, maximized, min_w, min_h


def override_geometry(
    size: object, avail_w: int, avail_h: int, gu: object
) -> tuple[int, int, int, int] | None:
    """Validate an explicit size and clamp it to the work area; None if invalid."""
    valid = _valid_size(size)
    if valid is None:
        return None
    avail_w, avail_h = _positive(avail_w), _positive(avail_h)
    min_w, min_h = minimum_size(avail_w, avail_h, gu)
    return min(valid[0], avail_w), min(valid[1], avail_h), min_w, min_h


def _valid_size(size: object) -> tuple[int, int] | None:
    if not isinstance(size, (tuple, list)) or len(size) != 2:
        return None
    if any(isinstance(part, bool) or not isinstance(part, int) for part in size):
        return None
    return (size[0], size[1]) if size[0] > 0 and size[1] > 0 else None


def parse_window_size(value: str) -> tuple[int, int] | None:
    """Ignore malformed overrides so an environment typo cannot prevent startup."""
    try:
        width, height = (int(part) for part in value.lower().split("x"))
    except ValueError:
        return None
    return _valid_size((width, height))


def resolve_override(size: object, env_value: str) -> tuple[int, int] | None:
    """Explicit size wins when valid; otherwise the environment value; else None."""
    return _valid_size(size) or parse_window_size(env_value)
