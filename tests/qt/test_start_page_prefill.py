"""The New batch card prefills what it can guess; a guess never overwrites what the owner typed."""

import pytest

pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

import test_qt_smoke as smoke
from PySide6.QtCore import QMetaObject, QObject, QUrl

from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.prefs import MemoryPrefs, layout_defaults, remember_layout
from opticfilm_linux.app.qt.app import create_engine, scanner_factory
from opticfilm_linux.app.qt.bridge import Bridge
from opticfilm_linux.app.qt.images import PreviewProvider

qapp = smoke.qapp


def _build(sweep600_dir, prefs):
    controller = Controller(scanner_factory(sweep600_dir))
    bridge = Bridge(controller, prefs, demo_dpi=600)
    provider = PreviewProvider(lambda n, p, k: controller.preview_path(n, p, k))
    engine, warnings = create_engine(bridge, provider, size=(1220, 780))
    assert engine.rootObjects(), warnings
    engine.warnings = warnings
    return bridge, engine


class Form:
    def __init__(self, bridge, engine, tmp_path, folder=True):
        self.bridge, self.root, self.tmp_path = bridge, engine.rootObjects()[0], tmp_path
        start = self.find("startPage")
        smoke._wait(bridge, lambda: start.property("enabled"))
        if folder:
            self.set_folder(tmp_path / "Mimi_Foundry-Work")

    def set_folder(self, path):
        self.find("startPage").setProperty("parentFolder", QUrl.fromLocalFile(str(path)))

    def find(self, name):
        item = self.root.findChild(QObject, name)
        assert item is not None, name
        return item

    def get(self, name, prop="text"):
        return self.find(name).property(prop)

    def set(self, name, value, prop="text"):
        self.find(name).setProperty(prop, value)

    def call(self, name):
        assert QMetaObject.invokeMethod(self.find("startPage"), name)


@pytest.fixture
def remembered(qapp, sweep600_dir):
    prefs = MemoryPrefs()
    remember_layout(prefs, "Family", "Maine", "c", 120, 3)
    bridge, engine = _build(sweep600_dir, prefs)
    yield bridge, engine
    bridge.shutdown()
    engine.deleteLater()


@pytest.fixture
def fresh(qapp, sweep600_dir):
    bridge, engine = _build(sweep600_dir, smoke.manual_export_prefs())
    yield bridge, engine
    bridge.shutdown()
    engine.deleteLater()


def test_the_empty_form_is_prefilled_from_the_last_batch(remembered, tmp_path):
    form = Form(*remembered, tmp_path)
    assert form.get("newBatchCollection") == "Family"
    assert form.get("newBatchAlbum") == "Maine"
    assert form.get("newBatchSection") == "c"
    assert form.get("newBatchSectionSize", "value") == 120
    assert form.get("newBatchBox", "value") == 4


def test_a_first_run_keeps_the_form_defaults(fresh, tmp_path):
    form = Form(*fresh, tmp_path)
    assert form.get("newBatchCollection") == ""
    assert form.get("newBatchSection") == "a"
    assert form.get("newBatchSectionSize", "value") == 140
    assert form.get("newBatchBox", "value") == 1


def test_typed_values_are_never_overwritten_by_the_prefill(remembered, tmp_path):
    form = Form(*remembered, tmp_path)
    form.set("newBatchCollection", "Mine")
    form.set("newBatchAlbum", "Mine")
    form.set("newBatchSection", "b")
    form.set("newBatchSectionSize", 77, "value")
    form.set("newBatchBox", 9, "value")
    form.call("prefillFromLastBatch")
    assert form.get("newBatchCollection") == "Mine"
    assert form.get("newBatchAlbum") == "Mine"
    assert form.get("newBatchSection") == "b"
    assert form.get("newBatchSectionSize", "value") == 77
    assert form.get("newBatchBox", "value") == 9


def test_the_name_comes_from_the_first_phrase_of_the_notes(fresh, tmp_path):
    form = Form(*fresh, tmp_path)
    form.set("newBatchKnow", "Maine trip, summer 1974, Kodachrome")
    assert form.get("newBatchName") == "Maine trip"


def test_the_name_follows_the_notes_while_it_is_still_a_guess(fresh, tmp_path):
    form = Form(*fresh, tmp_path)
    form.set("newBatchKnow", "Maine")
    form.set("newBatchKnow", "Maine trip, 1974")
    assert form.get("newBatchName") == "Maine trip"


