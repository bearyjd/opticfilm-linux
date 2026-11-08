"""The New batch card proposes a description and date from what the owner knows (guess assist)."""

import pytest

pytestmark = pytest.mark.qt
pytest.importorskip("PySide6")

import test_qt_smoke as smoke
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QUrl

from opticfilm_linux.batch.store import BatchStore

qapp = smoke.qapp
loaded = smoke.loaded


def _plain(value):
    """A QML var arrives as a dict/list or as a QJSValue, depending on what holds it."""
    return value.toVariant() if hasattr(value, "toVariant") else value


KNOW = "Maine trip, summer 1974, Kodachrome, Grandma's camera"


class Page:
    def __init__(self, loaded, tmp_path):
        self.bridge, engine, _warnings = loaded
        self.root = engine.rootObjects()[0]
        self.tmp_path = tmp_path
        start = self.find("startPage")
        smoke._wait(self.bridge, lambda: start.property("enabled"))
        start.setProperty("parentFolder", QUrl.fromLocalFile(str(tmp_path)))
        self.find("newBatchName").setProperty("text", "Meta")

    def find(self, name):
        item = self.root.findChild(QObject, name)
        assert item is not None, name
        return item

    def set(self, name, value, prop="text"):
        self.find(name).setProperty(prop, value)

    def click(self, name):
        assert QMetaObject.invokeMethod(self.find(name), "click")

    def chips(self):
        """The choices the chips show, in order: read from the page, never by walking items."""
        return list(_plain(self.find("startPage").property("guessChoices")))

    def checked(self, value):
        return self.find("startPage").property("pendingDate") == value

    def notes(self):
        return list(_plain(self.find("startPage").property("guess"))["notes"])

    def pick(self, value):
        assert QMetaObject.invokeMethod(
            self.find("startPage"), "pickGuess", Q_ARG("QVariant", value)
        )

    def date(self):
        return self.find("newBatchYear").property("text"), self.find("newBatchMonth").property(
            "currentIndex"
        )

    def description(self):
        return self.find("newBatchDescription").property("text")


@pytest.fixture
def page(loaded, tmp_path):
    return Page(loaded, tmp_path)


def test_no_suggestion_until_something_is_typed(page):
    assert not page.find("newBatchGuess").property("visible")
    assert page.find("newBatchKnow").property("text") == ""


def test_a_suggestion_appears_with_the_default_chip_the_alternatives_and_the_note(page):
    page.set("newBatchKnow", KNOW)
    assert page.find("newBatchGuess").property("visible")
    assert "1974" in page.find("newBatchGuessText").property("text")
    assert page.chips() == ["1974", "1974-06", "1974-07", "1974-08"]
    assert page.find("newBatchGuessRepeater").property("count") == 4
    assert page.checked("1974")
    assert not page.checked("1974-07")
    notes = page.notes()
    assert len(notes) == 1
    assert "summer" in notes[0]


def test_use_suggestion_fills_the_empty_fields(page):
    page.set("newBatchKnow", KNOW)
    page.click("newBatchGuessApply")
    assert page.description() == KNOW
    assert page.date() == ("1974", 0)


def test_the_batch_is_created_through_the_normal_path_with_the_applied_values(page):
    page.set("newBatchKnow", "Christmas 1983 - Dec")
    page.click("newBatchGuessApply")
    page.click("createBatch")
    smoke._wait(page.bridge, lambda: page.bridge.hasBatch)
    page.bridge.closeBatch()
    smoke._wait(page.bridge, lambda: not page.bridge.hasBatch)
    with BatchStore.open(page.tmp_path / "Meta", readonly=True) as store:
        manifest = store.snapshot()
    assert (manifest.date, manifest.description) == ("1983-12", "Christmas 1983 - Dec")


def test_picking_a_chip_changes_what_use_suggestion_applies(page):
    page.set("newBatchKnow", KNOW)
    page.pick("1974-07")
    assert page.checked("1974-07")
    assert not page.checked("1974")
    page.click("newBatchGuessApply")
    assert page.date() == ("1974", 7)


def test_typed_values_are_never_overwritten_by_use_suggestion(page):
    page.set("newBatchDescription", "My own words")
    page.set("newBatchYear", "1999")
    page.set("newBatchKnow", KNOW)
    page.click("newBatchGuessApply")
    assert page.description() == "My own words"
    assert page.date() == ("1999", 0)


def test_only_the_empty_field_is_filled(page):
    page.set("newBatchDescription", "My own words")
    page.set("newBatchKnow", KNOW)
    page.click("newBatchGuessApply")
    assert page.description() == "My own words"
    assert page.date() == ("1974", 0)


