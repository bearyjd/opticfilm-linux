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
    scanner_text,
    slide_status_text,
    stage_text,
)
from opticfilm_linux.batch.model import Flag, SlideStatus, TrayStage
from opticfilm_linux.scanner.base import ScannerStatus


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
            "The scanner needs a power cycle: switch it off until its lights go out, "
            "switch it on, eject, load the holder again.",
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
        CAL_FAIL,
        "The scanner refused or lost the scan. Power-cycle it (off until the lights go out)",
        "please power cycle the device",
        "dark_b residual",
        "Calibration failed",
    ],
)
def test_a_driver_refusal_becomes_the_plain_power_cycle_advice(detail):
    assert failure_text(detail, "Tray 3 failed") == POWER_CYCLE_ADVICE
    assert POWER_CYCLE_ADVICE == (
        "The scanner stopped this scan. Power-cycle it (off until the lights go out, then on), "
        "eject, load the holder again and rescan this tray."
    )


@pytest.mark.parametrize("detail", ["", "disk full", "ValueError: bad slide"])
def test_any_other_failure_keeps_the_short_fallback(detail):
    assert failure_text(detail, "Tray 3 failed") == "Tray 3 failed"


def test_the_load_command_defaults_to_the_distrobox_launcher():
    assert load_command(Path("/home/me/opticfilm135i-linux")) == (
        "distrobox enter dev -- bash -lc "
        "'cd /home/me/opticfilm135i-linux && PYTHONPATH=. python3 -m of135i load'"
    )


def test_the_load_command_survives_a_driver_folder_with_spaces():
    command = load_command(Path("/home/me/my driver"))
    inner = shlex.split(command)[-1]
    assert shlex.split(inner)[:2] == ["cd", "/home/me/my driver"]


def test_the_load_command_without_a_driver_folder_names_a_placeholder():
    assert "of135i load" in load_command(None) and "opticfilm135i-linux" in load_command(None)


@pytest.mark.parametrize(
    ("action", "words"), [("eject", "eject the holder"), ("load", "next holder")]
)
def test_a_failed_holder_move_says_to_power_cycle_and_reload(action, words):
    text = holder_failed_text(action)
    assert words in text and "lights go out" in text and "How to load the scanner" in text
