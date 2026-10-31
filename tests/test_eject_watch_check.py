"""scripts/eject-watch-check.sh, the one-command no-motor check. Offline: stub systemctl and
journalctl on PATH, fake /proc and sysfs trees, a stub installer. No systemd, no scanner."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "eject-watch-check.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")

SYSTEMCTL_STUB = """#!/bin/bash
echo "$*" >>"$STUB_LOG"
case "$2" in
start)
    if [[ -z "${NO_PROC:-}" ]]; then
        mkdir -p "$PROC_ROOT/4242"
        printf 'python3\\0-m\\0of135i\\0watch\\0' >"$PROC_ROOT/4242/cmdline"
    fi
    ;;
stop)
    sleep "${STOP_SLEEP:-0}"
    [[ -n "${LEAVE_PROC:-}" ]] || rm -rf "$PROC_ROOT/4242"
    ;;
is-active) echo "${IS_ACTIVE:-inactive}" ;;
esac
exit 0
"""

JOURNALCTL_STUB = """#!/bin/bash
echo "journalctl $*" >>"$STUB_LOG"
echo "${JOURNAL_LINE:-watch: no scanner found}"
"""

INSTALLER_STUB = """#!/bin/bash
echo "installer $*" >>"$STUB_LOG"
exit "${INSTALL_RC:-0}"
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def rig(tmp_path: Path):
    scripts = tmp_path / "scripts"
    bindir = tmp_path / "bin"
    proc = tmp_path / "proc"
    sysfs = tmp_path / "sys"
    for d in (scripts, bindir, proc, sysfs):
        d.mkdir()
    (proc / "1").mkdir()
    (proc / "1" / "cmdline").write_bytes(b"/sbin/init\0")
    (sysfs / "1-1").mkdir()
    (sysfs / "1-1" / "idVendor").write_text("8087\n")
    (sysfs / "1-1" / "idProduct").write_text("0024\n")
    shutil.copy(CHECK, scripts / "eject-watch-check.sh")
    _exe(scripts / "install-eject-watch.sh", INSTALLER_STUB)
    _exe(bindir / "systemctl", SYSTEMCTL_STUB)
    _exe(bindir / "journalctl", JOURNALCTL_STUB)
    log = tmp_path / "calls.log"
    log.write_text("")
    driver = tmp_path / "driver"
    driver.mkdir()
    base_env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "PROC_ROOT": str(proc),
        "SYSFS_USB_ROOT": str(sysfs),
        "STUB_LOG": str(log),
        "START_WAIT_SECS": "3",
    }
    base_env.pop("OPTICFILM_DRIVER_DIR", None)

    def run(*args: str, **env: str):
        r = subprocess.run(
            [str(scripts / "eject-watch-check.sh"), *args],
            capture_output=True,
            text=True,
            env={**base_env, **env},
            check=False,
            timeout=60,
        )
        calls = log.read_text().splitlines()
        return r, calls

    run.driver = str(driver)
    run.sysfs = sysfs
    return run


def _last_line(r) -> str:
    return r.stdout.strip().splitlines()[-1]


def _systemctl_calls(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("--user")]


def test_full_pass_path(rig):
    r, calls = rig(rig.driver)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _last_line(r) == "PASS"
    assert r.stdout.count("PASS") == 1
    sc = _systemctl_calls(calls)
    assert sc.index("--user reset-failed opticfilm-eject-watch.service") < sc.index(
        "--user start opticfilm-eject-watch.service"
    )
    assert any(c.startswith("installer ") and rig.driver in c for c in calls)
    assert not any(" enable " in f" {c} " for c in sc)
    assert "systemctl --user enable opticfilm-eject-watch.service" in r.stdout


def test_driver_dir_from_env(rig):
    r, calls = rig(OPTICFILM_DRIVER_DIR=rig.driver)
    assert r.returncode == 0
    assert f"installer {rig.driver}" in calls


def test_usage_error_without_driver_dir(rig):
    r, calls = rig()
    assert r.returncode == 2
    assert calls == []


