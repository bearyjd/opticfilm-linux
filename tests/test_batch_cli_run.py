"""``ofscan batch run`` and ``free-held``: headless scan -> accept -> export -> free (fake only)."""

import ast
import json
from pathlib import Path

import pytest

from opticfilm_linux.app import batch_rules
from opticfilm_linux.batch import export as exports
from opticfilm_linux.batch import run_cli
from opticfilm_linux.batch.model import BatchDefaults, TrayStage
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.cli import main
from opticfilm_linux.scanner import interrupted
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner


@pytest.fixture
def batch(tmp_path):
    path = tmp_path / "b"
    assert main(["batch", "new", str(path), "--name", "Trips", "--dpi", "600"]) == 0
    return path


@pytest.fixture
def collection_batch(tmp_path):
    path = tmp_path / "c"
    BatchStore.create(path, "Fam", defaults=BatchDefaults(dpi=600), collection="Fam").close()
    return path


def _run(path, sweep, *extra):
    return main(["batch", "run", str(path), "--fake", str(sweep), *extra])


def _trays(path):
    with BatchStore.open(path, readonly=True) as store:
        return store.snapshot().trays


def _defaults(path):
    with BatchStore.open(path, readonly=True) as store:
        return store.snapshot().defaults


def test_auto_export_on_writes_jpegs_and_holds_tray_one(
    batch, synthetic_sweep_dir, tmp_path, capsys
):
    out = tmp_path / "album"
    code = _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export")
    text = capsys.readouterr().out
    assert code == 0
    assert len(list(out.glob("*.jpg"))) == 4
    (tray,) = _trays(batch)
    assert tray.auto == "held" and not tray.raw_removed
    assert f"ofscan batch free-held {batch} 1 --yes" in text
    assert "raw kept for tray 1" in text


def test_flags_are_persisted_before_scanning(
    batch, synthetic_sweep_dir, tmp_path, monkeypatch, capsys
):
    out = tmp_path / "album"
    seen = {}

    def spy():
        seen["defaults"] = _defaults_while_open(batch)

    scanner = FakeScanner(synthetic_sweep_dir, before_scan=spy)
    args = ["batch", "run", str(batch), "--fake", str(synthetic_sweep_dir)]
    flags = ["--out", str(out), "--auto-export", "--keep-raw", "--jpeg-size", "3600"]
    monkeypatch.setattr(run_cli, "FakeScanner", lambda _sweep: scanner)
    assert main([*args, *flags]) == 0
    expected = (str(out), True, True, "3600")
    assert seen["defaults"] == expected
    final = _defaults(batch)
    assert (final.export_dir, final.auto_export, final.keep_raw, final.jpeg_size) == expected
    printed = capsys.readouterr().out
    assert "auto_export" in printed and "jpeg_size" in printed


def _defaults_while_open(path):
    raw = json.loads((path / "batch.json").read_text())["defaults"]
    return (
        raw.get("export_dir"),
        raw.get("auto_export"),
        raw.get("keep_raw"),
        raw.get("jpeg_size"),
    )


def test_auto_export_off_leaves_tray_in_review_and_says_manual(
    batch, synthetic_sweep_dir, tmp_path, capsys
):
    out = tmp_path / "album"
    assert _run(batch, synthetic_sweep_dir, "--out", str(out), "--no-auto-export") == 0
    (tray,) = _trays(batch)
    assert tray.stage is TrayStage.REVIEW and tray.auto == "off"
    assert not out.exists() or not list(out.glob("*.jpg"))
    assert "manual" in capsys.readouterr().out


def test_free_held_needs_yes_then_frees_raw(batch, synthetic_sweep_dir, tmp_path, capsys):
    out = tmp_path / "album"
    _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export")
    capsys.readouterr()
    assert main(["batch", "free-held", str(batch), "1"]) == 2
    assert not _trays(batch)[0].raw_removed
    assert main(["batch", "free-held", str(batch), "1", "--yes"]) == 0
    assert _trays(batch)[0].raw_removed
    assert len(list(out.glob("*.jpg"))) == 4


def test_free_held_refuses_a_tray_that_is_not_held(batch, synthetic_sweep_dir, capsys):
    _run(batch, synthetic_sweep_dir, "--no-auto-export")
    assert main(["batch", "free-held", str(batch), "1", "--yes"]) == 2
    assert "not held" in capsys.readouterr().err
    assert main(["batch", "free-held", str(batch), "9", "--yes"]) == 2


def test_two_fake_trays_free_raw_after_release(batch, synthetic_sweep_dir, tmp_path):
    out = tmp_path / "album"
    assert _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export", "--count", "2") == 0
    first, second = _trays(batch)
    assert first.auto == "held" and not first.raw_removed
    assert not second.raw_removed
    assert main(["batch", "free-held", str(batch), "1", "--yes"]) == 0
    first, second = _trays(batch)
    assert first.raw_removed and second.raw_removed
    assert len(list(out.glob("*.jpg"))) == 8


@pytest.mark.parametrize("where", ["inside", "symlink", "none"])
def test_destination_refusals_scan_nothing(batch, synthetic_sweep_dir, tmp_path, capsys, where):
    if where == "inside":
        flags = ["--out", str(batch / "out"), "--auto-export"]
    elif where == "symlink":
        (tmp_path / "dead").symlink_to(tmp_path / "missing")
        flags = ["--out", str(tmp_path / "dead" / "x"), "--auto-export"]
    else:
        flags = ["--auto-export"]
    assert _run(batch, synthetic_sweep_dir, *flags) == 3
    assert "refused" in capsys.readouterr().err
    assert _trays(batch) == ()
    assert _defaults(batch) == BatchDefaults(dpi=600)


