"""The driver's ``--slides`` argument for a set of holder openings."""

from opticfilm_linux.model import ALL_POSITIONS, check_positions

SLIDES = "1-4"  # the four openings of the mounted-slide holder


def _runs(positions: tuple[int, ...]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for position in positions:
        if runs and runs[-1][1] == position - 1:
            runs[-1] = (runs[-1][0], position)
        else:
            runs.append((position, position))
    return runs


def slides_arg(positions: tuple[int, ...]) -> str:
    """A compact ascending spec: ``(1, 2, 3)`` gives ``1-3``, ``(1, 3)`` gives ``1,3``.

    The full holder is always exactly ``SLIDES``, so a default scan runs today's command.
    """
    check_positions(positions)
    if positions == ALL_POSITIONS:
        return SLIDES
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in _runs(positions))
