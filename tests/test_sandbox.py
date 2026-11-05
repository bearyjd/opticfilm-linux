"""Flatpak sandbox awareness: detection, the watcher gate and the shown load command."""

import pytest

from opticfilm_linux.app import eject_watch
from opticfilm_linux.app.eject_watch import EjectWatch, run_systemctl
from opticfilm_linux.app.qt.app import load_command_text, load_runner_note
from opticfilm_linux.app.sandbox import HOST_PREFIX, in_flatpak


@pytest.fixture(autouse=True)
def _outside_flatpak(monkeypatch):
    monkeypatch.delenv("OPTICFILM_LOAD_COMMAND", raising=False)
    monkeypatch.delenv("OPTICFILM_DRIVER_RUNNER", raising=False)
    monkeypatch.setattr("opticfilm_linux.app.sandbox.FLATPAK_INFO", "/nonexistent/.flatpak-info")


@pytest.fixture
def sandboxed(monkeypatch, tmp_path):
    info = tmp_path / ".flatpak-info"
    info.write_text("[Application]\n")
    monkeypatch.setattr("opticfilm_linux.app.sandbox.FLATPAK_INFO", str(info))


def test_detects_the_sandbox_by_the_info_file_only(monkeypatch, sandboxed):
    assert in_flatpak() is True


def test_a_leaked_flatpak_id_env_var_is_not_trusted(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "io.github.bearyjd.OpticFilmLinux")
    assert in_flatpak() is False


def _capture(monkeypatch):
    calls = []

    class Done:
        returncode = 0
        stdout = "active\n"

    def fake_run(cmd, **_kw):
        calls.append(list(cmd))
        return Done()

    monkeypatch.setattr(eject_watch.subprocess, "run", fake_run)
    return calls


def test_systemctl_is_plain_outside_flatpak(monkeypatch):
    calls = _capture(monkeypatch)
    run_systemctl(["--user", "stop", "u.service"])
    assert calls == [["systemctl", "--user", "stop", "u.service"]]


def test_systemctl_goes_through_the_host_in_flatpak(monkeypatch, sandboxed):
    calls = _capture(monkeypatch)
    run_systemctl(["--user", "stop", "u.service"])
    assert calls == [[*HOST_PREFIX, "systemctl", "--user", "stop", "u.service"]]
    assert HOST_PREFIX == ("flatpak-spawn", "--host")


def test_stop_and_restore_use_the_injected_runner_unchanged_in_flatpak(monkeypatch, sandboxed):
    seen = []
    states = iter(["active", "inactive"])

    def runner(args):
        seen.append(list(args))
        return 0, next(states, "inactive")

    watch = EjectWatch(runner=runner, process_check=lambda: False, sleep=lambda _s: None)
    watch.yield_to_app()
    assert watch.restore() is None
    assert ["--user", "stop", eject_watch.UNIT] in seen
    assert ["--user", "start", eject_watch.UNIT] in seen


def test_load_command_in_flatpak_is_the_host_terminal_form(monkeypatch, sandboxed):
    monkeypatch.setenv("OPTICFILM_DRIVER_DIR", "/app/share/opticfilm135i-linux")
    text = load_command_text()
    assert "distrobox" not in text
    assert "/app/" not in text
    assert "of135i load" in text
    note = load_runner_note()
    assert "Flatpak" in note and "host terminal" in note


def test_load_command_override_still_wins_in_flatpak(monkeypatch, sandboxed):
    monkeypatch.setenv("OPTICFILM_LOAD_COMMAND", " mine ")
    assert load_command_text() == "mine"
    assert "OPTICFILM_LOAD_COMMAND" in load_runner_note()


def _watch(runner):
    return EjectWatch(runner=runner, process_check=lambda: False, sleep=lambda _s: None)


def _missing(_args):
    raise FileNotFoundError("flatpak-spawn")


@pytest.mark.parametrize("runner", [_missing, lambda _a: (1, "")], ids=["missing", "empty"])
def test_an_unanswerable_host_query_refuses_to_open_the_scanner_in_flatpak(sandboxed, runner):
    with pytest.raises(eject_watch.WatchStillActive, match="Cannot ask the host") as caught:
        _watch(runner).yield_to_app()
    assert "in a host terminal" in str(caught.value)


@pytest.mark.parametrize("runner", [_missing, lambda _a: (1, "")], ids=["missing", "empty"])
def test_an_unanswerable_query_outside_flatpak_still_proceeds(runner):
    _watch(runner).yield_to_app()


def test_an_answered_inactive_host_query_proceeds_in_flatpak(sandboxed):
    _watch(lambda _a: (3, "inactive\n")).yield_to_app()


def test_an_active_host_unit_is_stopped_then_proceeds_in_flatpak(sandboxed):
    states = iter(["active", "inactive"])
    _watch(lambda _a: (0, next(states, "inactive"))).yield_to_app()


def test_stop_failure_messages_say_host_terminal_only_in_flatpak(sandboxed):
    with pytest.raises(eject_watch.WatchStillActive) as caught:
        EjectWatch(
            runner=lambda _a: (0, "active"), process_check=lambda: False, sleep=lambda _s: None
        ).yield_to_app()
    assert "in a host terminal" in str(caught.value)


def test_stop_failure_message_has_no_host_terminal_wording_outside_flatpak():
    with pytest.raises(eject_watch.WatchStillActive) as caught:
        EjectWatch(
            runner=lambda _a: (0, "active"), process_check=lambda: False, sleep=lambda _s: None
        ).yield_to_app()
    assert "host terminal" not in str(caught.value)


def test_restore_failure_message_says_host_terminal_in_flatpak(sandboxed):
    codes = iter([(0, "active"), (0, ""), (0, "inactive"), (1, "")])
    watch = _watch(lambda _a: next(codes))
    watch.yield_to_app()
    assert "in a host terminal" in (watch.restore() or "")
