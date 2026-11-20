"""``AutoPipeline.free_refusal`` is the single source of the free-held guards (GUI and CLI)."""

import dataclasses

import pytest

from opticfilm_linux.batch.model import Flag, SlideStatus
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.cli import main
from test_app_auto import pipeline

pytest_plugins = ("test_app_auto",)


def _defaults(**changes):
    return lambda m: dataclasses.replace(m, defaults=dataclasses.replace(m.defaults, **changes))


def _tray(number, **changes):
    def edit(manifest):
        trays = tuple(
            dataclasses.replace(t, **changes) if t.number == number else t for t in manifest.trays
        )
        return dataclasses.replace(manifest, trays=trays)

    return edit


def _slide(**changes):
    def edit(manifest):
        tray = manifest.trays[0]
        slides = (dataclasses.replace(tray.slides[0], **changes), *tray.slides[1:])
        return _tray(1, slides=slides)(manifest)

    return edit


CASES = [
    ("missing", 9, None, "does not exist"),
    ("not-held", 1, _tray(1, auto="failed"), "is not held"),
    ("raw-removed", 1, _tray(1, raw_removed=True), "is not held"),
    ("not-candidate", 2, _tray(2, auto="held"), "not the tray to release"),
    ("keep-raw", 1, _defaults(keep_raw=True), "keep_raw is set"),
    ("auto-export-off", 1, _defaults(auto_export=False), "auto-export is off"),
    (
        "awaiting-review",
        1,
        _slide(status=SlideStatus.NEEDS_LOOK, owner_decided=False),
        "await your review",
    ),
    (
        "owner-excluded",
        1,
        _slide(status=SlideStatus.NEEDS_LOOK, owner_decided=True),
        "excluded by your decision",
    ),
    ("unreviewed-flags", 1, _slide(flags=(Flag.TOO_DARK,)), "flags you have not reviewed"),
    ("stale-preview", 1, _slide(preview_stale=True), "previews are out of date"),
]


# GUI free attempts that must surface tray state (as on main), not no-op silently.
SURFACED = {
    "awaiting-review": ("blocked", "Waiting for owner review"),
    "owner-excluded": ("blocked", "Owner excluded slides"),
    "stale-preview": ("held", "Waiting for updated previews"),
}


def text_in(error):
    return next((m for _, m in SURFACED.values() if m in error), error)


def _held(store):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)
    tray = store.snapshot().trays[0]
    assert tray.auto == "held" and not tray.raw_removed
    return auto


def test_no_refusal_when_releasable_and_confirm_frees(store):
    auto = _held(store)
    assert auto.free_refusal(1) is None
    auto.confirm_free_held(1)
    assert store.snapshot().trays[0].raw_removed


@pytest.mark.parametrize(("case", "number", "edit", "text"), CASES, ids=[c[0] for c in CASES])
def test_each_refusal_reason_cli_agrees(store, capsys, case, number, edit, text):
    auto = _held(store)
    if edit is not None:
        store.update(edit)
    reason = auto.free_refusal(number)
    assert reason is not None and text in reason
    directory = store.directory
    store.close()
    capsys.readouterr()
    assert main(["batch", "free-held", str(directory), str(number), "--yes"]) == 3
    assert f"refused: {reason}" in capsys.readouterr().err
    reopened = BatchStore.open(directory)
    reopened.close()


@pytest.mark.parametrize(("case", "number", "edit", "text"), CASES, ids=[c[0] for c in CASES])
def test_gui_confirm_surfaces_or_noops_as_on_main(store, case, number, edit, text):
    auto = _held(store)
    if edit is not None:
        store.update(edit)
    before = store.snapshot()
    auto.confirm_free_held(number)
    after = store.snapshot()
    if case in SURFACED:
        tray = after.trays[0]
        assert not tray.raw_removed
        assert (tray.auto, text_in(tray.auto_error)) == SURFACED[case]
    else:
        assert after == before