def test_scanner_present_refuses_without_any_systemctl_call(rig):
    dev = rig.sysfs / "2-3"
    dev.mkdir()
    (dev / "idVendor").write_text("07b3\n")
    (dev / "idProduct").write_text("1436\n")
    r, calls = rig("--enable", rig.driver)
    assert r.returncode != 0
    assert "OFF" in r.stdout
    assert calls == []


def test_unreadable_sysfs_refuses(rig):
    r, calls = rig(rig.driver, SYSFS_USB_ROOT="/nonexistent-sysfs-root")
    assert r.returncode != 0
    assert calls == []


def test_installer_failure_fails(rig):
    r, calls = rig(rig.driver, INSTALL_RC="1")
    assert r.returncode != 0
    assert _last_line(r) == "FAIL"
    assert not any("start" in c for c in _systemctl_calls(calls))
    assert not any(" enable " in f" {c} " for c in _systemctl_calls(calls))


def test_process_never_appears_fails_with_journal(rig):
    r, calls = rig(rig.driver, NO_PROC="1", START_WAIT_SECS="2", JOURNAL_LINE="cd: null directory")
    assert r.returncode != 0
    assert _last_line(r) == "FAIL"
    assert "cd: null directory" in r.stdout
    assert any("--since -2min" in c for c in calls)


def test_slow_stop_fails(rig):
    r, _ = rig(rig.driver, STOP_SLEEP="3", STOP_FAIL_SECS="1", STOP_WARN_SECS="0")
    assert r.returncode != 0
    assert _last_line(r) == "FAIL"
    assert "stop took" in r.stdout


def test_slowish_stop_warns_but_passes(rig):
    r, _ = rig(rig.driver, STOP_SLEEP="2", STOP_FAIL_SECS="30", STOP_WARN_SECS="1")
    assert r.returncode == 0
    assert "WARN" in r.stdout


def test_failed_state_fails(rig):
    r, _ = rig(rig.driver, IS_ACTIVE="failed")
    assert r.returncode != 0
    assert _last_line(r) == "FAIL"
    assert "'failed'" in r.stdout


def test_leftover_process_fails(rig):
    r, _ = rig(rig.driver, LEAVE_PROC="1")
    assert r.returncode != 0
    assert _last_line(r) == "FAIL"
    assert "still running" in r.stdout


@pytest.mark.parametrize("line", ["Main process SIGKILL sent", "unit killed by signal"])
def test_sigkill_in_journal_fails(rig, line):
    r, _ = rig(rig.driver, JOURNAL_LINE=line)
    assert r.returncode != 0
    assert _last_line(r) == "FAIL"


def test_enable_only_after_pass_and_never_started_after(rig):
    r, calls = rig("--enable", rig.driver)
    assert r.returncode == 0
    sc = _systemctl_calls(calls)
    enable_at = sc.index("--user enable opticfilm-eject-watch.service")
    assert enable_at == len(sc) - 1
    assert sc.count("--user start opticfilm-eject-watch.service") == 1
    assert sc.index("--user start opticfilm-eject-watch.service") < enable_at
    assert _last_line(r) == "PASS"


@pytest.mark.parametrize(
    "env",
    [
        {"IS_ACTIVE": "failed"},
        {"LEAVE_PROC": "1"},
        {"JOURNAL_LINE": "SIGKILL"},
        {"INSTALL_RC": "1"},
    ],
)
def test_no_enable_when_anything_fails(rig, env):
    r, calls = rig("--enable", rig.driver, **env)
    assert r.returncode != 0
    assert not any(" enable " in f" {c} " for c in _systemctl_calls(calls))


def test_failure_stops_unit_and_sends_no_signals(rig):
    r, calls = rig(rig.driver, IS_ACTIVE="failed")
    sc = _systemctl_calls(calls)
    assert sc.count("--user stop opticfilm-eject-watch.service") == 1
    assert "kill" not in " ".join(sc).lower()
    assert "SIGKILL" not in r.stdout.split("FAIL:")[0]
