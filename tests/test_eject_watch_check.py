"""scripts/eject-watch-check.sh, the one-command check. Offline: stub systemctl and journalctl on
PATH, fake /proc, sysfs and cgroup trees, a stub installer. No systemd, no scanner."""

import os
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "eject-watch-check.sh"
APP_SRC = ROOT / "src" / "opticfilm_linux"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("python3") is None, reason="bash/python3 missing"
)

UNIT = "opticfilm-eject-watch.service"
SYSTEMCTL_STUB = """#!/bin/bash
echo "$*" >>"$STUB_LOG"
S="$STUB_STATE"
state() { if [[ -f "$S/active" ]]; then cat "$S/active"; else echo inactive; fi; }
case "$2" in
start)
    [[ -z "${BLOCK_START:-}" ]] || sleep "$BLOCK_START"
    echo "${START_STATE:-active}" >"$S/active"
    if [[ -z "${NO_PROC:-}" ]]; then
        mkdir -p "$PROC_ROOT/4242"
        printf 'python3\\0-m\\0of135i\\0watch\\0' >"$PROC_ROOT/4242/cmdline"
        [[ -z "${PROC_EXIT_AFTER:-}" ]] ||
            (sleep "$PROC_EXIT_AFTER"; rm -rf "$PROC_ROOT/4242") >/dev/null 2>&1 &
    fi
    ;;
stop)
    sleep "${STOP_SLEEP:-0}"
    if [[ -z "${STOP_KEEPS:-}" ]]; then
        echo "${STOP_STATE:-inactive}" >"$S/active"
        [[ -n "${LEAVE_PROC:-}" ]] || rm -rf "$PROC_ROOT/4242"
    fi
    ;;
is-active) if [[ -n "${IS_ACTIVE:-}" ]]; then echo "$IS_ACTIVE"; else state; fi ;;
enable) echo enabled >"$S/enabled" ;;
disable) [[ -n "${DISABLE_FAILS:-}" ]] || echo disabled >"$S/enabled" ;;
is-enabled)
    if [[ -f "$S/enabled" ]]; then cat "$S/enabled"
    elif [[ -n "${INIT_ENABLED:-}" ]]; then echo enabled
    else echo disabled; fi
    ;;
show)
    case "$*" in
    *ControlGroup*) echo "ControlGroup=${CG:-}" ;;
    *MainPID*) echo "MainPID=${MAINPID-4242}" ;;
    *ExecMainStartTimestamp*)
        echo "ExecMainStartTimestamp=${SHOW_START_TS-Sat 2026-10-31 10:00:00 UTC}"
        echo "ExecMainStatus=1"
        ;;
    esac
    ;;
esac
exit 0
"""

JOURNALCTL_STUB = """#!/bin/bash
echo "journalctl $*" >>"$STUB_LOG"
if [[ "$*" == *'--since -2min'* ]]; then
    [[ -z "${JOURNAL_STALE:-}" ]] || echo "$JOURNAL_STALE"
elif ! [[ "$*" =~ --since\\ [0-9]{4}-[0-9]{2}-[0-9]{2}\\  ]]; then
    echo "journalctl stub: unexpected --since form" >&2
fi
echo "${JOURNAL_LINE-of135i: Scanner 07b3:1436 not found on the host USB bus}"
"""

INSTALLER_STUB = """#!/bin/bash
echo "installer $*" >>"$STUB_LOG"
echo "runner: ${STUB_RUNNER:-distrobox:dev} (stub)"
exit "${INSTALL_RC:-0}"
"""

WATCH_ARGV = b"python3\0-m\0of135i\0watch\0"


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _add_device(sysfs: Path, name: str, vid: str, pid: str) -> None:
    dev = sysfs / name
    dev.mkdir()
    (dev / "idVendor").write_text(f"{vid}\n")
    (dev / "idProduct").write_text(f"{pid}\n")


