"""The New batch card takes a description and an estimated date (year, optional month)."""

from datetime import date

import pytest

pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

import test_qt_smoke as smoke
from PySide6.QtCore import QEvent, QMetaObject, QObject, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtQuick import QQuickItem

from opticfilm_linux.batch.store import BatchError, BatchStore

qapp = smoke.qapp
loaded = smoke.loaded


class Page:
    def __init__(self, loaded, tmp_path):
        self.bridge, engine, _warnings = loaded
        self.root = engine.rootObjects()[0]
        self.tmp_path = tmp_path
        start = self.find("startPage")
        smoke._wait(self.bridge, lambda: start.property("enabled"))  # the window is live
        start.setProperty("parentFolder", QUrl.fromLocalFile(str(tmp_path)))
        self.find("newBatchName").setProperty("text", "Meta")

    def find(self, name):
        item = self.root.findChild(QObject, name)
        assert item is not None, name
        return item

    def set(self, name, value, prop="text"):
        self.find(name).setProperty(prop, value)

    def type_into(self, name, text):
        """Real key presses into the focused control, so its validator and limits apply."""
        item = self.find(name)
        item.forceActiveFocus(Qt.TabFocusReason)
        for char in text:
            for kind in (QEvent.KeyPress, QEvent.KeyRelease):
                event = QKeyEvent(kind, Qt.Key_unknown, Qt.NoModifier, char)
                QGuiApplication.sendEvent(self.root, event)

    def focus_away(self):
        self.find("newBatchName").forceActiveFocus(Qt.TabFocusReason)

    def create_enabled(self):
        return self.find("createBatch").property("enabled")

    def create(self):
        assert QMetaObject.invokeMethod(self.find("createBatch"), "click")
        smoke._wait(self.bridge, lambda: self.bridge.hasBatch)
        self.bridge.closeBatch()
        smoke._wait(self.bridge, lambda: not self.bridge.hasBatch)
        with BatchStore.open(self.tmp_path / "Meta", readonly=True) as store:
            return store.snapshot()


@pytest.fixture
def page(loaded, tmp_path):
    return Page(loaded, tmp_path)


def test_the_fields_are_on_the_card_in_tab_order(page):
    description, year, month = (
        page.find(n) for n in ("newBatchDescription", "newBatchYear", "newBatchMonth")
    )
    assert description.property("placeholderText").startswith("e.g.")
    assert not month.property("enabled")  # no year yet
    page.set("newBatchYear", "1978")
    assert month.property("enabled")
    order = [description, year, month]
    assert all(isinstance(i, QQuickItem) for i in order)
    assert description.nextItemInFocusChain(True) is year
    assert year.nextItemInFocusChain(True) is month


@pytest.mark.parametrize(("year", "month", "stored"), [("1978", 0, "1978"), ("1978", 7, "1978-07")])
def test_a_year_or_year_and_month_is_stored_with_the_description(page, year, month, stored):
    page.set("newBatchDescription", "Lake trip")
    page.set("newBatchYear", year)
    page.set("newBatchMonth", month, "currentIndex")
    assert page.create_enabled()
    manifest = page.create()
    assert (manifest.date, manifest.description) == (stored, "Lake trip")


def test_a_blank_date_stores_none(page):
    manifest = page.create()
    assert (manifest.date, manifest.description) == (None, "")


@pytest.mark.parametrize("year", ["1850", str(date.today().year), "19"])
def test_a_bad_year_shows_the_message_and_disables_create(page, year):
    page.set("newBatchYear", year)
    error = page.find("newBatchDateError")
    assert error.property("visible")
    assert error.property("text")
    assert not page.create_enabled()
    page.set("newBatchYear", "1978")
    assert not error.property("visible")
    assert page.create_enabled()


def test_clear_empties_the_date_and_the_month(page):
    page.set("newBatchYear", "1978")
    page.set("newBatchMonth", 3, "currentIndex")
    assert QMetaObject.invokeMethod(page.find("newBatchDateClear"), "click")
    assert page.find("newBatchYear").property("text") == ""
    assert page.find("newBatchMonth").property("currentIndex") == 0
    assert not page.find("newBatchMonth").property("enabled")


def test_the_description_field_is_capped(page):
    assert page.find("newBatchDescription").property("maximumLength") == 500
    page.type_into("newBatchDescription", "é" * 600)
    assert len(page.find("newBatchDescription").property("text")) == 500


@pytest.mark.parametrize("typed", ["１９７８", "+1978", "1978.0", "abcd", "19a7b8"])
def test_only_ascii_digits_can_be_typed_into_the_year(page, typed):
    page.type_into("newBatchYear", typed)
    year = page.find("newBatchYear").property("text")
    assert year == "".join(c for c in typed if c in "0123456789")[:4]
    assert all(c in "0123456789" for c in year)


def test_the_error_waits_for_four_digits_or_leaving_the_field(page):
    error = page.find("newBatchDateError")
    page.type_into("newBatchYear", "1")
    assert not error.property("visible")
    assert not page.create_enabled()  # incomplete is never silently dropped
    page.type_into("newBatchYear", "85")
    assert not error.property("visible")
    page.type_into("newBatchYear", "0")
    assert error.property("visible")  # four digits, out of range
    page.set("newBatchYear", "")
    page.type_into("newBatchYear", "19")
    assert not error.property("visible")
    page.focus_away()
    assert error.property("visible")
    assert not page.create_enabled()


def test_create_needs_a_name_and_a_valid_date(page):
    page.set("newBatchName", "")
    page.set("newBatchYear", "1978")
    assert not page.create_enabled()
    page.set("newBatchName", "Meta")
    assert page.create_enabled()
    page.set("newBatchYear", "1850")
    assert not page.create_enabled()


def test_a_refusal_after_create_keeps_what_was_typed(page, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise BatchError("disk says no")

    monkeypatch.setattr(BatchStore, "create", refuse)
    page.set("newBatchDescription", "Lake trip")
    page.set("newBatchYear", "1978")
    page.set("newBatchMonth", 7, "currentIndex")
    assert QMetaObject.invokeMethod(page.find("createBatch"), "click")
    smoke._wait(page.bridge, lambda: page.bridge.hasBanner)
    assert "disk says no" in page.bridge.bannerMessage
    assert not page.bridge.hasBatch
    assert page.find("newBatchDescription").property("text") == "Lake trip"
    assert page.find("newBatchYear").property("text") == "1978"
    assert page.find("newBatchMonth").property("currentIndex") == 7


def test_tab_order_runs_name_description_year_month_clear(page):
    chain = ["newBatchName", "newBatchDescription", "newBatchYear", "newBatchMonth"]
    page.set("newBatchYear", "1978")
    items = [page.find(n) for n in chain] + [page.find("newBatchDateClear")]
    for before, after in zip(items, items[1:], strict=False):
        assert before.nextItemInFocusChain(True) is after


def test_the_bridge_validates_a_date_with_the_shared_rules(page):
    assert page.bridge.validateDate("") == ""
    assert page.bridge.validateDate("1978-07") == ""
    assert "1900" in page.bridge.validateDate("1850")
