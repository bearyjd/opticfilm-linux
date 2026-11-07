"""``ofscan batch run`` and ``free-held``: headless scan -> accept -> export -> free (fake only)."""

import ast
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from opticfilm_linux.app import batch_rules
from opticfilm_linux.batch import export as exports
from opticfilm_linux.batch import run_cli
from opticfilm_linux.batch.model import BatchDefaults, SlideStatus, TrayStage
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.cli import main
from opticfilm_linux.scanner import interrupted
from opticfilm_linux.scanner.base import ScanError, ScannerStatus
from opticfilm_linux.scanner.fake import FakeScanner
from opticfilm_linux.scanner.holder_latch import HolderLatch


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
    assert main(["batch", "free-held", str(batch), "1"]) == 3
    assert not _trays(batch)[0].raw_removed
    assert main(["batch", "free-held", str(batch), "1", "--yes"]) == 0
    assert _trays(batch)[0].raw_removed
    assert len(list(out.glob("*.jpg"))) == 4


def test_free_held_refuses_a_tray_that_is_not_held(batch, synthetic_sweep_dir, capsys):
    _run(batch, synthetic_sweep_dir, "--no-auto-export")
    assert main(["batch", "free-held", str(batch), "1", "--yes"]) == 3
    assert "not held" in capsys.readouterr().err
    assert main(["batch", "free-held", str(batch), "9", "--yes"]) == 3
    assert "does not exist" in capsys.readouterr().err


def test_two_fake_trays_free_raw_after_release(batch, synthetic_sweep_dir, tmp_path, capsys):
    out = tmp_path / "album"
    assert _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export", "--count", "2") == 0
    first, second = _trays(batch)
    assert first.auto == "held" and not first.raw_removed
    assert not second.raw_removed
    capsys.readouterr()
    assert main(["batch", "free-held", str(batch), "1", "--yes"]) == 0
    first, second = _trays(batch)
    assert first.raw_removed and second.raw_removed
    assert len(list(out.glob("*.jpg"))) == 8
    lines = capsys.readouterr().out.splitlines()
    assert any(ln.startswith("tray 1") and "raw freed" in ln for ln in lines)
    assert any(ln.startswith("tray 2") and "raw freed" in ln for ln in lines)


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


def _manifest_hash(path):
    return hashlib.sha256((path / "batch.json").read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("status", "text"),
    [(ScannerStatus.NOT_READY, "not ready"), (ScannerStatus.BUSY, "busy")],
)
def test_real_scanner_not_usable_is_refused_without_touching_the_manifest(
    batch, synthetic_sweep_dir, tmp_path, monkeypatch, capsys, status, text
):
    scanner = FakeScanner(synthetic_sweep_dir, statuses=(status,))
    monkeypatch.setattr(run_cli, "real_scanner", lambda: scanner)
    before = _manifest_hash(batch)
    assert (
        main(
            [
                "batch",
                "run",
                str(batch),
                "--out",
                str(tmp_path / "album"),
                "--auto-export",
                "--keep-raw",
                "--jpeg-size",
                "3600",
            ]
        )
        == 3
    )
    assert text in capsys.readouterr().err
    assert _trays(batch) == () and _manifest_hash(batch) == before


def test_real_scanner_ready_runs_one_tray(batch, synthetic_sweep_dir, monkeypatch):
    monkeypatch.setattr(run_cli, "real_scanner", lambda: FakeScanner(synthetic_sweep_dir))
    assert main(["batch", "run", str(batch), "--no-auto-export"]) == 0
    assert len(_trays(batch)) == 1


def test_interrupted_marker_is_refused(batch, synthetic_sweep_dir, tmp_path, capsys):
    interrupted.begin("scan")
    before = _manifest_hash(batch)
    assert (
        _run(
            batch,
            synthetic_sweep_dir,
            "--out",
            str(tmp_path / "album"),
            "--auto-export",
            "--keep-raw",
            "--jpeg-size",
            "3600",
        )
        == 3
    )
    assert "cut off" in capsys.readouterr().err
    assert _trays(batch) == () and _manifest_hash(batch) == before


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


