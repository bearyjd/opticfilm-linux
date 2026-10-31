import shlex
from pathlib import Path

import pytest

from opticfilm_linux.app.messages import (
    POWER_CYCLE_ADVICE,
    bytes_text,
    failure_text,
    flag_text,
    holder_failed_text,
    load_command,
    runner_note,
    scanner_text,
    slide_status_text,
    stage_text,
)
from opticfilm_linux.batch.model import Flag, SlideStatus, TrayStage
from opticfilm_linux.scanner.base import ScannerStatus
from opticfilm_linux.scanner.driver import POWER_CYCLE_HINT


@pytest.mark.parametrize(
    ("flag", "text"),
    [
        (Flag.TOO_DARK, "Very dark"),
        (Flag.TOO_BRIGHT, "Very bright"),
        (Flag.CLIPPING, "Highlights or shadows are cut off"),
        (Flag.DUST, "Dust or scratches detected"),
    ],
)
def test_flag_sentences(flag, text):
    assert flag_text(flag) == text


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (ScannerStatus.UNKNOWN, "Scanner status unknown: scanning is not available yet"),
        (ScannerStatus.READY, "Scanner ready"),
        (ScannerStatus.BUSY, "Scanner busy"),
        (
            ScannerStatus.NOT_READY,
            'Scanner is not loaded. Open "How to load the scanner", then press Scan.',
        ),
        (
            ScannerStatus.NOT_CONNECTED,
            "Scanner not found. Switch it on and check the USB cable.",
        ),
        (
            ScannerStatus.ERROR,
            "The scanner reports a problem. If a scan just failed or stopped, power-cycle it "
            "(off until the lights go out, then on), eject and load the holder again. If the app "
            'says the scanner driver was not found, see "How to load the scanner" / the README '
            "(OPTICFILM_DRIVER_DIR).",
        ),
    ],
)
def test_scanner_sentences(status, text):
    assert scanner_text(status) == text


def test_every_stage_has_words():
    assert {stage_text(s) for s in TrayStage} == {
        "Scanning",
        "Waiting to enhance",
        "Enhancing",
        "Ready to review",
        "Exported",
        "Failed",
    }


def test_slide_status_words_cover_every_status():
    assert {slide_status_text(s) for s in SlideStatus} == {
        "Pending",
        "Needs a look",
        "Accepted",
        "Empty window",
    }


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 bytes"),
        (999, "999 bytes"),
        (1500, "1.5 KB"),
        (2_500_000, "2.5 MB"),
        (1_300_000_000, "1.3 GB"),
    ],
)
def test_bytes_text(size, text):
    assert bytes_text(size) == text


CAL_FAIL = (
    "ERROR of135i: hardware session FAILED in operation scan, phase cal_dark_b: CalibrationError: "
    "dark_b is residual and no healthy dark_b has been measured this session. "
    "Power the scanner OFF, wait until its lights go out, then power it ON again."
)


@pytest.mark.parametrize(
    "detail",
    [
        f"ScanError: {POWER_CYCLE_HINT} Driver said: {CAL_FAIL}",
        f"the driver did not finish within 3600 s. {POWER_CYCLE_HINT} Log: /x.log",
    ],
)
def test_a_driver_refusal_becomes_the_plain_power_cycle_advice(detail):
    assert failure_text(detail, "Tray 3 failed") == POWER_CYCLE_ADVICE
    assert POWER_CYCLE_ADVICE == (
        "The scanner stopped this scan. Power-cycle it (off until the lights go out, then on), "
        "eject, load the holder again and rescan this tray."
    )


@pytest.mark.parametrize(
    "detail",
    [
        "",
        "disk full",
        "ValueError: bad slide",
        CAL_FAIL,  # the driver's own words, without the backend's hint: not matched loosely
        "OSError: [Errno 5] Input/output error: '/photos/calibration/x.tif'",
        "dark_b.npz missing; please power cycle later",
    ],
)
def test_any_other_failure_keeps_the_short_fallback(detail):
    assert failure_text(detail, "Tray 3 failed") == "Tray 3 failed"


def test_the_load_command_defaults_to_the_distrobox_launcher():
    assert load_command(Path("/home/me/opticfilm135i-linux")) == (
        "distrobox enter dev -- bash -lc "
        "'cd -- /home/me/opticfilm135i-linux && PYTHONPATH=. python3 -m of135i load'"
    )


def test_the_load_command_survives_a_driver_folder_with_spaces():
    command = load_command(Path("/home/me/my driver"))
    inner = shlex.split(command)[-1]
    assert shlex.split(inner)[:3] == ["cd", "--", "/home/me/my driver"]


def test_the_load_command_without_a_driver_folder_names_a_placeholder():
    assert "of135i load" in load_command(None) and "opticfilm135i-linux" in load_command(None)


