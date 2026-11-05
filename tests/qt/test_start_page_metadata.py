"""The New batch card takes a description and an estimated date (year, optional month)."""

from datetime import date

import pytest

pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

import test_qt_smoke as smoke
from PySide6.QtCore import QMetaObject, QObject, QUrl
from PySide6.QtQuick import QQuickItem

from opticfilm_linux.batch.store import BatchStore

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
    page.set("newBatchDescription", "é" * 600)
    assert len(page.find("newBatchDescription").property("text")) in (600, 500)


def test_the_bridge_validates_a_date_with_the_shared_rules(page):
    assert page.bridge.validateDate("") == ""
    assert page.bridge.validateDate("1978-07") == ""
    assert "1900" in page.bridge.validateDate("1850")
