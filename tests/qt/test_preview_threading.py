"""Even confirmation previews must read and decode images off the UI thread."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

from PySide6.QtCore import QThread, QUrl
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from opticfilm_linux.app.qt.images import PreviewProvider


def test_default_image_loading_reads_preview_on_worker(tmp_path):
    app = QApplication.instance() or QApplication(["preview-thread"])
    path = tmp_path / "preview.png"
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0xFF123456)
    assert image.save(str(path))
    threads = []

    def lookup(*_args):
        threads.append(QThread.currentThread())
        return path

    engine = QQmlEngine()
    engine.addImageProvider("preview", PreviewProvider(lookup))
    component = QQmlComponent(engine)
    component.setData(
        b"import QtQuick; Image { property bool ready: status === Image.Ready; "
        b'source: "image://preview/1/1/before/1" }',
        QUrl(),
    )
    item = component.create()
    assert item is not None, component.errors()
    for _ in range(200):
        QTest.qWait(5)
        if item.property("ready"):
            break
    assert threads and all(thread != app.thread() for thread in threads)
    assert item.property("ready")
    item.deleteLater()
