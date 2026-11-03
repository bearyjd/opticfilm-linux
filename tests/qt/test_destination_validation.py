"""Setup filesystem probes debounce typing and cannot publish stale results."""

import sys
import threading

import pytest

pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

import test_qt_smoke as smoke
from PySide6.QtCore import QObject, QUrl
from PySide6.QtTest import QTest

from opticfilm_linux.app.qt import bridge as bridge_module
from opticfilm_linux.batch.store import BatchError

qapp = smoke.qapp
loaded = smoke.loaded


def _setup(loaded, tmp_path, monkeypatch, probe):
    bridge, engine, _warnings = loaded
    monkeypatch.setattr(bridge_module, "_check_export_directory", probe, raising=False)
    worker = sys.modules.get("opticfilm_linux.app.qt.destination")
    if worker is not None:
        monkeypatch.setattr(worker, "_check_export_directory", probe)
    root = engine.rootObjects()[0]
    page = root.findChild(QObject, "startPage")
    page.setProperty("parentFolder", QUrl.fromLocalFile(str(tmp_path)))
    name = root.findChild(QObject, "newBatchName")
    name.setProperty("text", "First")
    page.setProperty("exportFolder", QUrl.fromLocalFile(str(tmp_path / "photos")))
    root.findChild(QObject, "newBatchAutoExport").setProperty("checked", True)
    return bridge, root, page, name


def test_setup_debounces_filesystem_probes_off_the_qt_thread(loaded, tmp_path, monkeypatch):
    calls = []
    gui_thread = threading.get_ident()

    def probe(batch, _destination):
        calls.append((batch.name, threading.get_ident()))

    bridge, root, _page, name = _setup(loaded, tmp_path, monkeypatch, probe)
    for value in ("Typing", "Typed", "Final"):
        name.setProperty("text", value)
        QTest.qWait(20)
    assert calls == []
    assert not root.findChild(QObject, "createBatch").property("enabled")
    smoke._wait(bridge, lambda: root.findChild(QObject, "createBatch").property("enabled"))
    assert calls == [("Final", calls[0][1])]
    assert calls[0][1] != gui_thread


def test_stale_probe_cannot_enable_create_for_changed_destination(loaded, tmp_path, monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def probe(_batch, destination):
        if destination.name == "photos":
            started.set()
            assert release.wait(5)
            finished.set()
        else:
            raise BatchError("Current destination is invalid")

    bridge, root, page, _name = _setup(loaded, tmp_path, monkeypatch, probe)
    create = root.findChild(QObject, "createBatch")
    try:
        smoke._wait(bridge, started.is_set)
        page.setProperty("exportFolder", QUrl.fromLocalFile(str(tmp_path / "invalid")))
        assert not create.property("enabled")
        release.set()
        smoke._wait(bridge, finished.is_set)
        QTest.qWait(30)
        assert not create.property("enabled")
        reason = root.findChild(QObject, "automaticExportProblem")
        smoke._wait(bridge, lambda: reason.property("text") == "Current destination is invalid")
        assert not create.property("enabled")
    finally:
        release.set()