@pytest.fixture
def rig(tmp_path: Path):
    scripts = tmp_path / "scripts"
    bindir = tmp_path / "bin"
    proc = tmp_path / "proc"
    sysfs = tmp_path / "sys"
    state = tmp_path / "state"
    for d in (scripts, bindir, proc, sysfs, state, tmp_path / "driver"):
        d.mkdir()
    shutil.copytree(
        APP_SRC, tmp_path / "src" / "opticfilm_linux", ignore=shutil.ignore_patterns("*")
    )
    (tmp_path / "src" / "opticfilm_linux" / "app").mkdir()
    shutil.copy(APP_SRC / "__init__.py", tmp_path / "src" / "opticfilm_linux")
    for name in ("__init__.py", "eject_watch.py"):
        shutil.copy(APP_SRC / "app" / name, tmp_path / "src" / "opticfilm_linux" / "app")
    (proc / "1").mkdir()
    (proc / "1" / "cmdline").write_bytes(b"/sbin/init\0")
    _add_device(sysfs, "1-1", "8087", "0024")
    shutil.copy(CHECK, scripts / "eject-watch-check.sh")
    _exe(scripts / "install-eject-watch.sh", INSTALLER_STUB)
    _exe(bindir / "systemctl", SYSTEMCTL_STUB)
    _exe(bindir / "journalctl", JOURNALCTL_STUB)
    log = tmp_path / "calls.log"
    log.write_text("")
    cgroot = tmp_path / "cgroup"
    cgroot.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "EJECT_WATCH_CHECK_TESTING": "1",
        "PROC_ROOT": str(proc),
        "SYSFS_USB_ROOT": str(sysfs),
        "CGROUP_ROOT": str(cgroot),
        "STUB_LOG": str(log),
        "STUB_STATE": str(state),
        "START_WAIT_SECS": "3",
        "SETTLE_SECS": "0",
        "LIVE_HOLD_SECS": "2",
        "CGROUP_WAIT_SECS": "1",
    }
    for k in ("OPTICFILM_DRIVER_DIR", "INIT_ENABLED"):
        env.pop(k, None)

    def run(*args: str, **extra: str):
        r = subprocess.run(
            [str(scripts / "eject-watch-check.sh"), *args],
            capture_output=True,
            text=True,
            env={**env, **extra},
            check=False,
            timeout=90,
        )
        return r, log.read_text().splitlines()

    def popen(*args: str, **extra: str):
        return subprocess.Popen(
            [str(scripts / "eject-watch-check.sh"), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**env, **extra},
        )

    return SimpleNamespace(
        run=run,
        popen=popen,
        driver=str(tmp_path / "driver"),
        sysfs=sysfs,
        proc=proc,
        cgroot=cgroot,
        log=log,
        tmp=tmp_path,
    )


def _last(r) -> str:
    return r.stdout.strip().splitlines()[-1]


def _sc(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("--user")]


def _verbs(calls: list[str]) -> list[str]:
    return [c.split()[1] for c in _sc(calls)]


def _scanner_on(rig) -> None:
    _add_device(rig.sysfs, "2-3", "07b3", "1436")


# -- default (scanner OFF) mode ------------------------------------------------------------


def test_full_pass_path(rig):
    r, calls = rig.run(rig.driver)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _last(r).startswith("PASS (scanner-off mode")
    assert "only proven by --live" in _last(r)
    assert r.stdout.count("PASS") == 1
    verbs = _verbs(calls)
    assert verbs.index("reset-failed") < verbs.index("start") < verbs.index("stop")
    assert any(c.startswith("installer ") and rig.driver in c for c in calls)
    assert "enable" not in verbs
    assert f"systemctl --user enable {UNIT}" in r.stdout


def test_pass_when_process_never_seen_but_driver_output_is_in_the_journal(rig):
    r, _ = rig.run(rig.driver, NO_PROC="1")
    assert r.returncode == 0, r.stdout
    assert "watcher process seen: 0" in r.stdout


def test_pass_via_execmain_timestamp_when_journal_has_no_driver_line(rig):
    r, _ = rig.run(rig.driver, NO_PROC="1", JOURNAL_LINE="Started the thing")
    assert r.returncode == 0, r.stdout
    assert "ExecStart ran" in r.stdout


def test_no_sign_of_execstart_fails(rig):
    r, _ = rig.run(rig.driver, NO_PROC="1", SHOW_START_TS="", JOURNAL_LINE="unrelated")
    assert r.returncode == 1
    assert _last(r) == "FAIL"
    assert "no sign that ExecStart ran" in r.stdout


@pytest.mark.parametrize(
    "line",
    [
        "cd: null directory",
        "eval: syntax error",
        "No such file or directory",
        "bash: x: command not found",
        "Permission denied",
    ],
)
def test_previous_failure_signatures_fail_with_journal(rig, line):
    r, calls = rig.run(rig.driver, NO_PROC="1", JOURNAL_LINE=line)
    assert r.returncode == 1
    assert _last(r) == "FAIL"
    assert line in r.stdout
    assert any("--since -2min" in c for c in calls)


