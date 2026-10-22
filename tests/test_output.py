import numpy as np
import pytest
import tifffile
from PIL import Image

from opticfilm_linux.output import OutputSpec, write_slide

ICC_TAG = "InterColorProfile"


def _image(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 65536, size=(24, 32, 3), dtype=np.uint16)


def test_tiff16_roundtrip_is_lossless(tmp_path):
    image = _image()
    path = write_slide(image, OutputSpec(directory=tmp_path))
    back = tifffile.imread(path)
    assert back.dtype == np.uint16 and np.array_equal(back, image)


def test_icc_profile_embedded_in_tiff_png_and_jpeg(tmp_path):
    tiff = write_slide(_image(), OutputSpec(directory=tmp_path, fmt="tiff16"))
    with tifffile.TiffFile(tiff) as handle:
        assert ICC_TAG in handle.pages[0].tags
    for fmt in ("png8", "jpeg"):
        path = write_slide(_image(), OutputSpec(directory=tmp_path, fmt=fmt))
        assert Image.open(path).info.get("icc_profile")


def test_sequence_numbers_increase(tmp_path):
    spec = OutputSpec(directory=tmp_path)
    names = [write_slide(_image(i), spec).name for i in range(3)]
    assert names == ["0001.tif", "0002.tif", "0003.tif"]


def test_never_overwrites_existing_file(tmp_path):
    existing = tmp_path / "0001.tif"
    existing.write_bytes(b"keep me")
    path = write_slide(_image(), OutputSpec(directory=tmp_path))
    assert path.name == "0002.tif"
    assert existing.read_bytes() == b"keep me"


def test_two_runs_into_same_folder_keep_all_files(tmp_path):
    spec = OutputSpec(directory=tmp_path, album="Trip", date="1975-06")
    first = [write_slide(_image(i), spec) for i in range(4)]
    second = [write_slide(_image(i + 10), spec) for i in range(4)]
    assert len({*first, *second}) == 8
    assert len(list(tmp_path.iterdir())) == 8


def test_album_and_date_in_name(tmp_path):
    spec = OutputSpec(directory=tmp_path, album="Family Trips", date="1975-06")
    assert write_slide(_image(), spec).name == "Family Trips_1975-06_0001.tif"


def test_album_without_date_in_name(tmp_path):
    assert (
        write_slide(_image(), OutputSpec(directory=tmp_path, album="Trip")).name == "Trip_0001.tif"
    )


def test_jpeg_is_8bit_rgb(tmp_path):
    path = write_slide(_image(), OutputSpec(directory=tmp_path, fmt="jpeg"))
    assert path.suffix == ".jpg"
    assert Image.open(path).mode == "RGB"


def test_missing_directory_is_created(tmp_path):
    target = tmp_path / "deep" / "er"
    assert write_slide(_image(), OutputSpec(directory=target)).parent == target


def test_unknown_format_raises(tmp_path):
    with pytest.raises(ValueError, match="format"):
        write_slide(_image(), OutputSpec(directory=tmp_path, fmt="bmp"))  # type: ignore[arg-type]


def test_failed_encode_leaves_no_partial_file(tmp_path):
    bad = np.zeros((8, 8), dtype=np.uint16)  # not an RGB image
    with pytest.raises(ValueError):
        write_slide(bad, OutputSpec(directory=tmp_path, fmt="jpeg"))
    assert list(tmp_path.iterdir()) == []
