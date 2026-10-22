import numpy as np
import pytest

from opticfilm_linux.pipeline.orient import rotate


def _image() -> np.ndarray:
    """(3, 2, 3) uint16 whose value at (row, col) is row * 2 + col."""
    values = np.arange(6, dtype=np.uint16).reshape(3, 2, 1)
    return np.repeat(values, 3, axis=2)


def test_rotate_90_is_clockwise():
    image = _image()
    out = rotate(image, 90)
    assert out.shape == (2, 3, 3)
    assert out[0, 2, 0] == image[0, 0, 0]  # top-left goes to top-right
    assert out[0, 0, 0] == image[2, 0, 0]  # bottom-left goes to top-left


def test_rotate_180_twice_is_identity():
    image = _image()
    assert np.array_equal(rotate(rotate(image, 180), 180), image)


def test_rotate_zero_returns_an_equal_new_array():
    image = _image()
    out = rotate(image, 0)
    assert np.array_equal(out, image) and not np.shares_memory(out, image)


def test_rotate_result_is_contiguous_uint16():
    out = rotate(_image(), 270)
    assert out.dtype == np.uint16 and out.flags["C_CONTIGUOUS"]


@pytest.mark.parametrize("degrees", [45, -90, 360, 91])
def test_rotate_rejects_other_angles(degrees):
    with pytest.raises(ValueError, match="degrees"):
        rotate(_image(), degrees)
