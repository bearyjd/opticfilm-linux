"""The Eject-button watcher unit and its scripts. Offline: no systemd, no distrobox, no scanner.

distrobox-enter joins its arguments into one string and evals it, so everything after ``--`` in
the unit must be a plain word; the logic lives in scripts that run inside the container.
"""

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SYSTEMD = ROOT / "packaging" / "systemd"
UNIT = SYSTEMD / "opticfilm-eject-watch.service"
RUN = SYSTEMD / "eject-watch-run.sh"
STOP = SYSTEMD / "eject-watch-stop.sh"
INSTALL = ROOT / "scripts" / "install-eject-watch.sh"
PLAIN_WORD = re.compile(r"^[A-Za-z0-9_@./-]+$")
STOP_PATTERN = "[o]f135i( .*)? watch( |$)"


def _exec_lines() -> dict[str, list[str]]:
    lines = [x for x in UNIT.read_text().splitlines() if x.startswith(("ExecStart=", "ExecStop="))]
    return {x.split("=", 1)[0]: x.split("=", 1)[1].split() for x in lines}


def _script(path: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
    return subprocess.run(
        [str(path), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=cwd,
        check=False,
        timeout=60,
    )


# -- the unit ------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["ExecStart", "ExecStop"])
def test_every_word_after_the_distrobox_separator_is_a_plain_path(key):
    words = _exec_lines()[key]
    assert "--" in words
    after = words[words.index("--") + 1 :]
    assert len(after) >= 1
    for word in after:
        assert PLAIN_WORD.match(word), f"{key}: {word!r} would be mangled by distrobox-enter"


def test_the_unit_runs_the_two_scripts_and_never_kills():
    lines = _exec_lines()
    assert lines["ExecStart"][-2].endswith("/eject-watch-run.sh")
    assert lines["ExecStop"][-1].endswith("/eject-watch-stop.sh")
    text = UNIT.read_text()
    for needed in ("KillSignal=SIGINT", "SendSIGKILL=no", "KillMode=process", "TimeoutStopSec=120"):
        assert needed in text


def test_the_plain_word_check_rejects_shell_syntax():
    for bad in ["'a'", '"a"', "$1", "a;b", "(a)", "a|b", "a&b", "a>b", "`a`", "a\\b", "a b"]:
        assert not PLAIN_WORD.match(bad), bad


@pytest.mark.parametrize("path", [RUN, STOP, INSTALL])
def test_scripts_are_executable_and_parse(path):
    assert path.stat().st_mode & stat.S_IXUSR
    assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
@pytest.mark.parametrize("path", [RUN, STOP, INSTALL])
def test_scripts_pass_shellcheck(path):
    assert subprocess.run(["shellcheck", str(path)], check=False).returncode == 0


# -- the stop script -----------------------------------------------------------------------


def _fake_tools(tmp_path: Path) -> tuple[Path, Path]:
    """``pkill`` and ``pgrep`` stand-ins. The log records every call; the counter polls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "pkill").write_text(
        f'#!/bin/bash\necho "pkill $*" >> "{log}"\nexit "${{FAKE_PKILL_RC:-0}}"\n'
    )
    (bin_dir / "pgrep").write_text(
        "#!/bin/bash\n"
        f'echo "pgrep $*" >> "{log}"\n'
        f'n=$(grep -c "^pgrep" "{log}")\n'
        '[ "$n" -le "${FAKE_PGREP_BUSY_POLLS:-0}" ]\n'
    )
    (bin_dir / "kill").write_text(f'#!/bin/bash\necho "kill $*" >> "{log}"\n')
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    return bin_dir, log


def _stop(tmp_path: Path, **env: str):
    bin_dir, log = _fake_tools(tmp_path)
    run_env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "EJECT_WATCH_STOP_LOOPS": "3",
        "EJECT_WATCH_STOP_DELAY": "0",
        **env,
    }
    done = subprocess.run(
        [str(STOP)], capture_output=True, text=True, env=run_env, check=False, timeout=60
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return done, calls


def test_stop_with_nothing_running_exits_zero_without_polling(tmp_path):
    done, calls = _stop(tmp_path, FAKE_PKILL_RC="1")
    assert done.returncode == 0
    assert calls == [f"pkill -INT -f {STOP_PATTERN}"]


def test_stop_returns_once_the_process_has_exited(tmp_path):
    done, calls = _stop(tmp_path, FAKE_PKILL_RC="0", FAKE_PGREP_BUSY_POLLS="2")
    assert done.returncode == 0
    assert sum(c.startswith("pgrep") for c in calls) == 3


def test_stop_fails_after_the_wait_if_it_never_exits_and_never_kills(tmp_path):
    done, calls = _stop(tmp_path, FAKE_PKILL_RC="0", FAKE_PGREP_BUSY_POLLS="999")
    assert done.returncode == 1
    assert "not killed" in done.stderr
    assert [c for c in calls if c.startswith("pkill")] == [f"pkill -INT -f {STOP_PATTERN}"]
    assert not [c for c in calls if c.startswith("kill")]


def test_stop_script_text_has_no_kill_signal():
    code = "\n".join(x for x in STOP.read_text().splitlines() if not x.lstrip().startswith("#"))
    assert not re.search(r"-9\b|-KILL|SIGKILL|\bkill\b(?!all)", code.replace("pkill", ""))


def test_stop_pattern_matches_the_watcher_but_not_itself():
    pattern = re.compile(STOP_PATTERN)
    assert pattern.search("python3 -m of135i watch")
    assert pattern.search("python3 -m of135i --verbose watch")
    assert not pattern.search("python3 -m of135i watchdog")
    assert not pattern.search("python3 -m of135i load")
    assert not pattern.search("/bin/bash " + str(STOP))
    assert not pattern.search(STOP_PATTERN)  # the pattern text itself


# -- the run script ------------------------------------------------------------------------


def _fake_driver(tmp_path: Path) -> Path:
    driver = tmp_path / "driver"
    (driver / "of135i").mkdir(parents=True)
    (driver / "of135i" / "__init__.py").write_text("")
    (driver / "of135i" / "__main__.py").write_text(
        "import os, sys\nprint('watch-started', os.getcwd(), sys.argv[1:])\n"
    )
    return driver


def test_run_script_starts_watch_in_the_driver_folder(tmp_path):
    driver = _fake_driver(tmp_path)
    done = _script(RUN, str(driver), cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert f"watch-started {driver.resolve()} ['watch']" in done.stdout


def test_run_script_without_a_folder_says_so(tmp_path):
    for args in ([], [str(tmp_path / "nope")]):
        done = _script(RUN, *args)
        assert done.returncode == 2
        assert "not found" in done.stderr


# -- the installer -------------------------------------------------------------------------


_TOOLS = (
    "dirname",
    "grep",
    "mkdir",
    "mktemp",
    "install",
    "cmp",
    "diff",
    "rm",
    "bash",
    "env",
    "timeout",
    "realpath",
)


def _install(
    tmp_path: Path,
    driver: str | None,
    *,
    script=INSTALL,
    env=None,
    runner: str | None = "distrobox",
    pyusb: bool | str | None = None,
    distrobox_enter: bool = False,
    path_prefix: str = "",
):
    """Run the installer with a PATH of stubs and symlinks to the few real tools it needs, so the
    presence of python3/pyusb/distrobox-enter is decided by the test, not by this machine."""
    bin_dir = tmp_path / "ibin"
    tools = tmp_path / "itools"
    bin_dir.mkdir(exist_ok=True)
    tools.mkdir(exist_ok=True)
    for name in _TOOLS:
        if not (tools / name).exists():
            (tools / name).symlink_to(shutil.which(name) or "/bin/false")
    log = tmp_path / "systemctl.log"
    stub = bin_dir / "systemctl"
    stub.write_text(
        "#!/bin/bash\n"
        'if [[ "$2" == is-active ]]; then echo "${STUB_ACTIVE:-inactive}"; exit 0; fi\n'
        f'echo "$*" >> "{log}"\n'
    )
    stub.chmod(0o755)
    if pyusb is not None:
        # python3 is present. `pyusb` True/False: `import usb` works or not. A string: it works
        # only when PYTHONPATH is exactly that string (a probe that is not run like the unit fails).
        # The app's own helper (`-I`) is passed through to the real interpreter.
        cond = (
            "true"
            if pyusb is True
            else "false"
            if pyusb is False
            else f'[[ "${{PYTHONPATH:-}}" == {pyusb} && -z "${{PYTHONSTARTUP:-}}" ]]'
        )
        py = bin_dir / "python3"
        py.write_text(
            f'#!/bin/bash\nif [[ "$1" == -I ]]; then exec {sys.executable} "$@"; fi\n{cond}\n'
        )
        py.chmod(0o755)
    if distrobox_enter:
        (bin_dir / "distrobox-enter").write_text("#!/bin/bash\n")
        (bin_dir / "distrobox-enter").chmod(0o755)
    units = tmp_path / "units"
    proc_root = tmp_path / "iproc"  # no real process of this machine may influence the tests
    proc_root.mkdir(exist_ok=True)
    run_env = {k: v for k, v in os.environ.items() if k != "OPTICFILM_DRIVER_DIR"}
    run_env.update(
        PATH=f"{path_prefix}{bin_dir}:{tools}",
        EJECT_WATCH_UNIT_DIR=str(units),
        HOME=str(tmp_path),
        EJECT_WATCH_CHECK_TESTING="1",
        PROC_ROOT=str(proc_root),
    )
    run_env.update(env or {})
    args = ([] if runner is None else ["--runner", runner]) + ([] if driver is None else [driver])
    done = subprocess.run(
        [str(script), *args], capture_output=True, text=True, env=run_env, check=False, timeout=60
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return done, units / "opticfilm-eject-watch.service", calls


def test_installer_renders_the_unit_and_only_reloads_systemd(tmp_path):
    driver = tmp_path / "drv"
    driver.mkdir()
    done, unit, calls = _install(tmp_path, str(driver))
    assert done.returncode == 0, done.stderr
    text = unit.read_text()
    assert not re.search(r"@[A-Z_]+@", text)
    start = _exec_lines_of(text)["ExecStart"]
    assert start[-2:] == [f"{SYSTEMD}/eject-watch-run.sh", str(driver)]
    assert _exec_lines_of(text)["ExecStop"][-1] == f"{SYSTEMD}/eject-watch-stop.sh"
    assert calls == ["--user daemon-reload"]
    assert "not enabled or started" in done.stdout


def _exec_lines_of(text: str) -> dict[str, list[str]]:
    lines = [x for x in text.splitlines() if x.startswith(("ExecStart=", "ExecStop="))]
    return {x.split("=", 1)[0]: x.split("=", 1)[1].split() for x in lines}


def test_installer_ignores_unit_dir_override_in_production(tmp_path):
    driver = tmp_path / "drv"
    driver.mkdir()
    config = tmp_path / "config"
    done, overridden_unit, _ = _install(
        tmp_path,
        str(driver),
        env={"EJECT_WATCH_CHECK_TESTING": "", "XDG_CONFIG_HOME": str(config)},
    )
    assert done.returncode == 0, done.stderr
    assert not overridden_unit.exists()
    assert (config / "systemd" / "user" / "opticfilm-eject-watch.service").is_file()


@pytest.mark.parametrize("runner", ["host", "distrobox"])
def test_installer_resolves_driver_symlink_before_rendering(tmp_path, runner):
    target = tmp_path / "real-driver"
    target.mkdir()
    link = tmp_path / "driver-link"
    link.symlink_to(target, target_is_directory=True)
    done, unit, _ = _install(tmp_path, str(link), runner=runner, pyusb=True)
    assert done.returncode == 0, done.stderr
    assert str(link) not in unit.read_text()
    assert str(target.resolve()) in unit.read_text()


@pytest.mark.parametrize("runner", ["host", "distrobox"])
def test_installer_rejects_hostile_resolved_driver_symlink_target(tmp_path, runner):
    target = tmp_path / "bad target"
    target.mkdir()
    link = tmp_path / "driver-link"
    link.symlink_to(target, target_is_directory=True)
    done, unit, calls = _install(tmp_path, str(link), runner=runner, pyusb=True)
    assert done.returncode != 0, done.stdout
    assert "not allowed" in done.stderr
    assert not unit.exists() and calls == []


def test_installer_takes_the_driver_folder_from_the_environment(tmp_path):
    driver = tmp_path / "envdrv"
    driver.mkdir()
    done, unit, _ = _install(tmp_path, None, env={"OPTICFILM_DRIVER_DIR": str(driver)})
    assert done.returncode == 0, done.stderr
    assert str(driver) in unit.read_text()


def test_installer_shows_what_it_replaces(tmp_path):
    driver = tmp_path / "drv"
    driver.mkdir()
    _install(tmp_path, str(driver))
    other = tmp_path / "drv2"
    other.mkdir()
    done, unit, _ = _install(tmp_path, str(other))
    assert "replacing" in done.stdout and "drv2" in done.stdout
    assert str(other) in unit.read_text()


HOSTILE = ["with space", "dollar$x", "quo'te", 'dq"uote', "per%cent", "new\nline", "semi;colon"]


@pytest.mark.parametrize("name", HOSTILE)
def test_installer_rejects_a_hostile_driver_folder_and_writes_nothing(tmp_path, name):
    driver = tmp_path / name
    driver.mkdir()
    done, unit, calls = _install(tmp_path, str(driver))
    assert done.returncode != 0
    assert "not allowed" in done.stderr
    assert not unit.exists() and calls == []


@pytest.mark.parametrize("bad", ["relative/dir", "/nonexistent/for/sure", ""])
def test_installer_rejects_missing_relative_or_empty_folders(tmp_path, bad):
    done, unit, calls = _install(tmp_path, bad or None)
    assert done.returncode != 0 and not unit.exists() and calls == []


def test_installer_rejects_a_hostile_script_folder(tmp_path):
    home = tmp_path / "bad dir"
    (home / "scripts").mkdir(parents=True)
    shutil.copytree(SYSTEMD, home / "packaging" / "systemd")
    shutil.copy(INSTALL, home / "scripts" / INSTALL.name)
    driver = tmp_path / "drv"
    driver.mkdir()
    done, unit, calls = _install(tmp_path, str(driver), script=home / "scripts" / INSTALL.name)
    assert done.returncode != 0 and "not allowed" in done.stderr
    assert not unit.exists() and calls == []


def _locales() -> list[str]:
    have = subprocess.run(["locale", "-a"], capture_output=True, text=True, check=False).stdout
    names = {x.strip().lower().replace("utf-8", "utf8") for x in have.splitlines()}
    wanted = ("en_US.UTF-8", "C.UTF-8")
    return [loc for loc in wanted if loc.lower().replace("utf-8", "utf8") in names]


@pytest.mark.skipif(not _locales(), reason="no UTF-8 locale installed")
@pytest.mark.parametrize("locale", _locales())
@pytest.mark.parametrize("name", ["Zé", "cyrillicа", "full／width", "dotlessı"])
def test_installer_rejects_non_ascii_paths_under_a_utf8_locale(tmp_path, locale, name):
    driver = tmp_path / name
    driver.mkdir()
    done, unit, calls = _install(tmp_path, str(driver), env={"LC_ALL": locale})
    assert done.returncode != 0 and "not allowed" in done.stderr
    assert not unit.exists() and calls == []


def test_the_rendered_unit_is_readable_by_everyone_who_can_read_the_folder(tmp_path):
    driver = tmp_path / "drv"
    driver.mkdir()
    done, unit, _ = _install(tmp_path, str(driver))
    assert done.returncode == 0, done.stderr
    assert stat.S_IMODE(unit.stat().st_mode) == 0o644
