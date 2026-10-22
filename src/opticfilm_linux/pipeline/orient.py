"""Quarter-turn rotation of uint16 RGB images."""

import numpy as np

QUARTER_TURNS = (0, 90, 180, 270)


def rotate(rgb: np.ndarray, degrees: int) -> np.ndarray:
    """Rotate clockwise by 0, 90, 180 or 270 degrees; always returns a new contiguous array."""
    if degrees not in QUARTER_TURNS:
        raise ValueError(f"degrees must be one of {QUARTER_TURNS}, got {degrees}")
    return np.rot90(rgb, k=-(degrees // 90)).copy(order="C")