@pytest.mark.parametrize(
    ("action", "words"), [("eject", "eject the holder"), ("load", "next holder")]
)
def test_a_failed_holder_move_says_to_power_cycle_and_reload(action, words):
    text = holder_failed_text(action)
    assert words in text and "lights go out" in text and "How to load the scanner" in text


@pytest.mark.parametrize("folder", ["-", "-x y"])
def test_the_load_command_cannot_mistake_a_dash_folder_for_an_option(folder):
    inner = shlex.split(shlex.split(load_command(Path(folder)))[-1])
    assert inner[:3] == ["cd", "--", folder]


_DIR = Path("/home/me/driver")
_DEFAULT = (
    "distrobox enter dev -- bash -lc 'cd -- /home/me/driver && PYTHONPATH=. python3 -m of135i load'"
)


def test_the_host_runner_runs_the_driver_directly():
    assert load_command(_DIR, runner="host") == (
        "bash -lc 'cd -- /home/me/driver && PYTHONPATH=. python3 -m of135i load'"
    )


def test_a_named_distrobox_runner_uses_that_container():
    assert load_command(_DIR, runner="distrobox:photo-box.1") == _DEFAULT.replace(
        "dev", "photo-box.1", 1
    )


def test_an_empty_runner_is_the_default():
    assert load_command(_DIR, runner="") == _DEFAULT


@pytest.mark.parametrize(
    "runner",
    [
        "docker",
        "distrobox:",
        "distrobox:a b",
        "distrobox:x;rm -rf ~",
        "distrobox:$(id)",
        "Host",
        "distrobox:-h",
        "distrobox:--root",
        "distrobox:.",
        "distrobox:..",
    ],
)
def test_an_unknown_runner_or_unsafe_name_falls_back_to_the_default_and_logs(runner, caplog):
    with caplog.at_level("WARNING"):
        assert load_command(_DIR, runner=runner) == _DEFAULT
    assert "OPTICFILM_DRIVER_RUNNER" in caplog.text


def test_the_runner_is_read_from_the_environment_when_not_given(monkeypatch):
    monkeypatch.setenv("OPTICFILM_DRIVER_RUNNER", "host")
    assert load_command(_DIR).startswith("bash -lc ")


def test_the_note_says_which_runner_and_how_to_change_it():
    note = runner_note(runner=None, which=lambda _name: "/usr/bin/distrobox")
    assert 'container "dev"' in note and "OPTICFILM_DRIVER_RUNNER" in note
    assert "directly on this computer" in runner_note(runner="host", which=lambda _n: None)
    assert 'container "box"' in runner_note(runner="distrobox:box", which=lambda _n: "/x")


def test_the_note_warns_when_distrobox_is_missing():
    note = runner_note(runner="distrobox:dev", which=lambda _name: None)
    assert (
        "distrobox command was not found" in note
        and "install" in note
        and "OPTICFILM_DRIVER_RUNNER=host" in note
    )


def test_the_host_runner_never_warns_about_distrobox():
    assert "not found" not in runner_note(runner="host", which=lambda _name: None)


@pytest.fixture(autouse=True)
def _no_runner_env(monkeypatch):
    monkeypatch.delenv("OPTICFILM_DRIVER_RUNNER", raising=False)


def test_surrounding_whitespace_is_ignored():
    assert load_command(_DIR, runner="host ").startswith("bash -lc ")
    assert load_command(_DIR, runner="distrobox:x\n") == _DEFAULT.replace("dev", "x", 1)


def test_the_note_warns_when_only_distrobox_enter_is_present():
    only_enter = {"distrobox-enter": "/usr/bin/distrobox-enter"}.get
    assert "not found" in runner_note(runner="distrobox:dev", which=only_enter)


def test_the_note_is_quiet_when_distrobox_is_present():
    assert "not found" not in runner_note(runner="distrobox:dev", which=lambda _n: "/x")


def test_a_rejected_runner_is_named_in_the_note_not_presented_as_chosen():
    note = runner_note(runner="docker", which=lambda _n: "/x")
    assert 'OPTICFILM_DRIVER_RUNNER="docker" was not understood; using "dev"' in note


def test_a_rejected_runner_is_shortened_and_cleaned_for_display():
    note = runner_note(runner="distrobox:" + "a b\n\x1b[31m" * 30, which=lambda _n: "/x")
    assert "\n" not in note and "\x1b" not in note
    assert len(note) < 400


def test_a_rejected_runner_is_logged_once_for_command_and_note(caplog):
    with caplog.at_level("WARNING"):
        load_command(_DIR, runner="docker")
        runner_note(runner="docker", which=lambda _n: "/x")
    assert caplog.text.count("OPTICFILM_DRIVER_RUNNER") == 1
