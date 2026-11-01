from io import BytesIO

import numpy as np
import pytest
import tifffile
from PIL import Image

from opticfilm_linux.output import OutputSpec, write_slide

ICC_TAG = "InterColorProfile"


@pytest.mark.parametrize(
    "width,height,dpi,size,expected",
    [
        (9600, 6500, 7200, "3600", (4800, 3250, 3600)),
        (101, 99, 7200, "3600", (51, 50, 3600)),
        (99, 101, 7200, "3600", (50, 51, 3600)),
        (99, 101, 3600, "3600", (99, 101, 3600)),
        (99, 101, 600, "3600", (99, 101, 600)),
        (9600, 6500, 7200, "full", (9600, 6500, 7200)),
        (17, 11, 600, "full", (17, 11, 600)),
        (1, 1, 7200, "3600", (1, 1, 3600)),
        (101, 99, 4800, "3600", (76, 74, 3600)),
    ],
)
def test_jpeg_geometry_uses_sweep_dpi(width, height, dpi, size, expected):
    from opticfilm_linux.output import jpeg_geometry

    assert jpeg_geometry(width, height, dpi, size) == expected


@pytest.mark.parametrize("dpi", [0, -7200, 1.5, "7200", True, float("inf"), float("nan")])
def test_jpeg_geometry_rejects_invalid_dpi(dpi):
    from opticfilm_linux.output import jpeg_geometry

    with pytest.raises(ValueError, match="dpi"):
        jpeg_geometry(101, 99, dpi, "3600")


def test_jpeg_geometry_rejects_unknown_size():
    from opticfilm_linux.output import jpeg_geometry

    with pytest.raises(ValueError, match="size"):
        jpeg_geometry(101, 99, 7200, "invalid")


@pytest.mark.parametrize("shape", [(99, 101), (101, 99), (17, 63)])
@pytest.mark.parametrize("dpi", [600, 3600, 4800, 7200])
@pytest.mark.parametrize("size", ["full", "3600"])
def test_jpeg_lanczos_and_resolution(tmp_path, shape, dpi, size):
    rgb = np.random.default_rng(17).integers(0, 65536, (*shape, 3), dtype=np.uint16)
    divisor = max(1, dpi / 3600) if size == "3600" else 1
    expected_size = tuple(max(1, int(side / divisor + 0.5)) for side in shape[::-1])
    expected = Image.fromarray(((rgb.astype(np.uint32) + 128) // 257).astype(np.uint8))
    if expected.size != expected_size:
        expected = expected.resize(expected_size, Image.Resampling.LANCZOS)
    reference = BytesIO()
    expected.save(reference, format="JPEG", quality=95)
    reference.seek(0)
    path = write_slide(
        rgb, OutputSpec(directory=tmp_path, fmt="jpeg", scan_dpi=dpi, jpeg_size=size)
    )
    with Image.open(path) as image, Image.open(reference) as decoded:
        assert image.size == expected_size
        assert np.array_equal(np.asarray(image), np.asarray(decoded))
        delivered_dpi = min(dpi, 3600) if size == "3600" else dpi
        assert image.getexif()[282] == image.getexif()[283] == delivered_dpi
        assert image.getexif()[296] == 2
        assert image.info["icc_profile"]


@pytest.mark.parametrize("fmt", ["tiff16", "png8"])
def test_non_jpeg_pixels_unchanged(tmp_path, fmt):
    rgb = _image()
    path = write_slide(
        rgb, OutputSpec(directory=tmp_path, fmt=fmt, scan_dpi=7200, jpeg_size="3600")
    )
    if fmt == "tiff16":
        assert np.array_equal(tifffile.imread(path), rgb)
    else:
        with Image.open(path) as image:
            expected = ((rgb.astype(np.uint32) + 128) // 257).astype(np.uint8)
            assert np.array_equal(np.asarray(image), expected)


@pytest.mark.parametrize("dpi", [0, -1, 1.5, "7200", True, float("inf"), float("nan")])
@pytest.mark.parametrize("size", ["full", "3600"])
def test_invalid_jpeg_dpi_fails_before_opening_output(tmp_path, monkeypatch, dpi, size):
    def unexpected_open(*_args, **_kwargs):
        pytest.fail("invalid DPI opened an output file")

    monkeypatch.setattr("opticfilm_linux.output.open", unexpected_open, raising=False)
    with pytest.raises(ValueError, match="dpi"):
        write_slide(_image(), OutputSpec(tmp_path, "jpeg", scan_dpi=dpi, jpeg_size=size))
    assert list(tmp_path.iterdir()) == []


def test_legacy_jpeg_without_scan_dpi_keeps_original_pixels(tmp_path):
    rgb = _image()
    original = write_slide(rgb, OutputSpec(tmp_path, "jpeg"))
    sized = write_slide(rgb, OutputSpec(tmp_path, "jpeg", jpeg_size="3600"))
    assert sized.read_bytes() == original.read_bytes()


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


@pytest.mark.parametrize("album", ["../escaped", "a/b", "..", "a\\b"])
def test_album_with_path_parts_is_rejected(tmp_path, album):
    with pytest.raises(ValueError, match="album"):
        OutputSpec(directory=tmp_path, album=album)


def test_date_with_path_parts_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="date"):
        OutputSpec(directory=tmp_path, date="1975/06")


def test_interrupt_during_encode_leaves_no_partial_file(tmp_path, monkeypatch):
    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("opticfilm_linux.output._encode", interrupted)
    with pytest.raises(KeyboardInterrupt):
        write_slide(_image(), OutputSpec(directory=tmp_path))
    assert list(tmp_path.iterdir()) == []
