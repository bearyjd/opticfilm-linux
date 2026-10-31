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
        printf '4242 (python3 watcher) S%s 100 0\\n' "$(printf ' 0%.0s' {1..18})" \
            >"$PROC_ROOT/4242/stat"
        printf '0::%s\\n' "${WATCH_CGROUP:-/test-unit}" >"$PROC_ROOT/4242/cgroup"
        [[ -z "${PROC_EXIT_AFTER:-}" ]] ||
            (sleep "$PROC_EXIT_AFTER"; rm -rf "$PROC_ROOT/4242") >/dev/null 2>&1 &
    fi
    ;;
stop)
    touch "$S/stopped"
    if [[ -n "${FAIL_FIRST_STOP:-}" && ! -f "$S/stop-failed" ]]; then
        touch "$S/stop-failed"
        sleep "${FAILED_STOP_SLEEP:-0}"
        [[ -z "${FAILED_STOP_STATE:-}" ]] || echo "$FAILED_STOP_STATE" >"$S/active"
        exit 1
    fi
    sleep "${STOP_SLEEP:-0}"
    if [[ -z "${STOP_KEEPS:-}" ]]; then
        echo "${STOP_STATE:-inactive}" >"$S/active"
        [[ -n "${LEAVE_PROC:-}" ]] || rm -rf "$PROC_ROOT/4242" "$PROC_ROOT/4343"
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
    *FragmentPath*)
        echo "FragmentPath=${FRAGMENT_PATH-$EJECT_WATCH_UNIT_DIR/opticfilm-eject-watch.service}"
        exit "${FRAGMENT_RC:-0}"
        ;;
    *DropInPaths*)
        echo "DropInPaths=${DROPIN_PATHS:-}"
        rendered=$(sed -n 's/^ExecStart=//p' "$EJECT_WATCH_UNIT_DIR/opticfilm-eject-watch.service")
        default_start="{ path=${rendered%% *} ; argv[]=$rendered ; ignore_errors=no }"
        echo "ExecStart=${EFFECTIVE_EXECSTART-$default_start}"
        echo "KillSignal=${KILL_SIGNAL:-2}"
        echo "SendSIGKILL=${SEND_SIGKILL:-no}"
        exit "${EFFECTIVE_RC:-0}"
        ;;
    *ControlGroup*) echo "ControlGroup=${CG:-}" ;;
    *MainPID*)
        n=0; [[ ! -f "$S/pid-reads" ]] || n=$(cat "$S/pid-reads")
        echo $((n + 1)) >"$S/pid-reads"
        if [[ "$n" -gt 0 && -n "${REPLACE_WATCHER:-}" ]]; then
            if [[ "$REPLACE_WATCHER" == pid && -d "$PROC_ROOT/4242" ]]; then
                mv "$PROC_ROOT/4242" "$PROC_ROOT/4343"
                printf '4343 (python3 watcher) S%s 200 0\\n' "$(printf ' 0%.0s' {1..18})" \
                    >"$PROC_ROOT/4343/stat"
            elif [[ "$REPLACE_WATCHER" == reused ]]; then
                printf '4242 (python3 watcher) S%s 200 0\\n' "$(printf ' 0%.0s' {1..18})" \
            >"$PROC_ROOT/4242/stat"
            fi
        fi
        if [[ -n "${PID_CHANGES:-}" && "$n" -gt 0 ]]; then
            echo "MainPID=1"
        else echo "MainPID=${MAINPID-4242}"; fi
        ;;
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
if [[ -n "${JOURNAL_FAIL_AFTER_STOP:-}" && -f "$STUB_STATE/stopped" ]]; then exit 1; fi
[[ -z "${JOURNAL_STDERR:-}" ]] || echo "$JOURNAL_STDERR" >&2
if [[ "$*" == *'--since -2min'* ]]; then
    [[ -z "${JOURNAL_STALE:-}" ]] || echo "$JOURNAL_STALE"
elif ! [[ "$*" =~ --since\\ [0-9]{4}-[0-9]{2}-[0-9]{2}\\  ]]; then
    echo "journalctl stub: unexpected --since form" >&2
fi
if [[ -n "${JOURNAL_OLD_SAME_SECOND:-}" && "${@: -2:1}" != *.* ]]; then
    echo "old: of135i: not found on the host USB bus"
