"""Tests for scripts/hardware/*.sh using a fake driver package (no hardware, network or gh)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is not installed")

REPO = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO / "scripts" / "hardware"
HW_RUN = SCRIPT_DIR / "hw_run.sh"
ALL_SCRIPTS = ["hw_run.sh", "ab_gate_test.sh", "tray_and_armc_test.sh", "merge_when_green.sh"]

FAKE_MAIN = """\
import os, sys
print("trace=" + os.environ.get("OF135I_TRACE", ""))
print("dumpcal=" + os.environ.get("OF135I_DUMP_CAL", ""))
print("args=" + " ".join(sys.argv[1:]))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


@pytest.fixture
def driver(tmp_path: Path) -> Path:
    pkg = tmp_path / "driver" / "of135i"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text(FAKE_MAIN)
    return tmp_path / "driver"


@pytest.fixture(autouse=True)
def _clean_logs():
    # hw_run.sh writes logs under <repo>/spike/logs; remove only what a test created.
    logs = REPO / "spike" / "logs"
    before = set(logs.glob("*")) if logs.exists() else set()
    yield
    if logs.exists():
        for p in set(logs.glob("*")) - before:
            p.unlink()


def run_hw(driver_dir, attempt, name, *args, exit_code=0, **env):
    e = {k: v for k, v in os.environ.items() if k not in ("HW_RUN_NO_DOCTOR", "DOCTOR_CMD")}
    e.pop("OPTICFILM_DRIVER_DIR", None)
    e["FAKE_EXIT"] = str(exit_code)
    if driver_dir is not None:
        e["OPTICFILM_DRIVER_DIR"] = str(driver_dir)
    e.update(env)
    return subprocess.run(
        [BASH, str(HW_RUN), str(attempt), name, *args],
        capture_output=True,
        text=True,
        env=e,
        timeout=60,
    )


def log_text(attempt: Path, name: str) -> str:
    return (REPO / "spike" / "logs" / f"{attempt.name}-{name}.log").read_text()


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
