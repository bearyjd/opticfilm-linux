"""Empty frame evidence remains reviewable when Qt serves cached previews."""
# ruff: noqa: F811

import os

import pytest
from PIL import Image

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QUrl  # noqa: E402
from PySide6.QtQml import QQmlComponent  # noqa: E402
from test_qt_smoke import _scanned, _wait, loaded, qapp  # noqa: E402, F401

from opticfilm_linux.app.qt.bridge import _slide_dict  # noqa: E402
from opticfilm_linux.app.state import SlideCard  # noqa: E402
from opticfilm_linux.batch.model import SlideSettings, SlideStatus  # noqa: E402


def test_before_url_survives_absent_after_preview():
    card = SlideCard(1, SlideStatus.ACCEPTED, (), SlideSettings(), False, False, False, 0, 17)
    mapped = _slide_dict(2, card)
    assert mapped["beforeUrl"].endswith("/before/17")
    assert mapped["afterUrl"] == ""


def test_cached_empty_frames_can_be_confirmed_and_enlarged(loaded, tmp_path):
    bridge, engine, warnings = loaded
    bridge.newBatch(QUrl.fromLocalFile(str(tmp_path)).toString(), "Empty", "", "", 600)
    _wait(bridge, lambda: bridge.hasBatch)
    root = engine.rootObjects()[0]
    _wait(bridge, lambda: root.findChild(QObject, "emptyFramesDialog") is not None)
    dialog = root.findChild(QObject, "emptyFramesDialog")
    preview = tmp_path / "blank.jpg"
    Image.new("RGB", (100, 80), "white").save(preview)
    item = {
        "position": 1,
        "fingerprint": "a" * 64,
        "url": QUrl.fromLocalFile(str(preview)).toString(),
    }
    for _ in range(2):
        dialog.setProperty("previews", [dict(item)])
        assert QMetaObject.invokeMethod(dialog, "open")
        _wait(bridge, lambda: dialog.property("loaded") == 1)
        assert root.findChild(QObject, "confirmReviewedEmptyFrames").property("enabled")
        assert QMetaObject.invokeMethod(dialog, "close")
    assert QMetaObject.invokeMethod(dialog, "open")
    _wait(bridge, lambda: dialog.property("opened"))
    assert QMetaObject.invokeMethod(dialog, "enlargeFrame", Q_ARG(int, 0))
    enlarged = root.findChild(QObject, "enlargedEmptyFrame")
    _wait(bridge, lambda: enlarged.property("opened"))
    assert warnings == []


def test_mark_empty_button_routes_slide_back_to_blank(loaded, tmp_path):
    bridge, engine, warnings = loaded
    _scanned(bridge, tmp_path, "Mark empty")
    from pathlib import Path

    import opticfilm_linux.app.qt.bridge as bridge_module

    qml = Path(bridge_module.__file__).parents[1] / "qml" / "AdjustPanel.qml"
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml)))
    panel = component.createWithInitialProperties(
        {
            "tray": 1,
            "position": 1,
            "status": "accepted",
            "mirror": False,
            "brightness": 0,
            "color": 1,
            "grain": 0,
            "sharpen": 0,
            "ownerDecided": True,
        }
    )
    assert panel is not None, component.errors()
    button = panel.findChild(QObject, "markEmpty_1")
    assert button is not None and button.property("enabled")
    store = bridge._controller._session.store
    assert store.snapshot().trays[0].slides[0].status is not SlideStatus.BLANK
    assert QMetaObject.invokeMethod(button, "click")
    _wait(bridge, lambda: store.snapshot().trays[0].slides[0].status is SlideStatus.BLANK)
    slide = store.snapshot().trays[0].slides[0]
    assert slide.owner_decided and slide.empty_confirmed is None
    panel.setProperty("status", "blank")
    assert not button.property("enabled")
    panel.deleteLater()
    assert warnings == []