else
    echo "${JOURNAL_LINE-of135i: Scanner 07b3:1436 not found on the host USB bus}"
fi
exit "${JOURNAL_RC:-0}"
"""

INSTALLER_STUB = """#!/bin/bash
echo "installer $*" >>"$STUB_LOG"
mkdir -p "$EJECT_WATCH_UNIT_DIR"
if [[ "${STUB_RUNNER:-distrobox:dev}" == host ]]; then
    rendered="/usr/bin/python3 -m of135i watch"
else
    rendered="/usr/bin/env distrobox-enter --no-tty -n dev -- /checkout/eject-watch-run.sh ${@: -1}"
fi
unit_path="$EJECT_WATCH_UNIT_DIR/opticfilm-eject-watch.service"
printf '[Service]\\nExecStart=%s\\nKillSignal=SIGINT\\nSendSIGKILL=no\\n' "$rendered" >"$unit_path"
echo "runner: ${STUB_RUNNER:-distrobox:dev} (stub)"
if [[ -n "${STUB_HOST_PY:-}" ]]; then
    echo "host-python: $STUB_HOST_PY"
    echo "host-pythonpath: $STUB_HOST_PP"
fi
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
        "EJECT_WATCH_UNIT_DIR": str(tmp_path / "units"),
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
        env=env,
    )


def _last(r) -> str:
    return r.stdout.strip().splitlines()[-1]


def _sc(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("--user")]


def _verbs(calls: list[str]) -> list[str]:
    return [c.split()[1] for c in _sc(calls)]


def _scanner_on(rig) -> None:
    _add_device(rig.sysfs, "2-3", "07b3", "1436")
    rig.env["JOURNAL_LINE"] = "of135i: watching for button events"
    rig.env["CG"] = "/test-unit"


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


@pytest.mark.parametrize(
    "line", ["Started the thing", "error: unrecognized arguments", "RuntimeError: failed", ""]
)
@pytest.mark.parametrize("no_proc", ["", "1"])
def test_timestamp_or_process_without_scanner_off_driver_output_fails(rig, line, no_proc):
    r, _ = rig.run(rig.driver, NO_PROC=no_proc, JOURNAL_LINE=line)
    assert r.returncode == 1, r.stdout
    assert "PASS" not in r.stdout


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
    r, _ = rig.run(rig.driver, JOURNAL_LINE=f"of135i: not found on the host USB bus\n{line}")
    assert r.returncode == 1
    assert "step 4: this run's journal shows SIGKILL" in r.stdout


def test_stale_killed_line_before_the_start_is_ignored(rig):
    r, calls = rig.run(rig.driver, JOURNAL_STALE="old: code=killed, status=9/KILL")
    assert r.returncode == 0, r.stdout
    assert any(" --since 20" in c for c in calls)


def test_scanner_off_evidence_from_earlier_in_same_second_is_not_this_run(rig):
    r, _ = rig.run(
        rig.driver,
        NO_PROC="1",
        JOURNAL_LINE="unrelated",
        JOURNAL_OLD_SAME_SECOND="1",
        START_WAIT_SECS="1",
    )
    assert r.returncode == 1, r.stdout
    assert "PASS" not in r.stdout


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
    assert (rig.tmp / "state" / "active").read_text().strip() == "inactive"
    assert not (rig.proc / "4242").exists()


def _short_timeouts(rig):
    real_timeout = shutil.which("timeout")
    _exe(
        rig.tmp / "bin" / "timeout",
        '#!/bin/bash\necho "timeout $*" >>"$STUB_LOG"\n'
        '[[ "$1" != -k ]] || shift 2\nshift\n'
        f'exec {real_timeout} -k 0.1 0.3 "$@"\n',
    )


def test_cleanup_stops_and_disables_before_journal_diagnostics(rig):
    r, calls = rig.run(rig.driver, JOURNAL_LINE="Traceback")
    assert r.returncode == 1
    tail = next(i for i, call in enumerate(calls) if "--since -2min" in call)
    assert next(i for i, call in enumerate(calls) if call == f"--user stop {UNIT}") < tail
    assert next(i for i, call in enumerate(calls) if call == f"--user disable {UNIT}") < tail