def test_replace_is_an_explicit_choice_per_field(page):
    page.set("newBatchDescription", "My own words")
    page.set("newBatchYear", "1999")
    page.set("newBatchKnow", KNOW)
    assert page.find("newBatchGuessReplaceDescription").property("visible")
    assert page.find("newBatchGuessReplaceDate").property("visible")
    page.click("newBatchGuessReplaceDate")
    assert page.date() == ("1974", 0)
    assert page.description() == "My own words"
    page.click("newBatchGuessReplaceDescription")
    assert page.description() == KNOW


def test_replace_buttons_hide_when_the_fields_are_empty_or_already_match(page):
    page.set("newBatchKnow", KNOW)
    assert not page.find("newBatchGuessReplaceDescription").property("visible")
    assert not page.find("newBatchGuessReplaceDate").property("visible")
    page.click("newBatchGuessApply")
    assert not page.find("newBatchGuessReplaceDescription").property("visible")
    assert not page.find("newBatchGuessReplaceDate").property("visible")


def test_a_decade_has_no_default_so_use_suggestion_leaves_the_date_empty(page):
    page.set("newBatchKnow", "Family slides, the 1970s")
    assert not any(page.checked(c) for c in page.chips())
    assert len(page.chips()) == 10
    page.click("newBatchGuessApply")
    assert page.description() == "Family slides, the 1970s"
    assert page.date() == ("", 0)
    page.pick("1973")
    page.click("newBatchGuessApply")
    assert page.date() == ("1973", 0)  # picked now, and the date field was still empty


def test_a_decade_pick_is_applied_once_chosen(page):
    page.set("newBatchKnow", "the 1970s")
    page.pick("1973")
    page.click("newBatchGuessApply")
    assert page.date() == ("1973", 0)


def test_changing_the_text_resets_the_pick(page):
    page.set("newBatchKnow", KNOW)
    page.pick("1974-07")
    page.set("newBatchKnow", "Dec 1983")
    assert page.find("newBatchGuessChips").property("visible") is False
    page.click("newBatchGuessApply")
    assert page.date() == ("1983", 12)


def test_editing_the_text_keeps_the_pick_while_the_parsed_date_is_unchanged(page):
    page.set("newBatchKnow", KNOW)
    page.pick("1974-07")
    page.set("newBatchKnow", KNOW + ", slide box 2")
    assert page.checked("1974-07")


def test_a_pick_that_is_no_longer_offered_falls_back_to_the_default(page):
    page.set("newBatchKnow", KNOW)
    page.pick("1974-07")
    # Same parsed date (1974), but "summer" is gone so the month chips are too
    page.set("newBatchKnow", "Maine trip, 1974, Kodachrome, Grandma's camera")
    page.click("newBatchGuessApply")
    assert page.date() == ("1974", 0)


def test_clearing_the_text_hides_the_suggestion(page):
    page.set("newBatchKnow", KNOW)
    page.set("newBatchKnow", "")
    assert not page.find("newBatchGuess").property("visible")


def test_ctrl_enter_in_the_field_uses_the_suggestion(page):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QGuiApplication, QKeyEvent

    know = page.find("newBatchKnow")
    page.set("newBatchKnow", "Dec 1983")
    know.forceActiveFocus(Qt.TabFocusReason)
    for kind in (QEvent.KeyPress, QEvent.KeyRelease):
        QGuiApplication.sendEvent(page.root, QKeyEvent(kind, Qt.Key_Return, Qt.ControlModifier))
    assert page.description() == "Dec 1983"
    assert page.date() == ("1983", 12)


def test_one_chip_is_shown_per_choice(page):
    page.set("newBatchKnow", KNOW)
    repeater = page.find("newBatchGuessRepeater")
    assert repeater.property("count") == len(page.chips()) == 4
    assert page.find("newBatchGuessChips").property("visible")
    assert page.find("newBatchGuessApply").property("visible")


def test_the_bridge_slot_is_pure(page):
    before = (page.description(), page.date(), page.bridge.hasBatch)
    first = page.bridge.guessBatch(KNOW)
    again = page.bridge.guessBatch(KNOW)
    assert first == again
    assert first["date"] == "1974"
    assert first["alternatives"] == ["1974-06", "1974-07", "1974-08"]
    assert first["description"] == KNOW
    assert first["notes"]
    assert page.bridge.guessBatch("") == {
        "description": "",
        "date": "",
        "alternatives": [],
        "notes": [],
    }
    assert (page.description(), page.date(), page.bridge.hasBatch) == before


def test_the_know_field_is_capped_at_20000_characters(page):
    page.set("newBatchKnow", "x" * 25000)
    assert len(page.find("newBatchKnow").property("text")) == 20000
    assert page.find("newBatchGuess").property("visible")