def test_remains_running_lines_are_tolerated(rig):
    line = "Unit process 1234 (conmon) remains running after unit stopped."
    r, _ = rig.run(rig.driver, JOURNAL_LINE=f"of135i: not found on the host USB bus\n{line}")
    assert r.returncode == 0, r.stdout


def test_leftover_cgroup_process_fails_but_empty_cgroup_passes(rig):
    cg = rig.cgroot / "user.slice" / UNIT
    cg.mkdir(parents=True)
    (cg / "cgroup.procs").write_text("777\n")
    r, _ = rig.run(rig.driver, CG=f"/user.slice/{UNIT}")
    assert r.returncode == 1
    assert "processes remain in the unit's cgroup" in r.stdout
    (cg / "cgroup.procs").write_text("")
    r, _ = rig.run(rig.driver, CG=f"/user.slice/{UNIT}")
    assert r.returncode == 0, r.stdout
    assert "no process left in the unit's cgroup" in r.stdout


def test_unknown_cgroup_is_skipped_with_a_note(rig):
    r, _ = rig.run(rig.driver)
    assert "skipped the leftover conmon/podman check" in r.stdout


def test_driver_dir_from_env(rig):
    r, calls = rig.run(OPTICFILM_DRIVER_DIR=rig.driver)
    assert r.returncode == 0
    assert f"installer --runner auto {rig.driver}" in calls


@pytest.mark.parametrize("flag", [["--runner", "host"], ["--runner=host"]])
def test_runner_is_passed_to_the_installer(rig, flag):
    r, calls = rig.run(*flag, rig.driver, STUB_RUNNER="host")
    assert r.returncode == 0, r.stdout
    assert f"installer --runner host {rig.driver}" in calls
    assert "(runner: host)" in r.stdout


def test_runner_needs_a_value(rig):
    r, calls = rig.run(rig.driver, "--runner")
    assert r.returncode == 2 and calls == []


def test_host_live_requires_mainpid_to_be_the_watcher(rig):
    _scanner_on(rig)
    r, _ = rig.run("--live", rig.driver, STUB_RUNNER="host")
    assert r.returncode == 0, r.stdout
    r, _ = rig.run("--live", rig.driver, STUB_RUNNER="host", MAINPID="1")  # /proc/1 is init
    assert r.returncode == 1
    assert "is not 'of135i watch'" in r.stdout


def test_host_scanner_off_pass(rig):
    r, _ = rig.run(rig.driver, STUB_RUNNER="host", NO_PROC="1")
    assert r.returncode == 0, r.stdout


def test_usage_errors(rig):
    for args in ([], ["--bogus", "x"], ["a", "b"]):
        r, calls = rig.run(*args)
        assert r.returncode == 2
        assert calls == []


def test_installer_failure_fails(rig):
    r, calls = rig.run(rig.driver, INSTALL_RC="1")
    assert r.returncode == 1
    assert _last(r) == "FAIL"
    assert "start" not in _verbs(calls)
    assert "enable" not in _verbs(calls)


def test_slow_stop_fails(rig):
    r, _ = rig.run(rig.driver, STOP_SLEEP="3", STOP_FAIL_SECS="1", STOP_WARN_SECS="0")
    assert r.returncode == 1
    assert "stop took" in r.stdout


def test_slowish_stop_warns_but_passes(rig):
    r, _ = rig.run(rig.driver, STOP_SLEEP="2", STOP_FAIL_SECS="30", STOP_WARN_SECS="1")
    assert r.returncode == 0
    assert "WARN" in r.stdout


def test_stop_wall_limit_branch(rig):
    r, _ = rig.run(rig.driver, STOP_SLEEP="5", STOP_WALL_SECS="1")
    assert r.returncode == 1
    assert "did not finish within 1s" in r.stdout


def test_failed_state_fails(rig):
    r, _ = rig.run(rig.driver, STOP_STATE="failed")
    assert r.returncode == 1
    assert "'failed'" in r.stdout


def test_leftover_process_fails(rig):
    r, _ = rig.run(rig.driver, LEAVE_PROC="1")
    assert r.returncode == 1
    assert "still running" in r.stdout