def test_journal_calls_are_bounded_even_when_the_tool_hangs(rig):
    _short_timeouts(rig)
    journal = rig.tmp / "bin" / "journalctl"
    _exe(journal, '#!/bin/bash\ntrap "" TERM\nsleep 5\n')
    before = time.monotonic()
    r, _ = rig.run(rig.driver)
    assert time.monotonic() - before < 4
    assert r.returncode == 1


def test_second_signal_during_cleanup_skips_diagnostics_but_finishes_safety_ops(rig):
    p = rig.popen(rig.driver, JOURNAL_LINE="Traceback", STOP_SLEEP="1")
    for _ in range(200):
        if f"--user stop {UNIT}" in rig.log.read_text().splitlines():
            break
        time.sleep(0.05)
    else:
        p.kill()
        pytest.fail("cleanup stop not reached")
    p.send_signal(signal.SIGTERM)
    p.send_signal(signal.SIGHUP)
    out, _ = p.communicate(timeout=10)
    calls = rig.log.read_text().splitlines()
    assert f"--user disable {UNIT}" in calls
    assert not any("--since -2min" in call for call in calls)
    assert p.returncode == 1, out


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


@pytest.mark.parametrize("line", ["opened device", "not found on the host USB bus", "unrelated"])
def test_live_requires_readiness_before_hold(rig, line):
    _scanner_on(rig)
    r, _ = rig.run("--live", rig.driver, JOURNAL_LINE=line)
    assert r.returncode != 0, r.stdout
    assert "PASS" not in r.stdout


def test_live_pid_must_remain_identical(rig):
    _scanner_on(rig)
    r, _ = rig.run("--live", rig.driver, PID_CHANGES="1")
    assert r.returncode != 0, r.stdout
    assert "MainPID changed" in r.stdout


@pytest.mark.parametrize("extra", [{"CG": ""}, {"WATCH_CGROUP": "/unrelated"}])
def test_live_distrobox_name_match_without_unit_membership_never_passes(rig, extra):
    _scanner_on(rig)
    r, _ = rig.run("--live", rig.driver, **extra)
    assert r.returncode != 0, r.stdout
    assert "PASS" not in r.stdout


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


TRACEBACK = (
    'File "/home/u/drv/of135i/__main__.py", line 1, in <module>\n'
    "ModuleNotFoundError: No module named 'usb'"
)


@pytest.mark.parametrize("runner", ["host", "distrobox:dev"])
@pytest.mark.parametrize("no_proc", ["", "1"])
def test_only_a_traceback_in_the_journal_never_passes(rig, runner, no_proc):
    r, _ = rig.run(rig.driver, STUB_RUNNER=runner, NO_PROC=no_proc, JOURNAL_LINE=TRACEBACK)
    assert r.returncode == 1, r.stdout
    assert "failure signature" in r.stdout


@pytest.mark.parametrize("runner", ["host", "distrobox:dev"])
def test_a_traceback_wins_over_real_evidence(rig, runner):
    line = f"of135i: Scanner 07b3:1436 not found on the host USB bus\n{TRACEBACK}"
    r, _ = rig.run(rig.driver, STUB_RUNNER=runner, JOURNAL_LINE=line)
    assert r.returncode == 1, r.stdout


@pytest.mark.parametrize(
    "line", ["status=127", "status=203/EXEC", "ImportError: x", "SyntaxError: y", "Traceback (most"]
)
def test_more_failure_signatures(rig, line):
    r, _ = rig.run(rig.driver, NO_PROC="1", JOURNAL_LINE=line)
    assert r.returncode == 1


def test_the_word_of135i_alone_is_not_evidence(rig):
    r, _ = rig.run(rig.driver, NO_PROC="1", SHOW_START_TS="", JOURNAL_LINE='File "x/of135i/y.py"')
    assert r.returncode == 1
    assert "no sign that ExecStart ran" in r.stdout


@pytest.mark.parametrize("line", ["watching for button events", "opened device 07b3:1436"])
def test_driver_startup_lines_are_evidence(rig, line):
    r, _ = rig.run(rig.driver, NO_PROC="1", SHOW_START_TS="", JOURNAL_LINE=line)
    assert r.returncode == 1, r.stdout