def _edit_manifest(path, change):
    with BatchStore.open(path) as store:
        store.update(change)


def _scan_only(path, sweep):
    assert main(["batch", "scan", str(path), "--fake", str(sweep)]) == 0


@pytest.mark.parametrize("reason", ["keep_raw", "auto_export"])
def test_free_held_names_the_setting_that_blocks_it(
    batch, synthetic_sweep_dir, tmp_path, capsys, reason
):
    _run(batch, synthetic_sweep_dir, "--out", str(tmp_path / "album"), "--auto-export")
    capsys.readouterr()
    _edit_manifest(
        batch,
        lambda m: dataclasses.replace(
            m, defaults=dataclasses.replace(m.defaults, **{reason: reason == "keep_raw"})
        ),
    )
    assert main(["batch", "free-held", str(batch), "1", "--yes"]) == 3
    assert reason in capsys.readouterr().err.replace("auto-export", "auto_export")
    assert not _trays(batch)[0].raw_removed


def test_free_held_refuses_a_held_tray_that_is_not_the_release_candidate(
    batch, synthetic_sweep_dir, tmp_path, capsys
):
    out = tmp_path / "album"
    _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export", "--count", "2")
    capsys.readouterr()
    _edit_manifest(
        batch,
        lambda m: dataclasses.replace(
            m, trays=(m.trays[0], dataclasses.replace(m.trays[1], auto="held"))
        ),
    )
    assert main(["batch", "free-held", str(batch), "2", "--yes"]) == 3
    assert "not the tray to release" in capsys.readouterr().err


def test_free_held_with_no_tray_waiting_does_not_say_none(
    batch, synthetic_sweep_dir, tmp_path, capsys
):
    out = tmp_path / "album"
    _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export", "--count", "2")

    def edit(manifest):
        first, second = manifest.trays
        return dataclasses.replace(
            manifest,
            trays=(
                dataclasses.replace(first, raw_removed=True),
                dataclasses.replace(second, auto="held", stage=TrayStage.REVIEW),
            ),
        )

    _edit_manifest(batch, edit)
    capsys.readouterr()
    assert main(["batch", "free-held", str(batch), "2", "--yes"]) == 3
    err = capsys.readouterr().err
    assert "None" not in err and "no tray is waiting to be released" in err


def _first_slide(change):
    def edit(manifest):
        tray = manifest.trays[0]
        slides = (change(tray.slides[0]), *tray.slides[1:])
        return dataclasses.replace(
            manifest, trays=(dataclasses.replace(tray, slides=slides), *manifest.trays[1:])
        )

    return edit


@pytest.mark.parametrize(
    ("change", "text"),
    [
        (lambda s: dataclasses.replace(s, status=SlideStatus.NEEDS_LOOK), "await your review"),
        (
            lambda s: dataclasses.replace(s, status=SlideStatus.NEEDS_LOOK, owner_decided=True),
            "excluded",
        ),
        (lambda s: dataclasses.replace(s, preview_stale=True), "previews"),
    ],
)
def test_free_held_names_review_and_preview_blockers(
    batch, synthetic_sweep_dir, tmp_path, capsys, change, text
):
    _run(batch, synthetic_sweep_dir, "--out", str(tmp_path / "album"), "--auto-export")
    _edit_manifest(batch, _first_slide(change))
    capsys.readouterr()
    assert main(["batch", "free-held", str(batch), "1", "--yes"]) == 3
    err = capsys.readouterr().err
    assert text in err and "did not pass" not in err
    assert not _trays(batch)[0].raw_removed


def test_free_held_names_unreviewed_flags(batch, synthetic_sweep_dir, tmp_path, capsys):
    _run(batch, synthetic_sweep_dir, "--out", str(tmp_path / "album"), "--auto-export")
    _edit_manifest(
        batch,
        lambda m: dataclasses.replace(
            m, trays=(dataclasses.replace(m.trays[0], auto_flagged=(1,)), *m.trays[1:])
        ),
    )
    capsys.readouterr()
    assert main(["batch", "free-held", str(batch), "1", "--yes"]) == 3
    assert "flags" in capsys.readouterr().err
    assert not _trays(batch)[0].raw_removed


