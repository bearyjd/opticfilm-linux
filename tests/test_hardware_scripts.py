"""Tests for scripts/hardware/*.sh using a fake driver package (no hardware, network or gh)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

BASH = shutil.which("bash") or "bash"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")

REPO = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO / "scripts" / "hardware"
HW_RUN = SCRIPT_DIR / "hw_run.sh"
ALL_SCRIPTS = ["hw_run.sh", "ab_gate_test.sh", "tray_and_armc_test.sh", "merge_when_green.sh"]

FAKE_MAIN = """\
import os, sys
print("trace=" + os.environ.get("OF135I_TRACE", ""))
print("dumpcal=" + os.environ.get("OF135I_DUMP_CAL", ""))
print("args=" + " ".join(sys.argv[1:]))
calls = os.environ.get("FAKE_CALLS")
if calls:
    with open(calls, "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")
    with open(calls) as f:
        if sum(1 for _ in f) == int(os.environ.get("FAKE_FAIL_AT", "0")):
            sys.exit(1)
print("pythonpath=" + os.environ.get("PYTHONPATH", ""))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


@pytest.fixture
def driver(tmp_path: Path) -> Path:
    pkg = tmp_path / "driver" / "of135i"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text(FAKE_MAIN)
    return tmp_path / "driver"


def run_hw(driver_dir, attempt, name, *args, exit_code=0, cwd=None, **env):
    e = {k: v for k, v in os.environ.items() if k not in ("HW_RUN_NO_DOCTOR", "DOCTOR_CMD")}
    e.pop("OPTICFILM_DRIVER_DIR", None)
    e["FAKE_EXIT"] = str(exit_code)
    e["HW_RUN_LOG_DIR"] = str(Path(attempt).parent / "logs")
    if driver_dir is not None:
        e["OPTICFILM_DRIVER_DIR"] = str(driver_dir)
    e.update(env)
    return subprocess.run(
        [BASH, str(HW_RUN), str(attempt), name, *args],
        capture_output=True,
        text=True,
        env=e,
        cwd=cwd,
        timeout=60,
    )


def log_text(attempt: Path, name: str) -> str:
    return (attempt.parent / "logs" / f"{attempt.name}-{name}.log").read_text()


def test_success_passes_exit_status_and_sets_trace(driver, tmp_path):
    attempt = tmp_path / "att-ok"
    r = run_hw(driver, attempt, "t1", "-v", "scan")
    assert r.returncode == 0, r.stderr
    assert f"trace={attempt}/trace-t1.jsonl" in r.stdout
    assert "dumpcal=1" in r.stdout
    assert "args=-v scan" in r.stdout
    assert "driver_exit=0" in log_text(attempt, "t1")


def test_failure_exit_status_is_nonzero(driver, tmp_path):
    r = run_hw(driver, tmp_path / "att-fail", "t2", "scan", exit_code=1, HW_RUN_NO_DOCTOR="1")
    assert r.returncode != 0
    assert "driver_exit=1" in r.stdout


def test_doctor_not_run_on_success(driver, tmp_path):
    r = run_hw(driver, tmp_path / "att-nodoc", "t3", "scan", DOCTOR_CMD="echo DOCTOR_RAN")
    assert r.returncode == 0
    assert "DOCTOR_RAN" not in r.stdout


def test_doctor_runs_on_failure_and_keeps_exit_status(driver, tmp_path):
    attempt = tmp_path / "att-doc"
    r = run_hw(driver, attempt, "t4", "scan", exit_code=1, DOCTOR_CMD="echo DOCTOR_RAN")
    assert r.returncode != 0
    assert "DOCTOR_RAN" in r.stdout
    assert "DOCTOR_RAN" in log_text(attempt, "t4")


def test_failing_doctor_does_not_change_status(driver, tmp_path):
    r = run_hw(driver, tmp_path / "att-docfail", "t5", "scan", exit_code=3, DOCTOR_CMD="false")
    assert r.returncode != 0
    assert "driver_exit=3" in r.stdout