def _fake_python(path: Path, ok_when: str) -> str:
    path.write_text(f'#!/bin/bash\n[[ "${{PYTHONPATH:-}}" == {ok_when} ]]\n')
    path.chmod(0o755)
    return str(path)


def test_host_mode_probes_the_python_exactly_as_the_unit_will_run(rig):
    py = _fake_python(rig.tmp / "fakepy", rig.driver)
    env = {"STUB_RUNNER": "host", "STUB_HOST_PY": py, "STUB_HOST_PP": rig.driver}
    r, _ = rig.run(rig.driver, PYTHONPATH="/caller/fake", **env)
    assert r.returncode == 0, r.stdout
    env["STUB_HOST_PP"] = f"{rig.driver}:/missing"
    r, _ = rig.run(rig.driver, **env)
    assert r.returncode == 1
    assert "cannot import usb" in r.stdout


def test_usage_text_lists_runner(rig):
    r, _ = rig.run()
    assert "--runner" in r.stderr


@pytest.mark.parametrize(
    "extra",
    [
        {"JOURNAL_RC": "1"},
        {"JOURNAL_LINE": "", "JOURNAL_STDERR": "not found on the host USB bus"},
        {"JOURNAL_LINE": "   "},
        {"JOURNAL_LINE": "-- No entries --"},
        {"JOURNAL_FAIL_AFTER_STOP": "1"},
    ],
)
def test_journal_must_be_readable_and_successful_before_any_clean_claim(rig, extra):
    r, _ = rig.run(rig.driver, **extra)
    assert r.returncode == 1, r.stdout
    assert "journal" in r.stdout.lower()
    assert "OK no SIGKILL" not in r.stdout
    assert "PASS" not in r.stdout


@pytest.mark.parametrize(
    "extra",
    [
        {"FAIL_FIRST_STOP": "1", "IS_ACTIVE": "unknown"},
        {"FAIL_FIRST_STOP": "1", "FAILED_STOP_STATE": "inactive"},
        {"STOP_STATE": "unknown"},
    ],
)
def test_cleanup_retries_stop_until_shutdown_has_been_verified(rig, extra):
    r, calls = rig.run(rig.driver, NO_PROC="1", **extra)
    assert r.returncode == 1, r.stdout
    assert _verbs(calls).count("stop") >= 2
    assert "disable" in _verbs(calls)


@pytest.mark.parametrize("extra", [{"FRAGMENT_PATH": "/some/other/unit"}, {"FRAGMENT_RC": "1"}])
def test_loaded_unit_must_be_the_unit_just_installed(rig, extra):
    r, calls = rig.run(rig.driver, **extra)
    assert r.returncode == 1, r.stdout
    assert "FragmentPath" in r.stdout
    assert "start" not in _verbs(calls)


def test_fail_says_when_the_unit_was_enabled_before_the_check(rig):
    r, _ = rig.run(rig.driver, STOP_STATE="failed", INIT_ENABLED="1")
    assert "was enabled before this check" in r.stdout
    r, _ = rig.run(rig.driver, STOP_STATE="failed")
    assert "was enabled before this check" not in r.stdout


def test_live_leftover_process_after_stop_fails(rig):
    _scanner_on(rig)
    r, _ = rig.run("--live", rig.driver, LEAVE_PROC="1", IS_ACTIVE="active")
    assert r.returncode == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"DROPIN_PATHS": "/u/.config/systemd/user/opticfilm-eject-watch.service.d/override.conf"},
        {"DROPIN_PATHS": "/etc/systemd/user/opticfilm-eject-watch.service.d/override.conf"},
        {"DROPIN_PATHS": "/etc/systemd/user/service.d/../opticfilm-eject-watch.service.d/x.conf"},
        {"EFFECTIVE_EXECSTART": "{ path=/bin/sh ; argv[]=/bin/sh -c exit ; ignore_errors=no }"},
        {
            "EFFECTIVE_EXECSTART": (
                "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
                "-m of135i scan ; ignore_errors=no }"
            ),
            "STUB_RUNNER": "host",
        },
        {"KILL_SIGNAL": "15"},
        {"SEND_SIGKILL": "yes"},
        {"EFFECTIVE_RC": "1"},
    ],
)
def test_effective_unit_must_match_fresh_render_before_start(rig, extra):
    r, calls = rig.run("--enable", rig.driver, **extra)
    assert r.returncode == 1, r.stdout
    assert "start" not in _verbs(calls)
    assert "enable" not in _verbs(calls)
    assert "PASS" not in r.stdout


