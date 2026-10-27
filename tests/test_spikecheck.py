"""The offline hardware-run checker, against synthetic run folders (no real captures)."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

from opticfilm_linux import spikecheck

ROOT = Path(__file__).resolve().parent.parent
POSITION_LINE = "INFO of135i: completion poll settled f555 (captured f455) after {wait:.2f}s\n"


def make_run(
    folder: Path,
    *,
    wait: float | None = 10.44,
    poll_timeouts: int = 1,
    park_timed_out: bool = False,
    park_last: str = "f855",
    log_extra: str = "",
    frames: int = 1,
    exit_code: str | None = "0",
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    log = "INFO of135i: opened device\n"
    if wait is not None:
        log += POSITION_LINE.format(wait=wait)
    (folder / "scan.log").write_text(log + log_extra)
    for n in range(1, frames + 1):
        for name in (f"tray-f{n}.overscan.tiff", f"tray-f{n}-ir.overscan.tiff"):
            tifffile.imwrite(folder / name, np.zeros((6, 5, 3), np.uint16))
        (folder / f"tray-f{n}.diag.json").write_text(
            json.dumps(
                {
                    "poll_timeouts": poll_timeouts,
                    "park_waits": {
                        "a_timed_out": False,
                        "b_timed_out": park_timed_out,
                        "b_last": park_last,
                    },
                }
            )
        )
    if exit_code is not None:
        (folder / "exit_code").write_text(exit_code + "\n")
    return folder


def status(results, name: str) -> str:
    return next(r.status for r in results if r.name == name)


def test_good_run_passes_everything(tmp_path):
    results = spikecheck.check_folder(make_run(tmp_path / "ok"))
    assert {r.status for r in results} == {"PASS"}
    assert spikecheck.all_passed(results)


@pytest.mark.parametrize("code,expected", [("0", "PASS"), ("130", "FAIL"), ("oops", "FAIL")])
def test_exit_code(tmp_path, code, expected):
    results = spikecheck.check_folder(make_run(tmp_path / "r", exit_code=code))
    assert status(results, "exit-code") == expected


def test_exit_code_not_recorded_is_skipped_not_failed(tmp_path):
    results = spikecheck.check_folder(make_run(tmp_path / "r", exit_code=None))
    assert status(results, "exit-code") == "SKIP"
    assert spikecheck.all_passed(results)


def test_exit_code_from_argument_and_result_md(tmp_path):
    run = make_run(tmp_path / "r", exit_code=None)
    assert status(spikecheck.check_folder(run, exit_code=2), "exit-code") == "FAIL"
    (run / "RESULT.md").write_text("- process exited 0\n")
    assert status(spikecheck.check_folder(run), "exit-code") == "PASS"
    (run / "RESULT.md").write_text("- Parent Ctrl-C exit130\n")
    assert status(spikecheck.check_folder(run), "exit-code") == "FAIL"


def test_missing_ir_tiff_fails(tmp_path):
    run = make_run(tmp_path / "r")
    (run / "tray-f1-ir.overscan.tiff").unlink()
    assert status(spikecheck.check_folder(run), "tiffs") == "FAIL"


def test_no_tiffs_fails(tmp_path):
    run = make_run(tmp_path / "r")
    for f in run.glob("*.tiff"):
        f.unlink()
    assert status(spikecheck.check_folder(run), "tiffs") == "FAIL"


def test_eight_bit_tiff_fails(tmp_path):
    run = make_run(tmp_path / "r")
    tifffile.imwrite(run / "tray-f1.overscan.tiff", np.zeros((6, 5, 3), np.uint8))
    assert status(spikecheck.check_folder(run), "tiffs") == "FAIL"


def test_mismatched_rgb_ir_dimensions_fail(tmp_path):
    run = make_run(tmp_path / "r")
    tifffile.imwrite(run / "tray-f1-ir.overscan.tiff", np.zeros((7, 5, 3), np.uint16))
    assert status(spikecheck.check_folder(run), "tiffs") == "FAIL"


@pytest.mark.filterwarnings("ignore:.*zero-size array")
def test_empty_dimension_tiff_fails(tmp_path):
    run = make_run(tmp_path / "r")
    for name in ("tray-f1.overscan.tiff", "tray-f1-ir.overscan.tiff"):
        tifffile.imwrite(run / name, np.zeros((0, 5, 3), np.uint16))
    assert status(spikecheck.check_folder(run), "tiffs") == "FAIL"


def test_garbage_tiff_fails_without_crashing(tmp_path):
    run = make_run(tmp_path / "r")
    (run / "tray-f1.overscan.tiff").write_bytes(b"not a tiff at all")
    assert status(spikecheck.check_folder(run), "tiffs") == "FAIL"


@pytest.mark.parametrize(
    "wait,expected",
    [(10.44, "PASS"), (8.95, "PASS"), (11.9, "PASS"), (8.9, "FAIL"), (42.9, "FAIL")],
)
def test_position_window(tmp_path, wait, expected):
    results = spikecheck.check_folder(make_run(tmp_path / "r", wait=wait))
    assert status(results, "position-wait") == expected


def test_position_window_is_configurable(tmp_path):
    run = make_run(tmp_path / "r", wait=3.31)
    assert status(spikecheck.check_folder(run), "position-wait") == "FAIL"
    results = spikecheck.check_folder(run, position_target=3.31, position_tolerance=0.1)
    assert status(results, "position-wait") == "PASS"


def test_position_line_missing_fails(tmp_path):
    results = spikecheck.check_folder(make_run(tmp_path / "r", wait=None))
    assert status(results, "position-wait") == "FAIL"


def test_multi_frame_uses_the_longest_wait(tmp_path):
    run = make_run(tmp_path / "r", wait=0.0)
    with (run / "scan.log").open("a") as log:
        log.write(POSITION_LINE.format(wait=10.44))
        log.write(POSITION_LINE.format(wait=3.31))
    assert status(spikecheck.check_folder(run), "position-wait") == "PASS"


def test_park_timeout_fails(tmp_path):
    results = spikecheck.check_folder(make_run(tmp_path / "r", park_timed_out=True))
    assert status(results, "park") == "FAIL"


def test_park_wrong_final_status_fails(tmp_path):
    results = spikecheck.check_folder(make_run(tmp_path / "r", park_last="f455"))
    assert status(results, "park") == "FAIL"


def test_ejected_claim_is_not_park_proof(tmp_path):
    run = make_run(tmp_path / "r", log_extra="INFO of135i: holder ejected\n")
    for diag in run.glob("*.diag.json"):
        diag.unlink()
    (run / "RESULT.md").write_text("Scanner ejected the holder. Parked fine.\n")
    results = spikecheck.check_folder(run)
    assert status(results, "park") == "FAIL"
    assert status(results, "eject-claim") == "NOTE"
    assert "physically" in next(r.detail for r in results if r.name == "eject-claim")


def test_no_eject_text_has_no_note(tmp_path):
    results = spikecheck.check_folder(make_run(tmp_path / "r"))
    assert all(r.name != "eject-claim" for r in results)


def test_ejected_note_does_not_change_verdict(tmp_path):
    run = make_run(tmp_path / "r", log_extra="holder ejected\n")
    results = spikecheck.check_folder(run)
    assert status(results, "eject-claim") == "NOTE"
    assert spikecheck.all_passed(results)


@pytest.mark.parametrize("count,limit,expected", [(1, 1, "PASS"), (2, 1, "FAIL"), (2, 2, "PASS")])
def test_poll_timeout_limit(tmp_path, count, limit, expected):
    run = make_run(tmp_path / "r", poll_timeouts=count)
    results = spikecheck.check_folder(run, max_poll_timeouts=limit)
    assert status(results, "poll-timeouts") == expected


def test_poll_timeouts_checked_per_frame(tmp_path):
    run = make_run(tmp_path / "r", frames=2)
    diag = json.loads((run / "tray-f2.diag.json").read_text())
    diag["poll_timeouts"] = 3
    (run / "tray-f2.diag.json").write_text(json.dumps(diag))
    assert status(spikecheck.check_folder(run), "poll-timeouts") == "FAIL"


@pytest.mark.parametrize("content", ["{not json", "[]", '{"poll_timeouts": "many"}', ""])
def test_garbage_diag_fails_without_crashing(tmp_path, content):
    run = make_run(tmp_path / "r")
    (run / "tray-f1.diag.json").write_text(content)
    results = spikecheck.check_folder(run)
    assert status(results, "poll-timeouts") == "FAIL"
    assert not spikecheck.all_passed(results)


def test_missing_diag_fails_poll_timeouts(tmp_path):
    run = make_run(tmp_path / "r")
    (run / "tray-f1.diag.json").unlink()
    assert status(spikecheck.check_folder(run), "poll-timeouts") == "FAIL"


def test_calibration_warning_fails(tmp_path):
    line = "WARNING of135i: poll wv=0x018e wi=0x0122 timed out after 0.3s (last cc55, want ad55)\n"
    results = spikecheck.check_folder(make_run(tmp_path / "r", log_extra=line))
    assert status(results, "calibration") == "FAIL"


def test_calibration_warning_from_diag_details_fails(tmp_path):
    run = make_run(tmp_path / "r")
    diag = json.loads((run / "tray-f1.diag.json").read_text())
    diag["poll_timeout_details"] = [{"last": "cc55", "want": "ad55"}]
    (run / "tray-f1.diag.json").write_text(json.dumps(diag))
    assert status(spikecheck.check_folder(run), "calibration") == "FAIL"


def test_other_poll_warning_and_debug_cd55_are_not_calibration_failures(tmp_path):
    extra = (
        "WARNING of135i: poll wv=0x018e wi=0x0122 timed out after 0.3s (last ec55, want dc55)\n"
        "DEBUG of135i: cr wv=0x018e wi=0x0122: got cd55 want ad55 -- continuing\n"
    )
    results = spikecheck.check_folder(make_run(tmp_path / "r", log_extra=extra))
    assert status(results, "calibration") == "PASS"


def test_missing_log_fails_log_criteria_without_crashing(tmp_path):
    run = make_run(tmp_path / "r")
    (run / "scan.log").unlink()
    results = spikecheck.check_folder(run)
    assert status(results, "position-wait") == "FAIL"
    assert status(results, "calibration") == "FAIL"


def test_binary_garbage_log_does_not_crash(tmp_path):
    run = make_run(tmp_path / "r")
    (run / "scan.log").write_bytes(b"\xff\xfe\x00\x80" * 50)
    assert status(spikecheck.check_folder(run), "position-wait") == "FAIL"


def test_missing_and_non_directory_folder_fail(tmp_path):
    results = spikecheck.check_folder(tmp_path / "nope")
    assert [r.status for r in results] == ["FAIL"]
    (tmp_path / "file").write_text("x")
    assert not spikecheck.all_passed(spikecheck.check_folder(tmp_path / "file"))


def test_empty_folder_fails_every_evidence_criterion(tmp_path):
    (tmp_path / "e").mkdir()
    results = spikecheck.check_folder(tmp_path / "e")
    for name in ("tiffs", "position-wait", "park", "poll-timeouts", "calibration"):
        assert status(results, name) == "FAIL"


def test_report_lists_each_criterion(tmp_path):
    text = spikecheck.format_report(spikecheck.check_folder(make_run(tmp_path / "r")))
    assert "position-wait" in text and text.rstrip().endswith("VERDICT: PASS")
    bad = spikecheck.format_report(spikecheck.check_folder(tmp_path / "nope"))
    assert bad.rstrip().endswith("VERDICT: FAIL")


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_spike.py"), *map(str, args)],
        capture_output=True,
        text=True,
    )


def test_cli_exit_codes(tmp_path):
    good = run_cli(make_run(tmp_path / "ok"))
    assert good.returncode == 0 and "VERDICT: PASS" in good.stdout
    bad = run_cli(make_run(tmp_path / "bad", wait=42.9))
    assert bad.returncode == 1 and "VERDICT: FAIL" in bad.stdout


def test_cli_options(tmp_path):
    run = make_run(tmp_path / "r", wait=3.31, poll_timeouts=2)
    assert run_cli(run).returncode == 1
    done = run_cli(
        run, "--position-target", 3.31, "--position-tolerance", 0.2, "--max-poll-timeouts", 2
    )
    assert done.returncode == 0, done.stdout
    assert run_cli(run, "--exit-code", 1).returncode == 1


def test_cli_missing_folder(tmp_path):
    result = run_cli(tmp_path / "nope")
    assert result.returncode == 1 and "FAIL" in result.stdout


def test_frame_without_diag_fails_park_and_poll_timeouts(tmp_path):
    run = make_run(tmp_path / "r", frames=2)
    (run / "tray-f2.diag.json").unlink()
    results = spikecheck.check_folder(run)
    assert status(results, "park") == "FAIL"
    assert status(results, "poll-timeouts") == "FAIL"


def test_require_exit_code_fails_when_unrecorded(tmp_path):
    run = make_run(tmp_path / "r", exit_code=None)
    results = spikecheck.check_folder(run, require_exit_code=True)
    assert status(results, "exit-code") == "FAIL"
    assert run_cli(run, "--require-exit-code").returncode == 1
    assert run_cli(run).returncode == 0


def test_last_exit_mention_in_result_md_wins(tmp_path):
    run = make_run(tmp_path / "r", exit_code=None)
    (run / "RESULT.md").write_text("dry run exited 0\nreal run exited 130\n")
    assert status(spikecheck.check_folder(run), "exit-code") == "FAIL"


@pytest.mark.parametrize("details", [5, True, [5], [{"last": ["x"], "want": {"a": 1}}], "cc55"])
def test_malformed_poll_timeout_details_do_not_crash(tmp_path, details):
    run = make_run(tmp_path / "r")
    diag = json.loads((run / "tray-f1.diag.json").read_text())
    diag["poll_timeout_details"] = details
    (run / "tray-f1.diag.json").write_text(json.dumps(diag))
    assert status(spikecheck.check_folder(run), "calibration") == "PASS"


def test_negative_poll_timeouts_fail(tmp_path):
    run = make_run(tmp_path / "r", poll_timeouts=-1)
    assert status(spikecheck.check_folder(run), "poll-timeouts") == "FAIL"


def test_deeply_nested_diag_does_not_crash(tmp_path):
    run = make_run(tmp_path / "r")
    (run / "tray-f1.diag.json").write_text("[" * 100000)
    assert status(spikecheck.check_folder(run), "poll-timeouts") == "FAIL"


def test_rgb_name_containing_ir_is_still_rgb(tmp_path):
    run = make_run(tmp_path / "r")
    for name in ("tray-f1.overscan.tiff", "tray-f1-ir.overscan.tiff"):
        (run / name).rename(run / name.replace("tray", "tray-irregular"))
    (run / "tray-f1.diag.json").rename(run / "tray-irregular-f1.diag.json")
    assert status(spikecheck.check_folder(run), "tiffs") == "PASS"
    (run / "tray-irregular-f1-ir.overscan.tiff").unlink()
    assert status(spikecheck.check_folder(run), "tiffs") == "FAIL"


def test_unreadable_folder_fails_without_crashing(tmp_path, monkeypatch):
    run = make_run(tmp_path / "r")
    monkeypatch.setattr(
        Path, "glob", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "no"))
    )
    assert [r.status for r in spikecheck.check_folder(run)] == ["FAIL"]


def test_non_ascii_log_text_does_not_break_cli_output(tmp_path):
    line = "WARNING of135i: timed out (last cc55, want ad55) caf\u00e9\n"
    run = make_run(tmp_path / "r", log_extra=line)
    assert run_cli(run).returncode == 1


def test_truncated_tiff_fails(tmp_path):
    run = make_run(tmp_path / "r")
    path = run / "tray-f1.overscan.tiff"
    with tifffile.TiffFile(path) as tif:
        cut = tif.pages[0].dataoffsets[0]
    path.write_bytes(path.read_bytes()[:cut])
    assert status(spikecheck.check_folder(run), "tiffs") == "FAIL"


def test_diag_without_frame_images_fails(tmp_path):
    run = make_run(tmp_path / "r", frames=2)
    for name in ("tray-f2.overscan.tiff", "tray-f2-ir.overscan.tiff"):
        (run / name).unlink()
    assert status(spikecheck.check_folder(run), "tiffs") == "FAIL"


@pytest.mark.parametrize(
    "text", ["process exited -11", "process exited 0x82", "exit code: 1", "process exited 0.5"]
)
def test_odd_exit_values_in_result_md_fail(tmp_path, text):
    run = make_run(tmp_path / "r", exit_code=None)
    (run / "RESULT.md").write_text(text + "\n")
    assert status(spikecheck.check_folder(run), "exit-code") == "FAIL"


def test_grayscale_rgb_tiff_fails(tmp_path):
    run = make_run(tmp_path / "r")
    for name in ("tray-f1.overscan.tiff", "tray-f1-ir.overscan.tiff"):
        tifffile.imwrite(run / name, np.zeros((6, 5), np.uint16))
    assert status(spikecheck.check_folder(run), "tiffs") == "FAIL"


def test_last_signed_exit_mention_wins(tmp_path):
    run = make_run(tmp_path / "r", exit_code=None)
    (run / "RESULT.md").write_text("dry run exited 0\nreal run exited +130\n")
    assert status(spikecheck.check_folder(run), "exit-code") == "FAIL"


def test_orphan_ir_frame_fails_tiffs_and_diag_checks(tmp_path):
    run = make_run(tmp_path / "r")
    tifffile.imwrite(run / "tray-f2-ir.overscan.tiff", np.zeros((6, 5, 3), np.uint16))
    results = spikecheck.check_folder(run)
    assert status(results, "tiffs") == "FAIL"
    assert status(results, "park") == "FAIL"
    assert status(results, "poll-timeouts") == "FAIL"