@pytest.mark.parametrize("line", ["Main process SIGKILL sent", "code=killed, status=9/KILL"])
def test_sigkill_in_this_runs_journal_fails(rig, line):
    r, _ = rig.run(rig.driver, JOURNAL_LINE=f"of135i: x\n{line}")
    assert r.returncode == 1


def test_stale_killed_line_before_the_start_is_ignored(rig):
    r, calls = rig.run(rig.driver, JOURNAL_STALE="old: code=killed, status=9/KILL")
    assert r.returncode == 0, r.stdout
    assert any(" --since 20" in c for c in calls)


# -- enable gating --------------------------------------------------------------------------


def test_enable_only_after_pass_and_never_started_after(rig):
    r, calls = rig.run("--enable", rig.driver)
    assert r.returncode == 0
    verbs = _verbs(calls)
    assert verbs[-1] != "start"
    assert verbs.index("enable") > verbs.index("stop")
    assert "start" not in verbs[verbs.index("enable") :]
    assert verbs.count("start") == 1


@pytest.mark.parametrize(
    "env",
    [
        {"STOP_STATE": "failed"},
        {"LEAVE_PROC": "1"},
        {"JOURNAL_LINE": "SIGKILL"},
        {"INSTALL_RC": "1"},
    ],
)
def test_no_enable_when_anything_fails(rig, env):
    r, calls = rig.run("--enable", rig.driver, **env)
    assert r.returncode == 1
    assert "enable" not in _verbs(calls)


# -- guard ----------------------------------------------------------------------------------


def test_scanner_present_refuses_without_any_systemctl_call(rig):
    _scanner_on(rig)
    r, calls = rig.run("--enable", rig.driver)
    assert r.returncode == 3
    assert "OFF" in r.stdout
    assert calls == []


def test_empty_existing_sysfs_root_refuses(rig):
    empty = rig.tmp / "empty"
    empty.mkdir()
    r, calls = rig.run(rig.driver, SYSFS_USB_ROOT=str(empty))
    assert r.returncode == 3
    assert calls == []


def test_missing_sysfs_root_refuses(rig):
    r, calls = rig.run(rig.driver, SYSFS_USB_ROOT=str(rig.tmp / "nope"))
    assert r.returncode == 3
    assert calls == []


def test_sysfs_with_only_interface_entries_refuses(rig):
    only_ifaces = rig.tmp / "ifaces"
    (only_ifaces / "1-0:1.0").mkdir(parents=True)
    r, calls = rig.run(rig.driver, SYSFS_USB_ROOT=str(only_ifaces))
    assert r.returncode == 3
    assert calls == []


def test_unreadable_idvendor_refuses(rig):
    (rig.sysfs / "9-9").mkdir()
    (rig.sysfs / "9-9" / "idVendor").mkdir()  # exists but reading it fails, even as root
    r, calls = rig.run(rig.driver)
    assert r.returncode == 3
    assert calls == []


def test_overrides_are_ignored_without_the_testing_switch(rig):
    empty = rig.tmp / "empty"
    empty.mkdir()
    r, _ = rig.run(rig.driver, EJECT_WATCH_CHECK_TESTING="", SYSFS_USB_ROOT=str(empty))
    assert "ignoring the test override SYSFS_USB_ROOT" in r.stdout
    assert str(empty) not in r.stdout.replace("ignoring the test override SYSFS_USB_ROOT", "")


# -- other watchers and /proc ---------------------------------------------------------------


def test_decoy_watcher_before_start_refuses(rig):
    (rig.proc / "999").mkdir()
    (rig.proc / "999" / "cmdline").write_bytes(WATCH_ARGV)
    r, calls = rig.run(rig.driver)
    assert r.returncode == 4
    assert "kill -INT" in r.stdout
    assert calls == []


def test_unreadable_proc_fails_closed(rig):
    r, calls = rig.run(rig.driver, PROC_ROOT=str(rig.tmp / "no-proc"))
    assert r.returncode == 4
    assert calls == []


def test_similar_command_line_does_not_count(rig):
    (rig.proc / "999").mkdir()
    (rig.proc / "999" / "cmdline").write_bytes(b"python3\0-m\0of135i\0watchdog\0")
    r, _ = rig.run(rig.driver)
    assert r.returncode == 0, r.stdout


# -- cleanup, trap and honest FAIL messages -------------------------------------------------


