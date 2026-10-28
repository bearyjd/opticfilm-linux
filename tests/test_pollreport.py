"""The offline poll report, against synthetic run folders (no real captures)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from opticfilm_linux import pollreport

ROOT = Path(__file__).resolve().parent.parent

CAL_WARN = (
    "WARNING of135i: poll wv=0x0001 wi=0x0002 timed out after 0.3s "
    "(last {last}, want {want}) -- continuing\n"
)
CAL_DEBUG = "DEBUG of135i: cr wv=0x0001 wi=0x0002 got {got} want {want} -- continuing\n"
EJECT_TIMEOUT = (
    "ERROR of135i: poll_status_word timed out after 30.0s: last {last}, want {want} "
    "(mask 0xffff) -- strict, stopping\n"
)
EJECT_FAILED = (
    "ERROR of135i: hardware session FAILED in operation {op}, phase cold_init:homing-1, "
    "after 47 writes (2 execute pulses): StrictPollTimeoutError: boom\n"
)


def write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text)
    return folder


def report(folder: Path) -> str:
    return pollreport.build_report([folder])


# ---- status decoding ----------------------------------------------------------------


@pytest.mark.parametrize("text", ["ec55", "0xec55", "0XEC55", "EC55", " ec55 "])
def test_decode_accepts_both_spellings(text):
    status = pollreport.decode_status(text)
    assert status is not None
    assert status.first == 0xEC
    assert status.second == 0x55


def _bits(text):
    status = pollreport.decode_status(text)
    assert status is not None
    return status.bits


def test_decode_bit_names():
    assert _bits("0xf055") == ("power_bit", "buffer_empty", "bit20", "bit10")
    assert _bits("0b55") == ("loader_sensor", "bit02", "motor_enabled")
    assert _bits("0455") == ("lamp_on",)
    assert _bits("0055") == ()


@pytest.mark.parametrize(
    "bad", ["", "xyz", "ec5", "ec5555", "0x", "ec56", "ec 55", None, 123, b"ec55", ["ec55"]]
)
def test_decode_malformed_is_none_not_an_exception(bad):
    assert pollreport.decode_status(bad) is None


# ---- calibration classification -----------------------------------------------------


@pytest.mark.parametrize(
    "last,want,expected",
    [
        ("ec55", "dc55", "benign-baseline"),  # want: buffer_empty set, motor clear
        ("dd55", "dc55", "benign-baseline"),
        ("e955", "ad55", "other"),  # last still has motor set
        ("cc55", "ad55", "acquisition-not-observed"),
        ("ad55", "ad55", "other"),  # last is not idle/empty
        ("ec55", "ad55", "acquisition-not-observed"),
        ("ad55", "cc55", "benign-baseline"),
        ("c155", "a155", "other"),  # last has motor set
        ("8055", "2155", "other"),  # last lacks buffer_empty
        ("ec55", "4155", "other"),  # want has motor AND buffer_empty: neither pattern
        ("0055", "0055", "other"),  # neither
        ("zz55", "dc55", "malformed"),
        ("ec55", "", "malformed"),
        (None, "dc55", "malformed"),
    ],
)
def test_classify_calibration(last, want, expected):
    assert pollreport.classify_calibration(last, want) == expected


def test_classify_benign_beats_acquisition_when_want_is_idle():
    # want has motor clear and buffer_empty set -> baseline, whatever last says
    assert pollreport.classify_calibration("0155", "4055") == "benign-baseline"


# ---- scan.log -----------------------------------------------------------------------


def test_log_timeouts_and_readback_lines(tmp_path):
    text = (
        "INFO of135i: opened\n"
        + CAL_WARN.format(last="ec55", want="dc55")
        + CAL_WARN.format(last="ec55", want="ad55")
        + CAL_DEBUG.format(got="dd55", want="dc55")
    )
    out = report(write(tmp_path / "run", "scan.log", text))
    assert "2 timeout(s)" in out
    assert "benign-baseline" in out
    assert "acquisition-not-observed" in out
    assert "1 read-back mismatch" in out
    assert "VERDICT run:" in out


def test_log_malformed_status_reported_not_raised(tmp_path):
    text = CAL_WARN.format(last="zz", want="dc55") + CAL_WARN.format(last="ec55", want="0x")
    out = report(write(tmp_path / "run", "scan.log", text))
    assert out.count("malformed") >= 2
    assert "Traceback" not in out


def test_binary_and_garbage_logs_do_not_crash(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "scan.log").write_bytes(bytes(range(256)) * 50 + b"\xff\xfe poll timed out\n")
    (folder / "eject.log").write_bytes(b"\x00\x01 poll_status_word timed out after: last\n")
    out = report(folder)
    assert "VERDICT run:" in out


def test_empty_folder_says_nothing_found(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    out = report(folder)
    assert "no calibration poll timeouts found" in out
    assert "no cold-start move timeouts found" in out


def test_header_states_it_is_a_lead_not_proof(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    out = report(folder).lower()
    assert "lead from logs" in out
    assert "not proof" in out


# ---- diag.json ----------------------------------------------------------------------


def make_diag(folder: Path, name: str, data) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(data if isinstance(data, str) else json.dumps(data))


def test_diag_details_are_classified(tmp_path):
    folder = tmp_path / "run"
    make_diag(
        folder,
        "tray-f1.diag.json",
        {
            "poll_timeouts": 1,
            "poll_timeout_details": [{"last": "ec55", "want": "dc55", "wi": "0x2", "wv": "0x1"}],
            "poll_settle": [
                {"got": "dc55", "how": "match", "s": 0.1, "want": "dc55", "wi": 2, "wv": 1},
                {"got": "ec55", "how": "timeout", "s": 0.3, "want": "dc55", "wi": 2, "wv": 1},
                {"got": "dc55", "how": "match", "s": 0.1, "want": "dc55", "bits": {"a": 1}},
            ],
        },
    )
    out = report(folder)
    assert "tray-f1.diag.json" in out
    assert "benign-baseline" in out
    assert "match 2" in out
    assert "timeout 1" in out
    assert "1 with bits detail" in out


def test_diag_garbage_shapes_do_not_crash(tmp_path):
    folder = tmp_path / "run"
    make_diag(folder, "tray-f1.diag.json", "{not json")
    make_diag(folder, "tray-f2.diag.json", "[1, 2]")
    make_diag(folder, "tray-f3.diag.json", {"poll_timeouts": "x", "poll_settle": 7})
    make_diag(
        folder,
        "tray-f4.diag.json",
        {
            "poll_timeout_details": [1, None, {"last": 5, "want": []}],
            "poll_settle": [1, {"how": 3}],
        },
    )
    make_diag(folder, "tray-f5.diag.json", "[" * 100000)
    out = report(folder)
    assert "VERDICT run:" in out
    assert "unreadable" in out


def test_diag_poll_timeouts_without_details(tmp_path):
    folder = tmp_path / "run"
    make_diag(folder, "tray-f1.diag.json", {"poll_timeouts": 2})
    out = report(folder)
    assert "poll_timeouts=2" in out


def test_diag_bool_poll_timeouts_not_counted(tmp_path):
    folder = tmp_path / "run"
    make_diag(folder, "tray-f1.diag.json", {"poll_timeouts": True})
    assert "poll_timeouts=True" not in report(folder)


# ---- eject.log (cold start) ---------------------------------------------------------


def test_cold_move_timeout_without_outcome(tmp_path):
    text = (
        "INFO of135i: cold_init: homing round 1/3\n"
        "INFO of135i: cold_init: resync status word 0xf055 (want 0xf855)\n"
        + EJECT_TIMEOUT.format(last="0xf055", want="0xf855")
        + EJECT_FAILED.format(op="cold_init")
    )
    out = report(write(tmp_path / "run", "eject.log", text))
    assert "1 timeout(s)" in out
    assert "last f055" in out
    assert "want f855" in out
    assert "power_bit" in out
    assert "47 writes" in out
    assert "2 execute pulses" in out
    assert "homing round 1/3" in out
    assert "cannot distinguish moved-and-stopped from never-started" in out


@pytest.mark.parametrize(
    "word", ["ended-off-sensor", "still-busy", "no-busy-observed", "malformed", "other"]
)
def test_cold_move_outcome_is_reported_when_present(tmp_path, word):
    text = EJECT_TIMEOUT.format(last="0xf055", want="0xf855") + f"INFO of135i: outcome {word}\n"
    out = report(write(tmp_path / "run", "eject.log", text))
    assert f"outcome: {word}" in out
    assert "cannot distinguish" not in out


def test_cold_move_unknown_outcome_word_is_ignored(tmp_path):
    text = EJECT_TIMEOUT.format(last="0xf055", want="0xf855") + "INFO of135i: outcome bogus\n"
    out = report(write(tmp_path / "run", "eject.log", text))
    assert "cannot distinguish" in out


def test_non_cold_init_failure_is_not_a_cold_failure(tmp_path):
    text = EJECT_FAILED.format(op="scan")
    out = report(write(tmp_path / "run", "eject.log", text))
    assert "no cold-start move timeouts found" in out


def test_cold_failure_without_poll_line_still_reported(tmp_path):
    out = report(write(tmp_path / "run", "eject.log", EJECT_FAILED.format(op="cold_init")))
    assert "cold_init:homing-1" in out
    assert "47 writes" in out


def test_cold_malformed_status_words(tmp_path):
    text = EJECT_TIMEOUT.format(last="0xzz", want="0xf855")
    out = report(write(tmp_path / "run", "eject.log", text))
    assert "malformed" in out


# ---- verdicts and CLI ---------------------------------------------------------------


def test_one_verdict_line_per_run(tmp_path):
    a = write(tmp_path / "a", "scan.log", CAL_WARN.format(last="ec55", want="dc55"))
    b = write(tmp_path / "b", "eject.log", EJECT_TIMEOUT.format(last="0xf055", want="0xf855"))
    out = pollreport.build_report([a, b])
    verdicts = [line for line in out.splitlines() if line.startswith("VERDICT ")]
    assert [v.split(":")[0] for v in verdicts] == ["VERDICT a", "VERDICT b"]
    assert "1 benign-baseline" in verdicts[0]
    assert "cold-start move: 1 timeout(s)" in verdicts[1]
    assert "no cold-start move timeouts found" in verdicts[0]


def test_no_register_addresses_in_output(tmp_path):
    text = CAL_WARN.format(last="ec55", want="dc55") + CAL_DEBUG.format(got="dd55", want="dc55")
    out = report(write(tmp_path / "run", "scan.log", text))
    assert "wv=" not in out
    assert "wi=" not in out
    assert "0x0001" not in out


def test_main_exit_codes(tmp_path, capsys):
    ok = tmp_path / "ok"
    ok.mkdir()
    assert pollreport.main([str(ok)]) == 0
    assert pollreport.main([str(tmp_path / "missing")]) == 2
    assert "error:" in capsys.readouterr().err
    afile = tmp_path / "file.txt"
    afile.write_text("x")
    assert pollreport.main([str(afile)]) == 2


def test_main_unreadable_folder_does_not_hide_the_others(tmp_path, capsys):
    ok = write(tmp_path / "ok", "scan.log", CAL_WARN.format(last="ec55", want="dc55"))
    code = pollreport.main([str(tmp_path / "missing"), str(ok)])
    assert code == 2
    assert "VERDICT ok:" in capsys.readouterr().out


def test_main_timeouts_never_change_exit_code(tmp_path):
    text = CAL_WARN.format(last="e955", want="ad55") + EJECT_TIMEOUT.format(last="1", want="2")
    assert pollreport.main([str(write(tmp_path / "r", "scan.log", text))]) == 0


def test_verdict_reports_log_and_diagnostic_sources_separately(tmp_path):
    folder = write(tmp_path / "run", "scan.log", CAL_WARN.format(last="cc55", want="ad55"))
    make_diag(
        folder,
        "tray-f1.diag.json",
        {"poll_timeout_details": [{"last": "ec55", "want": "dc55"}]},
    )
    verdict = next(x for x in report(folder).splitlines() if x.startswith("VERDICT"))
    assert "logs 1 (1 acquisition-not-observed)" in verdict
    assert "diagnostics 1 (1 benign-baseline)" in verdict


def test_readback_line_with_colon_after_register_is_a_mismatch(tmp_path):
    text = "DEBUG of135i: cr wv=0x0001 wi=0x0002: got c555 want cc55 -- continuing\n"
    out = report(write(tmp_path / "run", "scan.log", text))
    assert "1 read-back mismatch" in out
    assert "no calibration poll timeouts found" in out


@pytest.mark.parametrize(
    "writes,pulses", [("9" * 5000, "2"), ("47", "9" * 5000), ("9" * 5000,) * 2]
)
def test_huge_digit_counts_in_failure_line_do_not_crash(tmp_path, writes, pulses):
    digits = "9" * 5000
    text = (
        "ERROR of135i: hardware session FAILED in operation cold_init, phase cold_init:homing-1, "
        f"after {writes} writes ({pulses} execute pulses): boom\n"
    )
    out = report(write(tmp_path / "run", "eject.log", text))
    assert "VERDICT run:" in out
    assert digits not in out


def test_settle_how_is_allowlisted_and_cannot_forge_lines(tmp_path):
    folder = tmp_path / "run"
    make_diag(
        folder,
        "tray-f1.diag.json",
        {
            "poll_settle": [
                {"how": "match"},
                {"how": "x\nVERDICT forged: all fine"},
                {"how": "wv=0x1234"},
                {"how": "A" * 500},
            ]
        },
    )
    out = report(folder)
    assert "unrecognised 3" in out
    assert "match 1" in out
    assert "forged" not in out
    assert "0x1234" not in out
    assert sum(line.startswith("VERDICT") for line in out.splitlines()) == 1


def test_aggregate_poll_timeouts_without_details_are_not_reported_as_none(tmp_path):
    folder = tmp_path / "run"
    make_diag(folder, "tray-f1.diag.json", {"poll_timeouts": 2})
    verdict = next(x for x in report(folder).splitlines() if x.startswith("VERDICT"))
    assert "diagnostics count 2 without detail (cannot classify)" in verdict
    assert "no calibration poll timeouts found" not in verdict


def test_aggregate_count_with_details_is_not_double_counted(tmp_path):
    folder = tmp_path / "run"
    make_diag(
        folder,
        "tray-f1.diag.json",
        {"poll_timeouts": 1, "poll_timeout_details": [{"last": "ec55", "want": "dc55"}]},
    )
    verdict = next(x for x in report(folder).splitlines() if x.startswith("VERDICT"))
    assert "without detail" not in verdict


def test_zero_or_negative_aggregate_is_none_found(tmp_path):
    folder = tmp_path / "run"
    make_diag(folder, "tray-f1.diag.json", {"poll_timeouts": 0})
    make_diag(folder, "tray-f2.diag.json", {"poll_timeouts": -3})
    verdict = next(x for x in report(folder).splitlines() if x.startswith("VERDICT"))
    assert "no calibration poll timeouts found" in verdict


def test_script_runs_as_a_program(tmp_path):
    folder = write(tmp_path / "run", "scan.log", CAL_WARN.format(last="ec55", want="dc55"))
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "poll_report.py"), str(folder)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "VERDICT run:" in proc.stdout
