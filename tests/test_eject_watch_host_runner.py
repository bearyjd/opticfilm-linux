"""The host runner of the Eject-button watcher unit (no container) and the installer's runner
selection. Offline: stub python3, systemctl and distrobox-enter; no systemd, no scanner."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_eject_watch_unit_scripts import HOSTILE, INSTALL, PLAIN_WORD, SYSTEMD, UNIT, _install

HOST_UNIT = SYSTEMD / "opticfilm-eject-watch-host.service"


def _host_install(tmp_path: Path, **kw):
    driver = tmp_path / "drv"
    driver.mkdir(exist_ok=True)
    kw.setdefault("runner", "host")
    kw.setdefault("pyusb", True)
    done, unit, calls = _install(tmp_path, str(driver), **kw)
    return done, unit, calls, driver


def _lines(text: str, key: str) -> list[str]:
    return [x.split("=", 1)[1] for x in text.splitlines() if x.startswith(key + "=")]


def _fake_python(directory: Path) -> None:
    directory.mkdir()
    py = directory / "python3"
    py.write_text("#!/bin/bash\nexit 0\n")
    py.chmod(0o755)


def test_both_templates_use_only_plain_words_in_every_exec_line():
    for path in (UNIT, HOST_UNIT):
        for line in path.read_text().splitlines():
            if line.startswith(("ExecStart=", "ExecStop=")):
                words = line.split("=", 1)[1].split()
                after = words[words.index("--") + 1 :] if "--" in words else words
                assert after
                assert all(PLAIN_WORD.match(w) for w in after), (path.name, line)


def test_host_template_never_kills_and_needs_no_exec_stop():
    text = HOST_UNIT.read_text()
    assert "ExecStop=" not in text
    for needed in (
        "KillSignal=SIGINT",
        "KillMode=process",
        "SendSIGKILL=no",
        "TimeoutStopSec=120",
        "StartLimitIntervalSec=0",
        "Restart=always",
        "RestartSec=30",
        "RestartSteps=3",
        "RestartMaxDelaySec=120",
    ):
        assert needed in text.splitlines()  # a whole setting line, not a mention in a comment
    settings = [x for x in text.splitlines() if x and not x.startswith("#")]
    assert not [x for x in settings if "SIGKILL" in x and x != "SendSIGKILL=no"]


def test_host_installer_renders_the_host_unit(tmp_path):
    done, unit, calls, driver = _host_install(tmp_path)
    assert done.returncode == 0, done.stderr
    text = unit.read_text()
    assert not re.search(r"@[A-Z_]+@", text)
    py = str(tmp_path / "ibin" / "python3")
    assert _lines(text, "ExecStart") == [f"{py} -m of135i watch"]
    assert _lines(text, "WorkingDirectory") == [str(driver)]
    assert _lines(text, "Environment") == [f"PYTHONPATH={driver}"]
    assert "ExecStop=" not in text and "distrobox-enter" not in text
    assert calls == ["--user daemon-reload"]
    assert "runner: host" in done.stdout and "not enabled or started" in done.stdout


def test_overlay_goes_into_pythonpath_only_when_given(tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    done, unit, _, driver = _host_install(tmp_path, env={"OF135I_DEPS_OVERLAY": str(overlay)})
    assert done.returncode == 0, done.stderr
    assert _lines(unit.read_text(), "Environment") == [f"PYTHONPATH={driver}:{overlay}"]


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_overlay_is_rejected(tmp_path, name):
    overlay = tmp_path / name
    overlay.mkdir()
    done, unit, calls, _ = _host_install(tmp_path, env={"OF135I_DEPS_OVERLAY": str(overlay)})
    assert done.returncode != 0 and "not allowed" in done.stderr
    assert not unit.exists() and calls == []


def test_an_unknown_placeholder_in_a_template_is_refused(tmp_path):
    home = tmp_path / "copy"
    (home / "scripts").mkdir(parents=True)
    shutil.copytree(SYSTEMD, home / "packaging" / "systemd")
    shutil.copy(INSTALL, home / "scripts" / INSTALL.name)
    template = home / "packaging" / "systemd" / HOST_UNIT.name
    template.write_text(template.read_text() + "# @NOT_FILLED@\n")
    done, unit, calls, _ = _host_install(tmp_path, script=home / "scripts" / INSTALL.name)
    assert done.returncode != 0 and "placeholder was left" in done.stderr
    assert not unit.exists() and calls == []


def test_hostile_python_path_is_rejected(tmp_path):
    odd = tmp_path / "bad dir"
    _fake_python(odd)
    done, unit, calls, _ = _host_install(tmp_path, pyusb=None, path_prefix=f"{odd}:")
    assert done.returncode != 0 and "not allowed" in done.stderr
    assert not unit.exists() and calls == []


def test_relative_python_path_is_rejected(tmp_path):
    _fake_python(tmp_path / "rel")
    done, unit, calls, _ = _host_install(tmp_path, pyusb=None, path_prefix="rel:")
    assert done.returncode != 0
    assert not unit.exists() and calls == []


def test_host_without_pyusb_is_refused_with_a_hint(tmp_path):
    done, unit, calls, _ = _host_install(tmp_path, pyusb=False)
    assert done.returncode != 0
    assert "cannot import pyusb" in done.stderr and "dnf install python3-pyusb" in done.stderr
    assert not unit.exists() and calls == []


def test_host_without_python3_is_refused(tmp_path):
    done, unit, _, _ = _host_install(tmp_path, pyusb=None)
    assert done.returncode != 0 and "python3 was not found" in done.stderr
    assert not unit.exists()


@pytest.mark.parametrize(
    ("pyusb", "distrobox", "expect"),
    [
        (True, True, "runner: host"),
        (True, False, "runner: host"),
        (False, True, "runner: distrobox:dev"),
        (None, True, "runner: distrobox:dev"),
    ],
)
def test_auto_picks_host_when_pyusb_imports_else_distrobox(tmp_path, pyusb, distrobox, expect):
    done, unit, _, _ = _host_install(
        tmp_path, runner="auto", pyusb=pyusb, distrobox_enter=distrobox
    )
    assert done.returncode == 0, done.stderr
    assert expect in done.stdout
    assert ("distrobox-enter" in unit.read_text()) == (expect != "runner: host")


@pytest.mark.parametrize("pyusb", [False, None])
def test_auto_with_neither_pyusb_nor_distrobox_is_a_clear_error(tmp_path, pyusb):
    done, unit, calls, _ = _host_install(tmp_path, runner="auto", pyusb=pyusb)
    assert done.returncode != 0
    assert "no way to run the watcher" in done.stderr and "pyusb" in done.stderr
    assert not unit.exists() and calls == []


def test_runner_defaults_to_auto(tmp_path):
    done, _, _, _ = _host_install(tmp_path, runner=None)
    assert done.returncode == 0 and "runner: host" in done.stdout


def test_distrobox_runner_names_the_container(tmp_path):
    done, unit, _, _ = _host_install(tmp_path, runner="distrobox:my-box.1")
    assert done.returncode == 0, done.stderr
    assert "-n my-box.1 --" in unit.read_text()
    assert "runner: distrobox:my-box.1" in done.stdout


@pytest.mark.parametrize(
    "runner", ["distrobox:", "distrobox:-x", "distrobox:a b", "distrobox:a;b", "docker", "HOST"]
)
def test_bad_runner_values_are_rejected(tmp_path, runner):
    done, unit, calls, _ = _host_install(tmp_path, runner=runner)
    assert done.returncode != 0
    assert not unit.exists() and calls == []


def test_probe_runs_python_like_the_unit_not_with_the_callers_environment(tmp_path):
    drv = str(tmp_path / "drv")
    kw = {
        "runner": "auto",
        "pyusb": drv,
        "env": {"PYTHONPATH": "/caller/fake", "PYTHONSTARTUP": "/caller/startup.py"},
    }
    done, unit, _, _ = _host_install(tmp_path, **kw)
    assert done.returncode == 0, done.stderr
    assert "runner: host" in done.stdout
    assert f"ExecStart={tmp_path}/ibin/python3" in unit.read_text()


def test_probe_includes_the_overlay(tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    exact = f"{tmp_path / 'drv'}:{overlay}"
    for runner in ("auto", "host"):
        kw = {"runner": runner, "pyusb": exact, "distrobox_enter": True}
        kw["env"] = {"OF135I_DEPS_OVERLAY": str(overlay)}
        done, _, _, _ = _host_install(tmp_path, **kw)
        assert done.returncode == 0, (runner, done.stderr)
        assert "runner: host" in done.stdout


def test_host_runner_refused_when_the_probe_fails_in_the_units_environment(tmp_path):
    done, unit, _, _ = _host_install(tmp_path, pyusb="/never")
    assert done.returncode != 0 and "cannot import pyusb" in done.stderr
    assert not unit.exists()


def test_installer_prints_what_the_check_script_needs_to_reprobe(tmp_path):
    done, _, _, driver = _host_install(tmp_path)
    assert f"host-python: {tmp_path}/ibin/python3" in done.stdout
    assert f"host-pythonpath: {driver}" in done.stdout


def _install_twice(tmp_path, *, second_runner, second_env, first_runner="distrobox"):
    first, unit, _, _ = _host_install(tmp_path, runner=first_runner)
    assert first.returncode == 0, first.stderr
    before = unit.read_text()
    second, _, calls, _ = _host_install(tmp_path, runner=second_runner, env=second_env)
    return second, unit, before, calls


@pytest.mark.parametrize("state", ["active", "activating", "deactivating", "reloading"])
def test_switching_runner_over_a_running_unit_is_refused(tmp_path, state):
    done, unit, before, calls = _install_twice(
        tmp_path, second_runner="host", second_env={"STUB_ACTIVE": state}
    )
    assert done.returncode != 0
    assert "systemctl --user stop opticfilm-eject-watch.service" in done.stderr
    assert unit.read_text() == before
    assert calls.count("--user daemon-reload") == 1  # only the first install's


def test_reinstalling_the_identical_unit_over_a_running_one_is_fine(tmp_path):
    done, _, _, _ = _install_twice(
        tmp_path,
        second_runner="distrobox",
        second_env={"STUB_ACTIVE": "active"},
    )
    assert done.returncode == 0, done.stderr
    assert "already up to date" in done.stdout


def _decoy_proc(tmp_path: Path, *argv: str) -> dict[str, str]:
    root = tmp_path / "decoy_proc"
    (root / "77").mkdir(parents=True)
    (root / "77" / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    return {"PROC_ROOT": str(root)}


def test_a_leftover_watch_process_blocks_a_changed_unit(tmp_path):
    env = _decoy_proc(tmp_path, "python3", "-m", "of135i", "watch")
    done, unit, before, _ = _install_twice(tmp_path, second_runner="host", second_env=env)
    assert done.returncode != 0
    assert "of135i watch" in done.stderr and "kill -INT" in done.stderr
    assert unit.read_text() == before


def test_an_unrelated_process_does_not_block(tmp_path):
    env = _decoy_proc(tmp_path, "python3", "-m", "of135i", "watchdog")
    done, _, _, _ = _install_twice(tmp_path, second_runner="host", second_env=env)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("runner", ["host", "distrobox"])
def test_rendered_units_pass_systemd_analyze_verify(tmp_path, runner):
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze not available")
    done, unit, _, _ = _host_install(tmp_path, runner=runner)
    assert done.returncode == 0, done.stderr
    out = subprocess.run(
        ["systemd-analyze", "--user", "verify", str(unit)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert "Unknown key" not in out.stderr and "Unknown section" not in out.stderr, out.stderr
