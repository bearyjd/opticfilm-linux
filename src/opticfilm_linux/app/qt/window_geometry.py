"""Window sizing policy in logical pixels, without a Qt dependency."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SavedGeometry:
    width: int
    height: int
    maximized: bool


def initial_geometry(
    avail_w: int, avail_h: int, gu: float, saved: SavedGeometry | None
) -> tuple[int, int, bool, int, int]:
    """Fit the initial or remembered window to its target screen's work area."""
    min_w, min_h = min(round(40 * gu), avail_w), min(round(30 * gu), avail_h)
    if saved is not None:
        width = min(max(saved.width, min_w), avail_w)
        height = min(max(saved.height, min_h), avail_h)
        return width, height, saved.maximized, min_w, min_h
    width = min(max(int(0.85 * avail_w), min_w), round(100 * gu))
    height = min(max(int(0.85 * avail_h), min_h), round(60 * gu))
    maximized = avail_w < 70 * gu or avail_h < 45 * gu
    return width, height, maximized, min_w, min_h


def parse_window_size(value: str) -> tuple[int, int] | None:
    """Ignore malformed overrides so an environment typo cannot prevent startup."""
    try:
        width, height = (int(part) for part in value.lower().split("x"))
    except ValueError:
        return None
    return (width, height) if width > 0 and height > 0 else None
