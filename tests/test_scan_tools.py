"""Tests for scripts/tools (scan_average, timing_model) using synthetic data only."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

TOOLS = Path(__file__).resolve().parents[1] / "scripts" / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sa = _load("scan_average")
tm = _load("timing_model")

H, W = 640, 704


def _field(seed: int):
    """Band-limited random RGB field as sinusoid parameters, evaluable at any coordinates."""
    rng = np.random.default_rng(seed)
    n = 160
    freq = rng.normal(0, 0.03, (3, n, 2))
    amp = rng.normal(0, 1, (3, n)) / np.sqrt(n / 2)
    phase = rng.uniform(0, 2 * np.pi, (3, n))
    return freq, amp, phase


def _render(field, sy=0.0, sx=0.0, k=1.0, h=H, w=W) -> np.ndarray:
    """img(q) = F(k * (q - s)) for q on the h x w grid; mean 28000, sd about 5000."""
    freq, amp, phase = field
    y = k * (np.arange(h) - sy)
    x = k * (np.arange(w) - sx)
    out = np.empty((h, w, 3))
    for c in range(3):
        ay = 2 * np.pi * np.outer(y, freq[c, :, 0]) + phase[c]
        ax = 2 * np.pi * np.outer(x, freq[c, :, 1])
        wa = amp[c]
        f = (np.cos(ay) * wa) @ np.cos(ax).T - (np.sin(ay) * wa) @ np.sin(ax).T
        out[:, :, c] = 28000 + 5000 * f
    return out


def _scan(field, sy, sx, sigma, seed, k=1.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = _render(field, sy, sx, k) + rng.normal(0, sigma, (H, W, 3))
    return np.clip(np.rint(img), 0, 65535).astype(np.uint16)


def test_phase_correlate_recovers_subpixel_shift():
    field = _field(1)
    a = _render(field).mean(axis=2).astype(np.float32)
    b = _render(field, 3.37, -2.21).mean(axis=2).astype(np.float32)
    dy, dx, peak = sa.phase_correlate(a, b)
    assert dy == pytest.approx(3.37, abs=0.1)
    assert dx == pytest.approx(-2.21, abs=0.1)
    assert peak > 15


def test_average_recovers_shifts_and_reduces_noise(tmp_path):
    field = _field(2)
    sigma = 300.0
    truth = [(0.0, 0.0), (1.4, -2.6), (-3.3, 4.75), (2.2, 1.1), (-1.6, -0.4)]
    paths = []
    for i, (sy, sx) in enumerate(truth):
        p = tmp_path / f"s{i}.tiff"
        tifffile.imwrite(
            p,
            _scan(field, sy, sx, sigma, 100 + i),
            photometric="rgb",
            resolution=(3600, 3600),
            resolutionunit=2,
        )
        paths.append(p)
    out, meta, lines = sa.average(paths)
    text = "\n".join(lines)
    ref = sa.luminance(sa.read_tiff(paths[0])[0])
    for p, (sy, sx) in zip(paths[1:], truth[1:], strict=True):
        reg = sa.register(ref, sa.luminance(sa.read_tiff(p)[0]))
        assert reg.dy == pytest.approx(sy, abs=0.1)
        assert reg.dx == pytest.approx(sx, abs=0.1)
        assert abs(reg.scale_y - 1) < 1e-3 and abs(reg.scale_x - 1) < 1e-3
    assert "noise_measured_vs_n" in text
    assert meta["resolution"] == (3600.0, 3600.0)
    assert out.dtype == np.uint16 and out.shape[0] < H and out.shape[1] < W


def test_noise_drops_roughly_one_over_sqrt_n(tmp_path):
    field = _field(3)
    sigma = 300.0
    shifts = [(0.0, 0.0), (0.7, 1.3), (-1.2, 0.6), (1.9, -0.8), (-0.5, -1.7)]
    paths = []
    for i, (sy, sx) in enumerate(shifts):
        p = tmp_path / f"n{i}.tiff"
        tifffile.imwrite(p, _scan(field, sy, sx, sigma, 200 + i), photometric="rgb")
        paths.append(p)
    _, _, lines = sa.average(paths)
    curve_line = next(ln for ln in lines if ln.startswith("noise_measured_vs_n"))
    vals = [float(tok.split("=")[1]) for tok in curve_line.split()[1:]]
    assert len(vals) == 4
    assert vals[0] == pytest.approx(sigma, rel=0.25)
    # n = 4: expect ~ sigma / 2 (registration and Fourier-shift add a little)
    assert vals[3] == pytest.approx(sigma / 2, rel=0.3)
    assert vals[3] < vals[1] < vals[0]


def test_different_image_is_refused(tmp_path):
    a = tmp_path / "a.tiff"
    b = tmp_path / "b.tiff"
    tifffile.imwrite(a, _scan(_field(4), 0, 0, 200, 1), photometric="rgb")
    tifffile.imwrite(b, _scan(_field(5), 0, 0, 200, 2), photometric="rgb")
    with pytest.raises(sa.RegistrationError):
        sa.average([a, b])
    assert sa.main([str(tmp_path / "o.tiff"), str(a), str(b)]) == 2
    assert not (tmp_path / "o.tiff").exists()


def test_cli_writes_16bit_tiff_with_resolution(tmp_path, capsys):
    field = _field(6)
    paths = []
    for i, (sy, sx) in enumerate([(0, 0), (0.9, -1.1)]):
        p = tmp_path / f"c{i}.tiff"
        tifffile.imwrite(
            p,
            _scan(field, sy, sx, 100, 300 + i),
            photometric="rgb",
            resolution=(3600, 3600),
            resolutionunit=2,
        )
        paths.append(p)
    out = tmp_path / "avg.tiff"
    assert sa.main([str(out), *map(str, paths)]) == 0
    assert "register c1.tiff" in capsys.readouterr().out
    with tifffile.TiffFile(out) as tf:
        page = tf.pages.first
        assert page.dtype == np.uint16 and page.shape[2] == 3
        assert page.tags["XResolution"].value == (3600, 1)


# ---- timing_model ---------------------------------------------------------------------


def _diag(frame: int, scan=44.0, dpi=3600, lines=11696, overscan=1.5) -> dict:
    ph = {
        "scan": scan,
        "position": 1.7 + 3.5 * (frame - 1),
        "park": 3.7 + 3.5 * (frame - 1),
        "cal_dark_a": 0.5,
        "cal_shading_measure": 2.5,
        "cal_shading_verify": 2.0,
    }
    return {
        "dpi": dpi,
        "frame": frame,
        "lines": lines,
        "overscan_mm": overscan,
        "phase_seconds": ph,
        "total_seconds": sum(ph.values()) + 0.4,
    }


@pytest.fixture
def model():
    return tm.fit_model([_diag(k) for k in (1, 2, 3, 4)])


def test_fit_model_recovers_phases(model):
    assert model.scan == pytest.approx(44.0)
    assert model.position_slope == pytest.approx(3.5)
    assert model.position_intercept == pytest.approx(1.7)
    assert model.calibration == pytest.approx(5.0)
    assert model.overhead == pytest.approx(0.4)


def test_fit_model_needs_baseline_dpi():
    with pytest.raises(ValueError):
        tm.fit_model([_diag(1, dpi=7200)])


def test_estimate_baseline_and_options(model):
    base = tm.estimate(model, n_slides=8, tray_size=4)
    expect_opening = [44 + 1.7 + 3.7 + 7 * k + 5 + 0.4 for k in range(4)]
    assert base.opening_seconds == pytest.approx(expect_opening)
    assert base.total_seconds == pytest.approx(2 * (sum(expect_opening) + 30))
    reuse = tm.estimate(model, n_slides=8, cal_reuse=True)
    assert base.total_seconds - reuse.total_seconds == pytest.approx(2 * 3 * 5.0)
    post = tm.estimate(model, n_slides=8, post_overlap=True)
    assert base.total_seconds - post.total_seconds == pytest.approx(8 * 0.4)
    swap = tm.estimate(model, n_slides=8, swap_seconds=0)
    assert base.total_seconds - swap.total_seconds == pytest.approx(60)


def test_estimate_dpi_and_overscan_scale_scan_only(model):
    base = tm.estimate(model, n_slides=4, swap_seconds=0)
    hi = tm.estimate(model, n_slides=4, swap_seconds=0, dpi=7200)
    assert hi.total_seconds - base.total_seconds == pytest.approx(4 * 3 * 44.0)
    short = tm.estimate(model, n_slides=4, swap_seconds=0, overscan_mm=0.5)
    ratio = (11696 - 576) / 11696
    assert base.total_seconds - short.total_seconds == pytest.approx(4 * 44.0 * (1 - ratio))


def test_estimate_partial_tray_and_validation(model):
    est = tm.estimate(model, n_slides=5, tray_size=4)
    assert est.n_trays == 2
    with pytest.raises(ValueError):
        tm.estimate(model, tray_size=0)


def test_cli_prints_hours_and_help_states_unmeasured(tmp_path, capsys):
    paths = []
    for k in (1, 2):
        p = tmp_path / f"d{k}.diag.json"
        p.write_text(json.dumps(_diag(k)))
        paths.append(str(p))
    assert tm.main([*paths, "--slides", "100"]) == 0
    assert "hours" in capsys.readouterr().out
    helptext = " ".join(tm.build_parser().format_help().split())
    assert "UNMEASURED" not in helptext
    assert "MEASURED on 2026-11-07" in helptext
    assert "recalibrates every opening" in helptext
    assert "PROJECTION" in helptext


def test_scale_error_is_reported_and_corrected(tmp_path):
    field = _field(7)
    k = 1.0005  # image is slightly magnified relative to the reference
    paths = []
    for i, (sy, sx, kk) in enumerate([(0.0, 0.0, 1.0), (1.2, -0.8, k), (-0.7, 0.9, k)]):
        p = tmp_path / f"k{i}.tiff"
        tifffile.imwrite(p, _scan(field, sy, sx, 150, 400 + i, kk), photometric="rgb")
        paths.append(p)
    ref = sa.luminance(sa.read_tiff(paths[0])[0])
    reg = sa.register(ref, sa.luminance(sa.read_tiff(paths[1])[0]))
    assert reg.scale_y == pytest.approx(1 / k, abs=1.5e-4)
    assert reg.residual < 0.15
    assert reg.translation_residual > reg.residual
    out, _, _ = sa.average(paths)
    assert out.shape[0] > 400


# ---- scan_average: ground truth, guards, output safety ---------------------------------


def _render_affine(field, k: float, theta_deg: float, n: int = 448) -> np.ndarray:
    """img(q) = F(k * R(theta) (q - centre) + centre): scaled and rotated about the centre."""
    freq, amp, phase = field
    th = np.radians(theta_deg)
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float64) - n / 2
    ys = k * (np.cos(th) * yy - np.sin(th) * xx) + n / 2
    xs = k * (np.sin(th) * yy + np.cos(th) * xx) + n / 2
    out = np.empty((n, n, 3))
    for c in range(3):
        acc = np.zeros((n, n))
        for i in range(0, freq.shape[1], 20):
            f = freq[c, i : i + 20]
            arg = (
                2 * np.pi * (ys[..., None] * f[:, 0] + xs[..., None] * f[:, 1])
                + phase[c, i : i + 20]
            )
            acc += (np.cos(arg) * amp[c, i : i + 20]).sum(axis=2)
        out[:, :, c] = 28000 + 5000 * acc
    return np.clip(np.rint(out), 0, 65535).astype(np.uint16)


@pytest.mark.parametrize("shift", [(2.4, -3.7), (-1.3, 0.45), (5.0, 2.0)])
def test_warp_crop_matches_ground_truth_for_known_shift(shift):
    field = _field(11)
    img = np.clip(np.rint(_render(field, *shift)), 0, 65535).astype(np.uint16)
    truth = _render(field)
    reg = sa.Registration(*shift, 0.0, 0.0, 0.0, 0.0, (H / 2, W / 2), 0.0, 0.0, 9, 99.0)
    area = (60, H - 60, 60, W - 60)
    out = sa.warp_crop(img, reg, area)
    err = out - truth[area[0] : area[1], area[2] : area[3]]
    assert np.sqrt(np.mean(err**2)) < 8.0  # wrong sign or direction gives thousands


def test_average_matches_ground_truth_end_to_end(tmp_path):
    field = _field(12)
    shifts = [(0.0, 0.0), (1.4, -2.6), (-2.2, 3.3)]
    paths = []
    for i, (sy, sx) in enumerate(shifts):
        p = tmp_path / f"g{i}.tiff"
        tifffile.imwrite(p, _scan(field, sy, sx, 0.0, i), photometric="rgb")
        paths.append(p)
    out, _, _ = sa.average(paths)
    ref_lum = sa.luminance(sa.read_tiff(paths[0])[0])
    regs = [sa.IDENTITY] + [
        sa.register(ref_lum, sa.luminance(sa.read_tiff(p)[0])) for p in paths[1:]
    ]
    r0, r1, c0, c1 = sa.common_area([(H, W)] * 3, regs, (H, W))
    assert out.shape[:2] == (r1 - r0, c1 - c0)
    err = out - _render(field)[r0:r1, c0:c1]
    assert np.sqrt(np.mean(err**2)) < 5.0


def test_scale_and_rotation_guards_refuse():
    field = _field(13)
    ref = sa.luminance(_render_affine(field, 1.0, 0.0))
    assert sa.register(ref, sa.luminance(_render_affine(field, 1.0, 0.0))).residual < 0.1
    with pytest.raises(sa.RegistrationError, match="not small"):
        sa.register(ref, sa.luminance(_render_affine(field, 1.02, 0.0)), max_residual=50)
    with pytest.raises(sa.RegistrationError, match="not small"):
        sa.register(ref, sa.luminance(_render_affine(field, 1.0, 1.0)), max_residual=50)


def test_residual_gate_refuses_inconsistent_tiles_alone():
    field = _field(14)
    top = _scan(field, 0.0, 2.0, 50, 1)[: H // 2]
    bottom = _scan(field, 0.0, -2.0, 50, 2)[H // 2 :]
    ref = sa.luminance(_scan(field, 0.0, 0.0, 50, 3))
    img = sa.luminance(np.concatenate([top, bottom]))
    with pytest.raises(sa.RegistrationError, match="residual"):
        sa.register(ref, img)  # default 0.5 px gate; scale/rotation stay in range
    assert sa.register(ref, img, max_residual=50).residual > 0.5


def _tiny(path: Path, value: int = 1000) -> Path:
    tifffile.imwrite(path, np.full((8, 8, 3), value, np.uint16), photometric="rgb")
    return path


@pytest.fixture
def ok_average(monkeypatch):
    """average() succeeds trivially, so only the path guards can make main() refuse."""
    result = np.zeros((8, 8, 3), np.uint16)
    monkeypatch.setattr(sa, "average", lambda paths, residual: (result, {}, ["ok"]))


def test_output_equal_to_input_is_refused(tmp_path, capsys, ok_average):
    a, b = _tiny(tmp_path / "a.tiff"), _tiny(tmp_path / "b.tiff", 2000)
    before = a.read_bytes()
    assert sa.main([str(a), str(a), str(b), "--force"]) == 2
    assert a.read_bytes() == before
    assert "also an input" in capsys.readouterr().err


def test_output_symlink_to_input_is_refused(tmp_path, ok_average):
    a, b = _tiny(tmp_path / "a.tiff"), _tiny(tmp_path / "b.tiff", 2000)
    link = tmp_path / "link.tiff"
    link.symlink_to(a)
    before = a.read_bytes()
    assert sa.main([str(link), str(a), str(b), "--force"]) == 2
    assert a.read_bytes() == before and link.is_symlink()


def test_output_symlink_is_refused_even_to_a_third_file(tmp_path, ok_average, capsys):
    a, b = _tiny(tmp_path / "a.tiff"), _tiny(tmp_path / "b.tiff", 2000)
    other = tmp_path / "other.tiff"
    other.write_bytes(b"third")
    link = tmp_path / "link.tiff"
    link.symlink_to(other)
    assert sa.main([str(link), str(a), str(b), "--force"]) == 2
    assert "symlink" in capsys.readouterr().err
    assert other.read_bytes() == b"third"
    dangling = tmp_path / "dangling.tiff"
    dangling.symlink_to(tmp_path / "nowhere.tiff")
    assert sa.main([str(dangling), str(a), str(b)]) == 2
    assert not (tmp_path / "nowhere.tiff").exists()


def test_existing_output_needs_force(tmp_path, ok_average, capsys):
    a, b = _tiny(tmp_path / "a.tiff"), _tiny(tmp_path / "b.tiff", 2000)
    out = tmp_path / "out.tiff"
    out.write_bytes(b"precious")
    assert sa.main([str(out), str(a), str(b)]) == 2
    assert "--force" in capsys.readouterr().err
    assert out.read_bytes() == b"precious"
    assert sa.main([str(out), str(a), str(b), "--force"]) == 0
    assert tifffile.imread(out).shape == (8, 8, 3)


def test_duplicate_input_is_refused(tmp_path, ok_average, capsys):
    a = _tiny(tmp_path / "a.tiff")
    alias = tmp_path / "alias.tiff"
    alias.symlink_to(a)
    assert sa.main([str(tmp_path / "o.tiff"), str(a), str(a)]) == 2
    assert "more than once" in capsys.readouterr().err
    assert sa.main([str(tmp_path / "o.tiff"), str(a), str(alias)]) == 2
    assert not (tmp_path / "o.tiff").exists()


def test_failed_write_leaves_no_partial_file(tmp_path, monkeypatch, capsys):
    a, b = _tiny(tmp_path / "a.tiff"), _tiny(tmp_path / "b.tiff", 2000)
    out = tmp_path / "out.tiff"
    out.write_bytes(b"precious")

    def boom(path, data, **kwargs):
        Path(path).write_bytes(b"partial")
        raise OSError("disk full")

    result = np.zeros((8, 8, 3), np.uint16)
    monkeypatch.setattr(sa, "average", lambda paths, residual: (result, {}, []))
    monkeypatch.setattr(sa.tifffile, "imwrite", boom)
    assert sa.main([str(out), str(a), str(b), "--force"]) == 2
    assert "could not write" in capsys.readouterr().err
    assert out.read_bytes() == b"precious"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.tiff", "b.tiff", "out.tiff"]


def test_metadata_is_preserved_and_description_dropped(tmp_path):
    field = _field(15)
    icc = b"fake-icc-profile-bytes"
    paths = []
    for i, (sy, sx) in enumerate([(0.0, 0.0), (0.8, -0.6)]):
        p = tmp_path / f"m{i}.tiff"
        tifffile.imwrite(
            p,
            _scan(field, sy, sx, 100, 500 + i),
            photometric="rgb",
            resolution=(3600, 3600),
            resolutionunit=2,
            description="uncropped original",
            extratags=[(34675, 7, len(icc), icc, True)],
        )
        paths.append(p)
    out = tmp_path / "avg.tiff"
    assert sa.main([str(out), *map(str, paths)]) == 0
    with tifffile.TiffFile(out) as tf:
        page = tf.pages.first
        assert page.dtype == np.uint16
        assert page.tags["XResolution"].value == (3600, 1)
        assert page.tags["ResolutionUnit"].value == 2
        assert bytes(page.tags[34675].value) == icc
        assert "ImageDescription" not in page.tags


def test_symlink_alias_of_output_among_inputs_is_refused(tmp_path, ok_average, capsys):
    out = _tiny(tmp_path / "real.tiff")
    other = _tiny(tmp_path / "other.tiff", 2000)
    alias = tmp_path / "alias.tiff"
    alias.symlink_to(out)
    before = out.read_bytes()
    assert sa.main([str(out), str(other), str(alias), "--force"]) == 2
    assert "also an input" in capsys.readouterr().err
    assert out.read_bytes() == before


def test_missing_output_directory_is_refused_before_any_work(tmp_path, monkeypatch, capsys):
    a, b = _tiny(tmp_path / "a.tiff"), _tiny(tmp_path / "b.tiff", 2000)

    def never(*args, **kwargs):
        raise AssertionError("average() must not run")

    monkeypatch.setattr(sa, "average", never)
    assert sa.main([str(tmp_path / "nodir" / "o.tiff"), str(a), str(b)]) == 2
    assert "does not exist" in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_output_directory_is_refused(tmp_path, ok_average, capsys):
    a, b = _tiny(tmp_path / "a.tiff"), _tiny(tmp_path / "b.tiff", 2000)
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        assert sa.main([str(ro / "o.tiff"), str(a), str(b)]) == 2
        assert "not writable" in capsys.readouterr().err
    finally:
        ro.chmod(0o700)


def test_output_mode_follows_umask(tmp_path, ok_average):
    a, b = _tiny(tmp_path / "a.tiff"), _tiny(tmp_path / "b.tiff", 2000)
    out = tmp_path / "o.tiff"
    old = os.umask(0o022)
    try:
        assert sa.main([str(out), str(a), str(b)]) == 0
    finally:
        os.umask(old)
    assert stat.S_IMODE(out.stat().st_mode) == 0o644


def test_lanczos_taps_are_zero_outside_the_window():
    for frac in (-0.5, 0.0, 0.3, 0.5):
        taps = sa._taps(frac)
        assert taps.sum() == pytest.approx(1.0, abs=1e-6)
    assert sa._taps(-0.5)[-1] == 0.0  # j = A sits 12.5 px from the sample point
    assert sa._taps(0.5)[0] != 0.0
    assert np.allclose(sa._taps(0.0), np.eye(1, 2 * sa.LANCZOS_A, sa.LANCZOS_A - 1)[0], atol=1e-6)
