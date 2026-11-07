"""The driver's ``--slides`` argument for a set of holder openings."""

from opticfilm_linux.model import ALL_POSITIONS, check_positions

SLIDES = "1-4"  # the four openings of the mounted-slide holder


def slides_arg(positions: tuple[int, ...]) -> str:
    """One range when consecutive (``(1, 2, 3)`` gives ``1-3``), else a comma list (``1,3,4``).

    Only these two documented forms are sent; a range inside a comma list is never built. The
    full holder is always exactly ``SLIDES``, so a default scan runs today's command.
    """
    check_positions(positions)
    if positions == ALL_POSITIONS:
        return SLIDES
    first, last = positions[0], positions[-1]
    if len(positions) > 1 and last - first == len(positions) - 1:
        return f"{first}-{last}"
    return ",".join(str(p) for p in positions)
