import numpy as np
import pytest

from opticfilm_linux.model import HolderKind, RawSweep
from opticfilm_linux.sweepstore import SweepFormatError, load_sweep, save_sweep, sweep_dpi


def _sweep(with_ir: bool = True) -> RawSweep:
    rng = np.random.default_rng(1)
    rgb = rng.integers(0, 65536, size=(40, 12, 3), dtype=np.uint16)
    ir = rng.integers(0, 65536, size=(40, 12), dtype=np.uint16) if with_ir else None
    return RawSweep(rgb=rgb, ir=ir, dpi=600, holder=HolderKind.SLIDE4)


def test_sweep_roundtrip(tmp_path):
    original = _sweep()
    save_sweep(original, tmp_path)
    loaded = load_sweep(tmp_path)
    assert loaded.rgb.dtype == np.uint16
    assert np.array_equal(loaded.rgb, original.rgb)
    assert np.array_equal(loaded.ir, original.ir)
    assert (loaded.dpi, loaded.holder) == (600, HolderKind.SLIDE4)


def test_loaded_arrays_are_memory_mapped_and_read_only(tmp_path):
    save_sweep(_sweep(), tmp_path)
    loaded = load_sweep(tmp_path)
    assert isinstance(loaded.rgb, np.memmap)
    assert loaded.rgb.flags.writeable is False


def test_ir_is_optional(tmp_path):
    save_sweep(_sweep(with_ir=False), tmp_path)
    assert load_sweep(tmp_path).ir is None


def test_truncated_rgb_file_raises_sweepformaterror(tmp_path):
    save_sweep(_sweep(), tmp_path)
    rgb_file = tmp_path / "rgb.npy"
    rgb_file.write_bytes(rgb_file.read_bytes()[: rgb_file.stat().st_size // 2])
    with pytest.raises(SweepFormatError, match="rgb.npy"):
        load_sweep(tmp_path)


def test_missing_meta_raises_sweepformaterror(tmp_path):
    save_sweep(_sweep(), tmp_path)
    (tmp_path / "meta.json").unlink()
    with pytest.raises(SweepFormatError, match="meta.json"):
        load_sweep(tmp_path)


def test_missing_directory_raises_sweepformaterror(tmp_path):
    with pytest.raises(SweepFormatError):
        load_sweep(tmp_path / "does-not-exist")


def test_sweep_dpi_reads_meta_without_loading_arrays(tmp_path):
    save_sweep(_sweep(), tmp_path)
    assert sweep_dpi(tmp_path) == 600
    with pytest.raises(SweepFormatError):
        sweep_dpi(tmp_path / "missing")