def test_doctor_disabled_by_switch(driver, tmp_path):
    r = run_hw(
        driver,
        tmp_path / "att-off",
        "t6",
        "scan",
        exit_code=1,
        DOCTOR_CMD="echo DOCTOR_RAN",
        HW_RUN_NO_DOCTOR="1",
    )
    assert r.returncode != 0
    assert "DOCTOR_RAN" not in r.stdout


def test_default_doctor_uses_driver_doctor_command(driver, tmp_path):
    attempt = tmp_path / "att-def"
    r = run_hw(driver, attempt, "t7", "scan", exit_code=1)
    assert r.returncode != 0
    assert "args=doctor --json" in r.stdout
    assert f"{attempt}/doctor-after-t7.json" in r.stdout


def test_missing_driver_dir_is_a_clear_error(tmp_path):
    r = run_hw(None, tmp_path / "att-x", "t8", "scan")
    assert r.returncode != 0
    assert "OPTICFILM_DRIVER_DIR" in r.stderr


def test_driver_dir_without_package_is_a_clear_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = run_hw(empty, tmp_path / "att-y", "t9", "scan")
    assert r.returncode != 0
    assert "of135i" in r.stderr


def test_deps_overlay_is_on_pythonpath(driver, tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "fakedep.py").write_text("VALUE = 7\n")
    (driver / "of135i" / "__main__.py").write_text("import fakedep\nprint('dep', fakedep.VALUE)\n")
    r = run_hw(driver, tmp_path / "att-ov", "t10", "scan", OF135I_DEPS_OVERLAY=str(overlay))
    assert r.returncode == 0, r.stderr
    assert "dep 7" in r.stdout


