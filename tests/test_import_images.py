import numpy as np
import pytest
import tifffile
from PIL import Image

from opticfilm_linux.cli import main
from opticfilm_linux.imports import load_image


def _gradient(h=60, w=90) -> np.ndarray:
    ramp = np.linspace(2000, 60000, w, dtype=np.uint16)
    return np.stack([np.tile(ramp, (h, 1))] * 3, axis=2)


def test_a_16_bit_tiff_loads_unchanged(tmp_path):
    original = _gradient()
    tifffile.imwrite(tmp_path / "a.tif", original)
    assert np.array_equal(load_image(tmp_path / "a.tif"), original)


def test_an_8_bit_image_is_scaled_to_the_full_16_bit_range(tmp_path):
    Image.fromarray(np.full((8, 8, 3), 255, np.uint8)).save(tmp_path / "w.png")
    loaded = load_image(tmp_path / "w.png")
    assert loaded.dtype == np.uint16 and int(loaded.max()) == 65535 and loaded.shape == (8, 8, 3)


def test_grey_and_alpha_images_become_plain_rgb(tmp_path):
    Image.fromarray(np.full((8, 8), 128, np.uint8)).save(tmp_path / "g.png")
    Image.fromarray(np.full((8, 8, 4), 100, np.uint8)).save(tmp_path / "a.png")
    assert load_image(tmp_path / "g.png").shape == (8, 8, 3)
    assert load_image(tmp_path / "a.png").shape == (8, 8, 3)


def test_an_unreadable_file_is_a_clear_error(tmp_path):
    (tmp_path / "bad.tif").write_bytes(b"not an image")
    with pytest.raises(ValueError, match="bad.tif"):
        load_image(tmp_path / "bad.tif")


def test_enhance_command_writes_one_finished_file_per_image(tmp_path, capsys):
    for name in ("one", "two"):
        tifffile.imwrite(tmp_path / f"{name}.tif", _gradient())
    out = tmp_path / "out"
    code = main(
        ["enhance", str(tmp_path / "one.tif"), str(tmp_path / "two.tif"), "--out", str(out)]
    )
    assert code == 0 and "2 slides written" in capsys.readouterr().out
    assert len(list(out.glob("*.tif"))) == 2


def test_enhance_command_reports_a_bad_file_and_exits_2(tmp_path, capsys):
    (tmp_path / "bad.tif").write_bytes(b"nope")
    assert main(["enhance", str(tmp_path / "bad.tif"), "--out", str(tmp_path / "out")]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "bad.tif" in err
