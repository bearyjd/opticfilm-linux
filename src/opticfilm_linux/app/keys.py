"""The tray keys as data: one key per action, and whether it may act now, and why not.

Qt-free. The verdict reads the same ``AppState`` flags the buttons are enabled from
(``can_scan``, ``can_eject``, ``can_load_next``, ``can_finish_box``) and the same tray count the
Export button uses, so a key can never do what its button would refuse. A press that is not meant
as a command (held key, typing in a text field, read-only batch, no batch) is inert and silent; a
press the buttons would refuse for a reason the owner can fix gets a banner text.
"""

from dataclasses import dataclass
from enum import Enum

from opticfilm_linux.app.holder import NEXT_STRIP_OFF
from opticfilm_linux.app.state import AppState
from opticfilm_linux.scanner.base import ScannerStatus


class Action(Enum):
    SCAN = "scan"
    EJECT = "eject"
    LOAD_NEXT = "load_next"
    EXPORT = "export"
    FINISH_BOX = "finish_box"


# Plain letters, no modifier. Free of the review keys (A R N M, Space, [ ], Ctrl+A, Ctrl+Z) and of
# Qt/system chords (all of those need Ctrl or Alt).
KEY_OF: dict[Action, str] = {
    Action.SCAN: "S",
    Action.EJECT: "E",
    Action.LOAD_NEXT: "L",
    Action.EXPORT: "X",
    Action.FINISH_BOX: "F",
}
REVIEW_LETTERS = frozenset("ARNM")

_ACTION_OF = {letter: action for action, letter in KEY_OF.items()}


@dataclass(frozen=True)
class KeyVerdict:
    allowed: bool
    reason: str = ""  # banner text for a refusal; empty when allowed or silently inert


ALLOWED = KeyVerdict(True)
INERT = KeyVerdict(False)


def action_for(letter: str) -> Action | None:
    return _ACTION_OF.get(letter.upper())


def _refuse(reason: str) -> KeyVerdict:
    return KeyVerdict(False, reason)


def _busy_reason(state: AppState) -> str | None:
    if state.scan_pending:
        return "Wait for the scan to finish"
    if state.holder_busy:
        return "Wait for the eject/load to finish"
    if state.scanner is ScannerStatus.INTERRUPTED:
        return "A scan or holder move was cut off. Confirm the scanner reset first"
    return None


def _scan_reason(state: AppState) -> str:
    if state.can_confirm_holder_latched:
        return "Confirm the holder is latched before scanning"
    if state.scanner is not ScannerStatus.READY:
        return "The scanner is not ready. Load and latch the holder first"
    return "Scan is not available right now"


def _eject_reason(state: AppState) -> str:
    if state.scanner not in (ScannerStatus.READY, ScannerStatus.NOT_READY):
        return "The scanner is not ready to eject"
    return "Eject is not available right now"


def _load_reason(state: AppState) -> str:
    if not state.next_strip_enabled:
        return NEXT_STRIP_OFF
    if state.scanner is not ScannerStatus.NOT_READY:
        return "Load next holder works after the holder was ejected"
    return "Load next holder is not available right now"


def _finish_reason(state: AppState) -> str:
    if not state.collection:
        return "Finish box needs a box: this batch has no collection"
    if state.metadata_updating:
        return "Wait for the metadata update to finish"
    return "Finish box is not available while a scan, export or update is running"


def decide(
    action: Action,
    state: AppState,
    tray_count: int,
    *,
    text_focus: bool = False,
    auto_repeat: bool = False,
) -> KeyVerdict:
    """May ``action`` act now? ``tray_count`` is the tray list the Export button looks at."""
    if text_focus or auto_repeat or state.batch_name is None or state.readonly:
        return INERT
    if action is Action.EXPORT:
        return ALLOWED if tray_count > 0 else _refuse("There is no tray to export yet")
    gates = {
        Action.SCAN: (state.can_scan, _scan_reason),
        Action.EJECT: (state.can_eject, _eject_reason),
        Action.LOAD_NEXT: (state.can_load_next, _load_reason),
        Action.FINISH_BOX: (state.can_finish_box and bool(state.collection), _finish_reason),
    }
    open_, reason = gates[action]
    if open_:
        return ALLOWED
    return _refuse(_busy_reason(state) or reason(state))