def test_fail_disables_an_enabled_unit_and_reports_real_state(rig):
    r, calls = rig.run(rig.driver, IS_ACTIVE="failed", INIT_ENABLED="1")
    assert r.returncode == 1
    assert "disable" in _verbs(calls)
    assert "enable" not in _verbs(calls)
    assert "is-enabled=disabled" in r.stdout


def test_fail_message_is_truthful_when_unit_stays_active_and_enabled(rig):
    r, calls = rig.run(rig.driver, STOP_KEEPS="1", DISABLE_FAILS="1", INIT_ENABLED="1")
    assert r.returncode == 1
    assert "is-active=active" in r.stdout
    assert "is-enabled=enabled" in r.stdout
    assert "STILL RUNNING" in r.stdout
    assert "never kill -9" in r.stdout
    assert "still ENABLED" in r.stdout
    assert "left disabled and stopped" not in r.stdout
    assert _verbs(calls).count("stop") >= 2  # the check's own stop plus one more bounded stop


def test_fail_reports_leftover_watcher_process(rig):
    r, _ = rig.run(rig.driver, LEAVE_PROC="1")
    assert "watcher process=yes" in r.stdout
    assert "kill -INT" in r.stdout


def test_sigint_runs_the_stop_path(rig):
    p = rig.popen(rig.driver, BLOCK_START="5")
    for _ in range(100):
        if any(" start " in f" {c} " for c in rig.log.read_text().splitlines()):
            break
        time.sleep(0.1)
    else:
        p.kill()
        pytest.fail("start was never called")
    p.send_signal(signal.SIGINT)
    out, _ = p.communicate(timeout=30)
    verbs = _verbs(rig.log.read_text().splitlines())
    assert p.returncode == 1
    assert verbs.index("stop") > verbs.index("start")
    assert "interrupted" in out
    assert "final state:" in out
    assert out.strip().splitlines()[-1] == "FAIL"


# -- --live ---------------------------------------------------------------------------------


def test_live_refuses_when_scanner_not_on_bus(rig):
    r, calls = rig.run("--live", rig.driver)
    assert r.returncode == 3
    assert "ON the USB bus" in r.stdout
    assert calls == []


def test_live_pass(rig):
    _scanner_on(rig)
    r, calls = rig.run("--live", "--enable", rig.driver)
    assert r.returncode == 0, r.stdout
    assert "Do NOT press the Eject button" in r.stdout
    assert _last(r).startswith("PASS (--live mode")
    verbs = _verbs(calls)
    assert (
        verbs.index("stop") < verbs.index("enable") == len(verbs) - 1 - verbs[::-1].index("enable")
    )


def test_live_inconclusive_when_watcher_exits_by_itself(rig):
    _scanner_on(rig)
    r, calls = rig.run(
        "--live",
        "--enable",
        rig.driver,
        PROC_EXIT_AFTER="1",
        LIVE_HOLD_SECS="4",
        JOURNAL_LINE="of135i: scanner is not idle-homed",
    )
    assert r.returncode == 5
    assert "INCONCLUSIVE (watcher refused to run" in r.stdout
    assert "live stop path was NOT tested" in r.stdout
    assert "PASS" not in r.stdout
    assert "scanner is not idle-homed" in r.stdout
    assert "enable" not in _verbs(calls)
    assert "stop" in _verbs(calls)


def test_live_inconclusive_when_process_never_appears(rig):
    _scanner_on(rig)
    r, _ = rig.run("--live", rig.driver, NO_PROC="1", START_WAIT_SECS="2")
    assert r.returncode == 5


def test_live_fails_when_watcher_is_not_tied_to_the_unit(rig):
    _scanner_on(rig)
    r, _ = rig.run("--live", rig.driver, MAINPID="0")
    assert r.returncode == 1
    assert "not tied to the unit" in r.stdout


def test_live_fails_when_unit_is_not_running_but_process_is(rig):
    _scanner_on(rig)
    r, _ = rig.run("--live", rig.driver, IS_ACTIVE="inactive")
    assert r.returncode == 1
    assert "the unit is 'inactive'" in r.stdout


def test_live_leftover_process_after_stop_fails(rig):
    _scanner_on(rig)
    r, _ = rig.run("--live", rig.driver, LEAVE_PROC="1", IS_ACTIVE="active")
    assert r.returncode == 1