def test_old_tray_is_exported_and_reported_by_run(batch, synthetic_sweep_dir, tmp_path, capsys):
    _scan_only(batch, synthetic_sweep_dir)  # tray 1 waits with auto == ""
    out = tmp_path / "album"
    assert _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export") == 0
    first, second = _trays(batch)
    assert first.auto == "held" and len(list(out.glob("*.jpg"))) == 8
    lines = capsys.readouterr().out.splitlines()
    assert any(ln.startswith("tray 1") and "auto=-" in ln and "held" in ln for ln in lines)
    assert any(ln.startswith("tray 2") for ln in lines)


def test_no_auto_export_flips_an_old_pending_tray_to_off_and_says_so(
    batch, synthetic_sweep_dir, capsys
):
    _scan_only(batch, synthetic_sweep_dir)
    with BatchStore.open(batch) as store:
        store.update_tray(1, auto="pending")
    assert _run(batch, synthetic_sweep_dir, "--no-auto-export") == 0
    assert _trays(batch)[0].auto == "off"
    assert any(
        ln.startswith("tray 1") and "pending->off" in ln
        for ln in capsys.readouterr().out.splitlines()
    )


def test_old_manual_tray_under_auto_export_is_warned_about(
    batch, synthetic_sweep_dir, tmp_path, capsys
):
    _run(batch, synthetic_sweep_dir, "--no-auto-export")
    capsys.readouterr()
    out = tmp_path / "album"
    assert _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export") == 0
    assert "tray 1 stays manual" in capsys.readouterr().err
    assert _trays(batch)[0].auto == "off"


def test_released_latch_is_refused(batch, synthetic_sweep_dir, tmp_path, monkeypatch, capsys):
    scanner = FakeScanner(synthetic_sweep_dir)
    scanner.usb_address = (1, 2)
    monkeypatch.setattr(run_cli, "real_scanner", lambda: scanner)
    monkeypatch.setattr(run_cli, "load_latch", lambda bus, address: HolderLatch.RELEASED)
    before = _manifest_hash(batch)
    assert (
        main(
            [
                "batch",
                "run",
                str(batch),
                "--out",
                str(tmp_path / "album"),
                "--auto-export",
                "--keep-raw",
                "--jpeg-size",
                "3600",
            ]
        )
        == 3
    )
    assert "latch" in capsys.readouterr().err
    assert _trays(batch) == () and _manifest_hash(batch) == before


def test_export_settings_cannot_change_after_the_first_tray_is_released(
    batch, synthetic_sweep_dir, tmp_path, capsys
):
    _run(batch, synthetic_sweep_dir, "--out", str(tmp_path / "album"), "--auto-export")
    assert main(["batch", "free-held", str(batch), "1", "--yes"]) == 0
    capsys.readouterr()
    other = tmp_path / "other"
    assert _run(batch, synthetic_sweep_dir, "--out", str(other), "--auto-export") == 3
    assert "after releasing the first tray" in capsys.readouterr().err
    assert len(_trays(batch)) == 1 and not other.exists()


def test_ctrl_c_during_export_does_not_talk_about_the_scanner(
    batch, synthetic_sweep_dir, tmp_path, monkeypatch, capsys
):
    def interrupt(self, number):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_cli.AutoPipeline, "run", interrupt)
    out = tmp_path / "album"
    assert _run(batch, synthetic_sweep_dir, "--out", str(out), "--auto-export") == 130
    err = capsys.readouterr().err
    assert "no scan was in progress" in err and "power-cycle" not in err


def test_ctrl_c_before_scanning_exits_130_quietly(batch, synthetic_sweep_dir, monkeypatch, capsys):
    def interrupt(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_cli, "_open_scanner", interrupt)
    assert _run(batch, synthetic_sweep_dir) == 130
    assert "Traceback" not in capsys.readouterr().err


def test_unresolvable_out_path_is_a_refusal(batch, synthetic_sweep_dir, capsys):
    assert _run(batch, synthetic_sweep_dir, "--out", "~no_such_user_zz/album") == 3
    assert "cannot use --out" in capsys.readouterr().err
    assert _trays(batch) == ()


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
