"""Review edits reach the command queue before actions that may export or delete."""

import os
from unittest.mock import Mock, call

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from opticfilm_linux.app.prefs import MemoryPrefs  # noqa: E402
from opticfilm_linux.app.qt.bridge import Bridge  # noqa: E402


@pytest.mark.parametrize(
    ("slot", "args", "method"),
    [
        ("poll", (), "tick"),
        ("setAccepted", (1, 1, True), "set_status"),
        ("markEmpty", (1, 1), "set_status"),
        ("rotate", (1, 1, 1), "rotate"),
        ("mirror", (1, 1), "mirror"),
        ("resetSlide", (1, 2), "reset_slide"),
        ("setBatchOrientation", (90, False), "set_batch_orientation"),
        ("acceptAll", (), "accept_all"),
        (
            "confirmEmptyFrames",
            (1, 2, [{"position": 1, "fingerprint": "abc"}]),
            "confirm_empty_frames",
        ),
        ("requestFreeSpace", (), "request_free_space"),
        ("confirmFreeSpace", (), "confirm_free_space"),
        ("confirmFreeHeld", (1,), "confirm_free_held"),
        ("startExport", ("/tmp/out", "jpeg"), "export"),
        ("reexportAllAccepted", (), "reexport_all_accepted"),
        ("recoverInterruptedCleanup", (1,), "recover_interrupted_cleanup"),
        ("finishBox", (True,), "finish_box"),
        ("scan", (), "scan"),
        ("rescan", (1,), "rescan"),
    ],
)
def test_pending_edit_is_submitted_before_action(slot, args, method):
    app = QApplication.instance() or QApplication(["test"])
    controller = Mock()
    controller.tick.return_value = False
    bridge = Bridge(controller, MemoryPrefs())
    bridge.adjust(1, 1, "brightness", 0.5)
    getattr(bridge, slot)(*args)
    assert controller.method_calls[0] == call.adjust_slide(1, 1, {"brightness_ev": 0.5})
    assert controller.method_calls[1][0] == method
    assert not bridge._flush_timer.isActive()
    assert app is not None


def test_mark_empty_uses_existing_status_command():
    from opticfilm_linux.batch.model import SlideStatus

    app = QApplication.instance() or QApplication(["test"])
    controller = Mock()
    bridge = Bridge(controller, MemoryPrefs())
    bridge.markEmpty(2, 3)
    controller.set_status.assert_called_once_with(2, 3, SlideStatus.BLANK)
    assert app is not None
