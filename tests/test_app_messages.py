import pytest

from opticfilm_linux.app.messages import (
    bytes_text,
    flag_text,
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
        (ScannerStatus.NOT_READY, "Scanner not ready: load a tray"),
        (ScannerStatus.NOT_CONNECTED, "Scanner not connected"),
        (ScannerStatus.ERROR, "Scanner error"),
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
