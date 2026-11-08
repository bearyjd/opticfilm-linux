"""The tray key map and its allowed/refused matrix (Qt-free)."""

import dataclasses

import pytest

from opticfilm_linux.app import keys
from opticfilm_linux.app.keys import Action, decide
from opticfilm_linux.app.state import EMPTY_STATE
from opticfilm_linux.scanner.base import ScannerStatus

OPEN = dataclasses.replace(
    EMPTY_STATE,
    batch_name="b",
    collection="C",
    scanner=ScannerStatus.READY,
    can_scan=True,
    can_eject=True,
    can_load_next=True,
    can_finish_box=True,
)
GATE = {
    Action.SCAN: "can_scan",
    Action.EJECT: "can_eject",
    Action.LOAD_NEXT: "can_load_next",
    Action.FINISH_BOX: "can_finish_box",
}


def test_every_action_has_exactly_one_distinct_key():
    assert set(keys.KEY_OF) == set(Action)
    assert len(set(keys.KEY_OF.values())) == len(Action)
    for action, letter in keys.KEY_OF.items():
        assert keys.action_for(letter) is action
        assert keys.action_for(letter.lower()) is action


def test_no_key_collides_with_a_review_key():
    assert not set(keys.KEY_OF.values()) & keys.REVIEW_LETTERS
    assert keys.action_for("Q") is None


@pytest.mark.parametrize("action", list(Action))
def test_open_gates_allow(action):
    assert decide(action, OPEN, tray_count=1).allowed


@pytest.mark.parametrize("action", list(GATE))
def test_a_closed_button_gate_refuses_with_a_reason(action):
    state = dataclasses.replace(OPEN, **{GATE[action]: False})
    verdict = decide(action, state, tray_count=1)
    assert not verdict.allowed and verdict.reason


def test_export_needs_a_tray():
    verdict = decide(Action.EXPORT, OPEN, tray_count=0)
    assert not verdict.allowed and verdict.reason


@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize(
    "extra",
    [{"text_focus": True}, {"auto_repeat": True}],
)
def test_text_focus_and_auto_repeat_are_inert_and_silent(action, extra):
    verdict = decide(action, OPEN, tray_count=1, **extra)
    assert verdict == keys.INERT


@pytest.mark.parametrize("action", list(Action))
def test_readonly_and_no_batch_are_inert(action):
    assert decide(action, dataclasses.replace(OPEN, readonly=True), 1) == keys.INERT
    assert decide(action, dataclasses.replace(OPEN, batch_name=None), 1) == keys.INERT


@pytest.mark.parametrize(
    ("changes", "text"),
    [
        ({"scan_pending": True, "can_scan": False}, "scan to finish"),
        ({"holder_busy": True, "can_scan": False}, "eject/load"),
        ({"can_scan": False, "can_confirm_holder_latched": True}, "latched"),
        ({"can_scan": False, "scanner": ScannerStatus.NOT_READY}, "not ready"),
    ],
)
def test_scan_refusal_reasons(changes, text):
    verdict = decide(Action.SCAN, dataclasses.replace(OPEN, **changes), 1)
    assert not verdict.allowed and text in verdict.reason


def test_load_next_switched_off_says_so():
    state = dataclasses.replace(OPEN, can_load_next=False, next_strip_enabled=False)
    assert "OPTICFILM_NEXT_STRIP" in decide(Action.LOAD_NEXT, state, 1).reason


def test_finish_box_needs_a_collection_and_waits_for_metadata_update():
    no_box = dataclasses.replace(OPEN, collection="")
    assert not decide(Action.FINISH_BOX, no_box, 1).allowed
    busy = dataclasses.replace(OPEN, can_finish_box=False, metadata_updating=True)
    assert "metadata update" in decide(Action.FINISH_BOX, busy, 1).reason


def test_no_decision_path_enables_what_the_state_forbids():
    # Every state flag combination: a key is allowed only if its button flag is.
    for action, field in GATE.items():
        closed = dataclasses.replace(OPEN, **{field: False})
        assert not decide(action, closed, 5).allowed