def test_the_name_falls_back_to_the_folder_name(fresh, tmp_path):
    form = Form(*fresh, tmp_path)
    form.call("fillName")
    assert form.get("newBatchName") == "Mimi Foundry Work"


def test_a_typed_name_is_never_overwritten(fresh, tmp_path):
    form = Form(*fresh, tmp_path)
    form.set("newBatchName", "My name")
    form.set("newBatchKnow", "Maine trip, 1974")
    form.call("applyGuess")
    form.call("fillName")
    assert form.get("newBatchName") == "My name"


def test_creating_a_batch_remembers_its_filing_fields(fresh, tmp_path):
    bridge = fresh[0]
    form = Form(*fresh, tmp_path)
    form.set("newBatchName", "Meta")
    form.set("newBatchCollection", "Family")
    form.set("newBatchAlbum", "Maine")
    form.set("newBatchSection", "b")
    form.set("newBatchSectionSize", 100, "value")
    form.set("newBatchBox", 3, "value")
    form.find("startPage").setProperty("parentFolder", QUrl.fromLocalFile(str(tmp_path)))
    assert QMetaObject.invokeMethod(form.find("createBatch"), "click")
    smoke._wait(bridge, lambda: bridge.hasBatch)
    defaults = layout_defaults(bridge._prefs)
    assert (defaults.collection, defaults.album, defaults.section) == ("Family", "Maine", "b")
    assert (defaults.section_size, defaults.next_box) == (100, 4)
    assert (bridge.lastCollection, bridge.lastSection, bridge.nextBox) == ("Family", "b", 4)


def test_a_failed_create_remembers_nothing(fresh, tmp_path):
    bridge = fresh[0]
    bridge.newBatch(
        QUrl.fromLocalFile(str(tmp_path)).toString(),
        "Bad",
        "",
        "not a date",
        600,
        collection="Nope",
        box=7,
    )
    smoke._wait(bridge, lambda: bridge.hasBanner)
    assert layout_defaults(bridge._prefs).next_box == 1


def test_an_emptied_field_is_refilled_from_the_last_batch(remembered, tmp_path):
    form = Form(*remembered, tmp_path)
    form.set("newBatchAlbum", "")
    form.call("prefillFromLastBatch")
    assert form.get("newBatchAlbum") == "Maine"


def test_choosing_a_folder_proposes_the_name_without_any_other_call(fresh, tmp_path):
    form = Form(*fresh, tmp_path, folder=False)
    assert form.get("newBatchName") == ""
    form.set_folder(tmp_path / "Mimi_Foundry-Work")
    assert form.get("newBatchName") == "Mimi Foundry Work"
    form.set_folder(tmp_path / "Other_Box")
    assert form.get("newBatchName") == "Other Box"


def test_use_suggestion_fills_an_emptied_name(fresh, tmp_path):
    form = Form(*fresh, tmp_path, folder=False)
    form.set("newBatchKnow", "Maine trip, 1974")
    form.set("newBatchName", "")
    form.call("applyGuess")
    assert form.get("newBatchName") == "Maine trip"


def test_the_page_logs_no_qml_warnings(fresh, remembered):
    assert fresh[1].warnings == []
    assert remembered[1].warnings == []


def test_the_hint_shows_only_when_something_was_prefilled(fresh, remembered, tmp_path):
    bare = Form(*fresh, tmp_path, folder=False)
    assert not bare.get("newBatchPrefillHint", "visible")
    bare.set_folder(tmp_path / "Mimi_Foundry-Work")
    assert bare.get("newBatchPrefillHint", "visible")
    assert Form(*remembered, tmp_path, folder=False).get("newBatchPrefillHint", "visible")


def test_a_failed_create_then_an_open_remembers_nothing_stale(fresh, tmp_path):
    bridge = fresh[0]
    parent = QUrl.fromLocalFile(str(tmp_path)).toString()
    bridge.newBatch(parent, "First", "", "", 600, collection="Fam", box=3)
    smoke._wait(bridge, lambda: bridge.hasBatch)
    bridge.closeBatch()
    smoke._wait(bridge, lambda: not bridge.hasBatch)
    bridge.newBatch(parent, "Bad", "", "not a date", 600, collection="Nope", box=7)
    smoke._wait(bridge, lambda: bridge.hasBanner)
    bridge.openBatch(str(tmp_path / "First"), False)
    smoke._wait(bridge, lambda: bridge.hasBatch)
    defaults = layout_defaults(bridge._prefs)
    assert (defaults.collection, defaults.next_box) == ("Fam", 4)
