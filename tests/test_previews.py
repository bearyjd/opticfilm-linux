import numpy as np
from PIL import Image

from opticfilm_linux.batch.previews import make_preview, write_preview

FULL = 65535


def _flat(h: int, w: int, level: float) -> np.ndarray:
    return np.full((h, w, 3), np.uint16(level * FULL), np.uint16)


def test_long_side_is_capped_and_aspect_kept():
    out = make_preview(_flat(2000, 3000, 0.5))
    assert out.dtype == np.uint8
    assert abs(out.shape[1] - 1600) <= 1 and abs(out.shape[0] - 1067) <= 1


def test_portrait_images_are_capped_on_their_long_side():
    out = make_preview(_flat(3000, 2000, 0.5))
    assert abs(out.shape[0] - 1600) <= 1 and abs(out.shape[1] - 1067) <= 1


def test_small_image_is_not_upscaled():
    assert make_preview(_flat(100, 150, 0.5)).shape == (100, 150, 3)


def test_gamma_brightens():
    plain = make_preview(_flat(10, 10, 0.25))
    bright = make_preview(_flat(10, 10, 0.25), gamma=2.2)
    assert bright.mean() > plain.mean() + 20


def test_full_scale_maps_to_255_and_black_to_0():
    out = make_preview(np.stack([_flat(4, 4, 1.0), _flat(4, 4, 0.0)])[0])
    assert out.max() == 255
    assert make_preview(_flat(4, 4, 0.0)).max() == 0


def test_write_preview_creates_a_readable_jpeg(tmp_path):
    path = tmp_path / "deep" / "01-after.jpg"
    write_preview(make_preview(_flat(200, 300, 0.4)), path)
    with Image.open(path) as image:
        assert image.size == (300, 200) and image.format == "JPEG"