@pytest.mark.parametrize("script", ALL_SCRIPTS)
def test_scripts_parse(script):
    r = subprocess.run([BASH, "-n", str(SCRIPT_DIR / script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
@pytest.mark.parametrize("script", ALL_SCRIPTS)
def test_scripts_shellcheck(script):
    r = subprocess.run(["shellcheck", str(SCRIPT_DIR / script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("script", ALL_SCRIPTS)
def test_scripts_have_no_personal_paths(script):
    text = (SCRIPT_DIR / script).read_text()
    assert "/home/" not in text
    assert ".local-research" not in text


def test_relative_driver_dir_is_resolved(driver, tmp_path):
    rel = os.path.relpath(driver, tmp_path)
    r = run_hw(rel, tmp_path / "att-rel", "t11", "scan", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_pythonpath_is_hermetic(driver, tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    r = run_hw(
        driver,
        tmp_path / "att-herm",
        "t12",
        "scan",
        OF135I_DEPS_OVERLAY=str(overlay),
        PYTHONPATH="/inherited",
    )
    assert f"pythonpath={driver}:{overlay}\n" in r.stdout


def test_python_with_spaces_keeps_default_doctor(driver, tmp_path):
    d = tmp_path / "a b"
    d.mkdir()
    wrapper = d / "py"
    wrapper.write_text('#!/bin/bash\nexec python3 "$@"\n')
    wrapper.chmod(0o755)
    r = run_hw(driver, tmp_path / "att-sp", "t13", "scan", exit_code=1, PYTHON=str(wrapper))
    assert r.returncode != 0
    assert "args=doctor --json" in r.stdout


# --- the supervised drivers: ab_gate_test.sh and tray_and_armc_test.sh -------------------------

COMMON = (
    "-v scan {slide} --dpi {dpi} --ir --no-clean --dark-fallback default --cal-wait-scanstop {stop}"
)


def run_script(script, args, driver, tmp_path, *, assume_yes=True, fail_at=0, stdin_text=None):
    attempt = tmp_path / "att"
    calls = tmp_path / "calls.txt"
    e = {k: v for k, v in os.environ.items() if k not in ("OPTICFILM_HW_ASSUME_YES",)}
    e.update(
        OPTICFILM_DRIVER_DIR=str(driver),
        HW_RUN_LOG_DIR=str(tmp_path / "logs"),
        HW_RUN_NO_DOCTOR="1",
        FAKE_CALLS=str(calls),
        FAKE_FAIL_AT=str(fail_at),
    )
    if assume_yes:
        e["OPTICFILM_HW_ASSUME_YES"] = "1"
    r = subprocess.run(
        [BASH, str(SCRIPT_DIR / script), *args(attempt)],
        stdin=subprocess.DEVNULL if stdin_text is None else None,
        input=stdin_text,
        capture_output=True,
        text=True,
        env=e,
        timeout=60,
    )
    lines = calls.read_text().splitlines() if calls.exists() else []
    return r, lines, attempt


def ab_expected(attempt):
    plan = [("warmup", "enforce"), ("1", "off"), ("2", "enforce"), ("3", "enforce"),
            ("4", "off"), ("5", "off"), ("6", "enforce")]  # fmt: skip
    return [
        COMMON.format(slide="--slide 4", dpi=600, stop=g) + f" -o {attempt}/ab-{n}.tiff"
        for n, g in plan
    ]


def armc_expected(attempt):
    plan = [("C1", "off", "enforce"), ("ctl1", "enforce", "off"),
            ("C2", "off", "enforce"), ("ctl2", "enforce", "off")]  # fmt: skip
    return [
        COMMON.format(slide="--slide 4", dpi=600, stop=s)
        + f" --cal-wait-bufempty {b} -o {attempt}/armc-{n}.tiff"
        for n, s, b in plan
    ]


def tray_expected(attempt):
    return [
        COMMON.format(slide="--slides 1-4", dpi=3600, stop="enforce") + f" -o {attempt}/tray.tiff"
    ]


SCRIPTS = [
    ("ab_gate_test.sh", lambda a: [str(a)], ab_expected),
    ("tray_and_armc_test.sh", lambda a: ["armc", str(a)], armc_expected),
    ("tray_and_armc_test.sh", lambda a: ["tray", str(a)], tray_expected),
]
IDS = ["ab", "armc", "tray"]


@pytest.mark.parametrize(("script", "args", "expected"), SCRIPTS, ids=IDS)
def test_driver_script_runs_exact_flags_in_order(driver, tmp_path, script, args, expected):
    r, lines, attempt = run_script(script, args, driver, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert lines == expected(attempt)
    assert "OPTICFILM_HW_ASSUME_YES" in r.stdout + r.stderr


@pytest.mark.parametrize(("script", "args", "expected"), SCRIPTS, ids=IDS)
def test_driver_script_aborts_without_confirmation(driver, tmp_path, script, args, expected):
    r, lines, _ = run_script(script, args, driver, tmp_path, assume_yes=False)
    assert r.returncode == 1
    assert lines == []
    assert "confirmation" in r.stdout + r.stderr


@pytest.mark.parametrize(("script", "args", "expected"), SCRIPTS, ids=IDS)
def test_piped_enter_is_not_a_confirmation(driver, tmp_path, script, args, expected):
    r, lines, _ = run_script(script, args, driver, tmp_path, assume_yes=False, stdin_text="\n")
    assert r.returncode == 1
    assert lines == []


@pytest.mark.parametrize(("script", "args", "expected"), SCRIPTS[:2], ids=IDS[:2])
def test_driver_script_stops_at_first_failed_scan(driver, tmp_path, script, args, expected):
    r, lines, attempt = run_script(script, args, driver, tmp_path, fail_at=2)
    assert r.returncode == 1
    assert lines == expected(attempt)[:2]


def test_tray_failure_exits_nonzero(driver, tmp_path):
    r, lines, _ = run_script("tray_and_armc_test.sh", SCRIPTS[2][1], driver, tmp_path, fail_at=1)
    assert r.returncode == 1
    assert len(lines) == 1


def test_unknown_phase_exits_2(driver, tmp_path):
    r, lines, _ = run_script("tray_and_armc_test.sh", lambda a: ["bogus", str(a)], driver, tmp_path)
    assert r.returncode == 2
    assert lines == []


def test_merge_script_requires_arguments():
    r = subprocess.run(
        [BASH, str(SCRIPT_DIR / "merge_when_green.sh")], capture_output=True, text=True
    )
    assert r.returncode != 0
    assert "usage" in r.stderr