@pytest.mark.parametrize(
    "dropins",
    [
        "",
        "/usr/lib/systemd/user/service.d/10-timeout-abort.conf",
        (
            "/usr/lib/systemd/user/service.d/10-timeout-abort.conf "
            "/etc/systemd/user/service.d/20-defaults.conf"
        ),
    ],
)
def test_global_service_defaults_allow_matching_effective_unit(rig, dropins):
    r, calls = rig.run(rig.driver, DROPIN_PATHS=dropins)
    assert r.returncode == 0, r.stdout
    assert any("-p DropInPaths -p ExecStart -p KillSignal -p SendSIGKILL" in c for c in calls)


@pytest.mark.parametrize("second_signal", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_cleanup_signal_cannot_skip_retry_after_failed_stop(rig, second_signal):
    p = rig.popen(rig.driver, JOURNAL_LINE="Traceback", FAIL_FIRST_STOP="1", FAILED_STOP_SLEEP="1")
    try:
        for _ in range(200):
            if (rig.tmp / "state" / "stop-failed").exists():
                break
            time.sleep(0.025)
        else:
            pytest.fail("first cleanup stop was never reached")
        p.send_signal(second_signal)
        out, _ = p.communicate(timeout=10)
    finally:
        if p.poll() is None:
            p.kill()
            p.communicate()
    calls = rig.log.read_text().splitlines()
    assert _verbs(calls).count("stop") == 2, out
    assert "disable" in _verbs(calls)
    assert (rig.tmp / "state" / "active").read_text().strip() == "inactive"
    assert not (rig.proc / "4242").exists()
    assert not any("--since -2min" in c for c in calls)
    assert p.returncode == 1


@pytest.mark.parametrize("hang_at", ["import", "proc traversal"])
def test_live_identity_python_probe_is_bounded_and_inconclusive(rig, hang_at):
    _scanner_on(rig)
    _short_timeouts(rig)
    module = rig.tmp / "src" / "opticfilm_linux" / "app" / "eject_watch.py"
    if hang_at == "import":
        extra = "if len(sys.argv) == 4: time.sleep(10)\n"
    else:
        extra = (
            "original_runs_watch = _runs_watch\n"
            "def _runs_watch(entry):\n"
            "    if len(sys.argv) == 4: time.sleep(10)\n"
            "    return original_runs_watch(entry)\n"
        )
    with module.open("a") as f:
        f.write("\nimport sys, time\n" + extra)
    before = time.monotonic()
    r, calls = rig.run("--live", "--enable", rig.driver, LIVE_HOLD_SECS="0")
    assert r.returncode == 5, r.stdout
    assert time.monotonic() - before < 8
    assert "INCONCLUSIVE" in r.stdout and "PASS" not in r.stdout
    assert any(c.startswith("timeout -k 1 3 python3 -I -c") for c in calls)
    assert "stop" in _verbs(calls) and "disable" in _verbs(calls)
    assert "enable" not in _verbs(calls)


@pytest.mark.parametrize("replacement", ["pid", "reused"])
def test_live_container_watcher_identity_cannot_change_behind_stable_mainpid(rig, replacement):
    _scanner_on(rig)
    r, calls = rig.run("--live", "--enable", rig.driver, MAINPID="1", REPLACE_WATCHER=replacement)
    assert r.returncode == 5, r.stdout
    assert "watcher identity changed" in r.stdout
    assert "PASS" not in r.stdout
    assert "enable" not in _verbs(calls)
    assert "stop" in _verbs(calls) and "disable" in _verbs(calls)


def test_live_distrobox_reports_identity_and_ownership_limit(rig):
    _scanner_on(rig)
    r, _ = rig.run("--live", rig.driver, MAINPID="1")
    assert r.returncode == 0, r.stdout
    assert "watcher PID 4242, starttime 100" in r.stdout
    assert "not proven to be the unit's own child" in r.stdout