@pytest.mark.parametrize("flags", [["--count", "0"], ["--jpeg-size", "9"], ["--box", "0"]])
def test_bad_flags_are_argument_errors(batch, synthetic_sweep_dir, flags):
    with pytest.raises(SystemExit) as raised:
        _run(batch, synthetic_sweep_dir, *flags)
    assert raised.value.code == 2


def test_box_and_section_apply_on_an_empty_section(collection_batch, synthetic_sweep_dir):
    assert _run(collection_batch, synthetic_sweep_dir, "--box", "2", "--section", "b") == 0
    with BatchStore.open(collection_batch, readonly=True) as store:
        manifest = store.snapshot()
    assert (manifest.box, manifest.section) == (2, "b")
    assert manifest.trays[0].location.box == 2


def test_box_section_change_refused_once_section_has_trays(
    collection_batch, synthetic_sweep_dir, capsys
):
    assert _run(collection_batch, synthetic_sweep_dir) == 0
    capsys.readouterr()
    assert _run(collection_batch, synthetic_sweep_dir, "--section", "b") == 3
    assert "section" in capsys.readouterr().err
    assert len(_trays(collection_batch)) == 1
    assert _run(collection_batch, synthetic_sweep_dir, "--section", "a") == 0  # unchanged: fine


def test_box_without_a_collection_is_refused(batch, synthetic_sweep_dir):
    assert _run(batch, synthetic_sweep_dir, "--box", "2") == 3
    assert _trays(batch) == ()


def test_real_scanner_refuses_more_than_one_tray_before_building_it(batch, monkeypatch, capsys):
    def never():
        raise AssertionError("a refused run must not touch the scanner")

    monkeypatch.setattr(run_cli, "real_scanner", never)
    assert main(["batch", "run", str(batch), "--count", "2"]) == 3
    assert "one holder" in capsys.readouterr().err
    assert _trays(batch) == ()


def test_real_scanner_not_ready_is_refused(batch, synthetic_sweep_dir, monkeypatch, capsys):
    scanner = FakeScanner(synthetic_sweep_dir, statuses=(ScannerStatus.NOT_READY,))
    monkeypatch.setattr(run_cli, "real_scanner", lambda: scanner)
    assert main(["batch", "run", str(batch)]) == 3
    assert "not ready" in capsys.readouterr().err
    assert _trays(batch) == ()


def test_real_scanner_ready_runs_one_tray(batch, synthetic_sweep_dir, monkeypatch):
    monkeypatch.setattr(run_cli, "real_scanner", lambda: FakeScanner(synthetic_sweep_dir))
    assert main(["batch", "run", str(batch), "--no-auto-export"]) == 0
    assert len(_trays(batch)) == 1


def test_interrupted_marker_is_refused(batch, synthetic_sweep_dir, capsys):
    interrupted.begin("scan")
    assert _run(batch, synthetic_sweep_dir) == 3
    assert "cut off" in capsys.readouterr().err
    assert _trays(batch) == ()


def test_old_manifest_without_export_keys_runs(batch, synthetic_sweep_dir, tmp_path):
    keys = json.loads((batch / "batch.json").read_text())["defaults"]
    assert not {"export_dir", "auto_export", "keep_raw", "jpeg_size"} & set(keys)
    out = tmp_path / "album"
    assert _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export") == 0
    assert len(list(out.glob("*.jpg"))) == 4


def test_ctrl_c_mid_scan_prints_the_confirm_text(batch, synthetic_sweep_dir, monkeypatch, capsys):
    def interrupt(self, timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr("opticfilm_linux.batch.runner.BatchRunner.wait_scans_idle", interrupt)
    assert _run(batch, synthetic_sweep_dir) == 130
    assert interrupted.CONFIRM_COMMAND in capsys.readouterr().err


def test_failed_scan_exits_2_and_exports_nothing(
    batch, synthetic_sweep_dir, tmp_path, monkeypatch, capsys
):
    def fail():
        raise ScanError("lamp failed")

    scanner = FakeScanner(synthetic_sweep_dir, before_scan=fail)
    monkeypatch.setattr(run_cli, "FakeScanner", lambda _sweep: scanner)
    out = tmp_path / "album"
    assert _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export") == 2
    assert "failed" in capsys.readouterr().err
    assert not out.exists() or not list(out.glob("*.jpg"))


def test_export_verification_failure_keeps_raw_and_exits_2(
    batch, synthetic_sweep_dir, tmp_path, monkeypatch, capsys
):
    def broken(*_args, **_kwargs):
        raise exports.ExportVerificationFailed(1, 2, ValueError("exported file is damaged"))

    monkeypatch.setattr("opticfilm_linux.app.auto.verify_tray_export", broken)
    out = tmp_path / "album"
    assert _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export") == 2
    (tray,) = _trays(batch)
    assert tray.auto == "failed" and not tray.raw_removed
    assert "damaged" in capsys.readouterr().err


def test_batch_rules_check_export_directory(tmp_path):
    with pytest.raises(BatchError):
        batch_rules.check_export_directory(tmp_path / "b", tmp_path / "b" / "x")
    batch_rules.check_export_directory(tmp_path / "b", tmp_path / "ok")


def test_run_cli_functions_stay_under_fifty_lines_and_file_under_800():
    path = Path(run_cli.__file__)
    source = path.read_text()
    assert len(source.splitlines()) < 800
    assert max(len(line) for line in source.splitlines()) <= 100
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            assert node.end_lineno - node.lineno + 1 < 50, node.name
