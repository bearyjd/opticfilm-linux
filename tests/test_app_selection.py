from dataclasses import FrozenInstanceError

import pytest

from opticfilm_linux.app.selection import (
    Selection,
    UndoEntry,
    clear,
    click,
    extend,
    move_focus,
    pop_undo,
    prune,
    push_undo,
    select_all,
    select_tray,
    select_where,
    selectable_keys,
    toggle,
)
from opticfilm_linux.batch.model import (
    BatchManifest,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)

ORDER = ((1, 1), (1, 2), (1, 3), (1, 4), (3, 1), (3, 2))


def test_click_toggle_and_clear_are_immutable():
    initial = Selection()
    selected = click(initial, ORDER[0])
    assert initial == Selection()
    assert selected == Selection(frozenset({ORDER[0]}), ORDER[0], ORDER[0])
    added = toggle(selected, ORDER[-1])
    assert added.keys == frozenset({ORDER[0], ORDER[-1]})
    assert added.anchor == added.focus == ORDER[-1]
    removed = toggle(added, ORDER[-1])
    assert removed.keys == selected.keys
    assert removed.anchor == removed.focus == ORDER[-1]
    assert clear() == Selection()
    with pytest.raises(FrozenInstanceError):
        selected.focus = None


def test_selection_detaches_mutable_input_keys():
    keys = {(1, 1)}
    selected = Selection(keys)
    keys.add((3, 2))
    assert selected.keys == frozenset({(1, 1)})
    assert isinstance(selected.keys, frozenset)


@pytest.mark.parametrize(
    "gesture",
    [
        lambda sel: extend(sel, (99, 1), ORDER),
        lambda sel: select_tray(sel, 99, ORDER, additive=True),
    ],
)
def test_noop_gestures_return_new_selection(gesture):
    initial = click(Selection(), (1, 1))
    result = gesture(initial)
    assert result == initial
    assert result is not initial


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (1, 4, {(1, 2), (1, 3), (1, 4), (3, 1)}),
        (4, 1, {(1, 2), (1, 3), (1, 4), (3, 1)}),
        (2, 2, {(1, 3)}),
    ],
)
def test_ranges_cross_trays_in_both_directions(start, end, expected):
    initial = click(Selection(), ORDER[start])
    result = extend(initial, ORDER[end], ORDER)
    assert result.keys == frozenset(expected)
    assert result.anchor == ORDER[start]
    assert result.focus == ORDER[end]


def test_additive_range_and_missing_anchor():
    initial = Selection(frozenset({ORDER[0]}), ORDER[3], ORDER[3])
    assert extend(initial, ORDER[-1], ORDER, additive=True).keys == frozenset(
        (ORDER[0], *ORDER[3:])
    )
    vanished = Selection(initial.keys, (2, 1), ORDER[0])
    assert extend(vanished, ORDER[-1], ORDER) == click(Selection(), ORDER[-1])
    assert extend(initial, (99, 1), ORDER) == initial


def test_selectable_order_skips_busy_trays_and_keeps_blanks_and_failed():
    stages = (
        TrayStage.REVIEW,
        TrayStage.SCANNING,
        TrayStage.SCANNED,
        TrayStage.ENHANCING,
        TrayStage.EXPORTED,
        TrayStage.FAILED,
    )
    trays = tuple(
        TrayRecord(n, stage, slides=(SlideRecord(2, SlideStatus.BLANK), SlideRecord(1)))
        for n, stage in enumerate(stages, 1)
    )
    manifest = BatchManifest("test", trays=tuple(reversed(trays)))
    order = selectable_keys(manifest)
    assert order == ((1, 1), (1, 2), (5, 1), (5, 2), (6, 1), (6, 2))
    assert select_all(order) == Selection(frozenset(order), order[0], order[0])


def test_tray_and_predicate_selection():
    initial = click(Selection(), ORDER[0])
    assert select_tray(initial, 3, ORDER).keys == frozenset(ORDER[4:])
    assert select_tray(initial, 3, ORDER, additive=True).keys == frozenset((ORDER[0], *ORDER[4:]))
    assert select_tray(initial, 99, ORDER) == Selection()
    assert select_tray(initial, 99, ORDER, additive=True) == initial
    assert select_where(ORDER, lambda key: key[1] == 2).keys == frozenset({(1, 2), (3, 2)})


@pytest.mark.parametrize("delta,index", [(-99, 0), (-1, 2), (1, 4), (4, 5), (99, 5)])
def test_focus_movement_clamps_and_crosses_trays(delta, index):
    assert move_focus(click(Selection(), ORDER[3]), delta, ORDER) == click(
        Selection(), ORDER[index]
    )


def test_shift_focus_movement_preserves_anchor():
    initial = click(Selection(), ORDER[1])
    moved = move_focus(initial, 4, ORDER, extend=True)
    assert moved == Selection(frozenset(ORDER[1:]), ORDER[1], ORDER[-1])
    assert move_focus(moved, -2, ORDER, extend=True).keys == frozenset(ORDER[1:4])


def test_prune_after_rescan_drops_missing_keys_and_cursors():
    initial = Selection(frozenset(ORDER), ORDER[0], ORDER[-1])
    assert prune(initial, ORDER[4:]) == Selection(frozenset(ORDER[4:]), None, ORDER[-1])
    assert prune(initial, ()) == Selection()


def test_empty_single_and_missing_focus():
    assert select_all(()) == Selection()
    assert select_where(ORDER, lambda key: False) == Selection()
    assert move_focus(Selection(), 1, ()) == Selection()
    assert move_focus(Selection(), 1, ORDER) == click(Selection(), ORDER[0])
    assert move_focus(Selection(focus=(99, 1)), -1, ORDER) == click(Selection(), ORDER[-1])
    assert move_focus(click(Selection(), ORDER[0]), 99, ORDER[:1]).keys == frozenset(ORDER[:1])


def test_undo_snapshot_is_detached_and_stack_is_bounded():
    before = {ORDER[0]: (SlideSettings(rotation=90), SlideStatus.ACCEPTED, False)}
    entry = UndoEntry("Rotate", before)
    before.clear()
    assert entry.before[ORDER[0]] == (SlideSettings(rotation=90), SlideStatus.ACCEPTED, False)
    with pytest.raises(TypeError):
        entry.before[ORDER[0]] = (SlideSettings(), SlideStatus.PENDING, True)
    stack = ()
    for index in range(23):
        original = stack
        stack = push_undo(stack, UndoEntry(str(index), {}))
        assert original is not stack
    assert tuple(item.label for item in stack) == tuple(map(str, range(3, 23)))
    remaining, popped = pop_undo(stack)
    assert popped.label == "22"
    assert remaining == stack[:-1]
    assert len(stack) == 20
    assert pop_undo(()) == ((), None)
