import numpy as np

from opticfilm_linux import cli as cli_module
from opticfilm_linux.cli import main
from opticfilm_linux.model import HolderKind, RawSweep
from opticfilm_linux.sweepstore import save_sweep


def _scan(sweep_dir, out_dir, *extra):
    return main(["scan", "--fake", str(sweep_dir), "--out", str(out_dir), *extra])


def test_scan_fake_end_to_end(synthetic_sweep_dir, tmp_path, capsys):
    out = tmp_path / "out"
    assert _scan(synthetic_sweep_dir, out, "--dpi", "600") == 0
    assert "4 slides written, 0 blank skipped" in capsys.readouterr().out
    assert len(list(out.glob("*.tif"))) == 4


def test_process_end_to_end(synthetic_sweep_dir, tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["process", str(synthetic_sweep_dir), "--out", str(out), "--format", "jpeg"])
    assert code == 0
    assert "4 slides written, 0 blank skipped" in capsys.readouterr().out
    assert len(list(out.glob("*.jpg"))) == 4


def test_truncated_sweep_gives_exit_2_and_message(synthetic_sweep_dir, tmp_path, capsys):
    rgb_file = synthetic_sweep_dir / "rgb.npy"
    rgb_file.write_bytes(rgb_file.read_bytes()[: rgb_file.stat().st_size // 2])
    assert _scan(synthetic_sweep_dir, tmp_path / "out", "--dpi", "600") == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "Traceback" not in err


def test_dpi_mismatch_gives_exit_2(synthetic_sweep_dir, tmp_path, capsys):
    assert _scan(synthetic_sweep_dir, tmp_path / "out") == 2
    assert "600" in capsys.readouterr().err


def test_sweep_with_no_openings_gives_exit_2(tmp_path, capsys):
    blank = RawSweep(
        rgb=np.zeros((5339, 827, 3), np.uint16), ir=None, dpi=600, holder=HolderKind.SLIDE4
    )
    save_sweep(blank, tmp_path / "sweep")
    assert main(["process", str(tmp_path / "sweep"), "--out", str(tmp_path / "out")]) == 2
    assert capsys.readouterr().err.startswith("error:")


def test_blank_positions_are_named_in_the_summary(tmp_path, capsys):
    from synthetic import make_sweep

    save_sweep(make_sweep(dpi=600, filled=(True, False, True, True)), tmp_path / "sweep")
    assert main(["process", str(tmp_path / "sweep"), "--out", str(tmp_path / "out")]) == 0
    assert "3 slides written, 1 blank skipped (position 2)" in capsys.readouterr().out


def test_album_with_path_separator_gives_exit_2(synthetic_sweep_dir, tmp_path, capsys):
    code = main(
        ["process", str(synthetic_sweep_dir), "--out", str(tmp_path / "out"), "--album", "../x"]
    )
    assert code == 2
    assert capsys.readouterr().err.startswith("error:")


def test_no_infrared_flag_skips_dust_removal_and_the_ir_plane(
    synthetic_sweep_dir, tmp_path, monkeypatch, capsys
):
    seen = {}
    real = cli_module.process_sweep

    def spy(sweep, options, out):
        seen["ir"], seen["remove_dust"] = sweep.ir, options.remove_dust
        return real(sweep, options, out)

    monkeypatch.setattr(cli_module, "process_sweep", spy)
    code = _scan(synthetic_sweep_dir, tmp_path / "out", "--dpi", "600", "--no-infrared")
    assert code == 0
    assert seen["ir"] is None and seen["remove_dust"] is False
    assert "4 slides written" in capsys.readouterr().out


def test_process_accepts_no_infrared_to_skip_dust_removal(
    synthetic_sweep_dir, tmp_path, monkeypatch
):
    seen = {}
    real = cli_module.process_sweep

    def spy(sweep, options, out):
        seen["remove_dust"] = options.remove_dust
        return real(sweep, options, out)

    monkeypatch.setattr(cli_module, "process_sweep", spy)
    args = ["process", str(synthetic_sweep_dir), "--out", str(tmp_path / "out"), "--no-infrared"]
    assert main(args) == 0
    assert seen["remove_dust"] is False
