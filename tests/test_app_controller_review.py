import dataclasses

import pytest

from controller_helpers import (
    make_controller,
    open_new,
    scan_when_ready,
    wait_all_in_review,
    wait_until,
)
from opticfilm_linux.batch.model import SlideSettings, SlideStatus
from opticfilm_linux.batch.store import BatchStore


@pytest.fixture
def reviewed(sweep600_dir, tmp_path):
    """A controller with one tray in review, tray 1 selected."""
    c = make_controller(sweep600_dir)
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    wait_all_in_review(c, 1)
    c.select_tray(1)
    wait_until(c, lambda: len(c.state().slides) == 4)
    yield c
    c.shutdown()


def _slide(c, position=1):
    return next(s for s in c.state().slides if s.position == position)


def test_accept_all_accepts_every_non_blank_slide_in_review(reviewed):
    reviewed.set_status(1, 2, SlideStatus.NEEDS_LOOK)
    wait_until(reviewed, lambda: _slide(reviewed, 2).status is SlideStatus.NEEDS_LOOK)
    reviewed.accept_all()
    wait_until(
        reviewed,
        lambda: all(
            s.status in (SlideStatus.ACCEPTED, SlideStatus.BLANK) for s in reviewed.state().slides
        ),
    )
    assert _slide(reviewed, 2).owner_decided


def test_edit_marks_owner_decided_changes_preview_token_and_keeps_status(reviewed):
    before = _slide(reviewed)
    reviewed.edit_slide(1, 1, SlideSettings(brightness_ev=1.0))
    wait_until(
        reviewed,
        lambda: (
            _slide(reviewed).settings.brightness_ev == 1.0
            and not _slide(reviewed).updating
            and _slide(reviewed).preview_token != before.preview_token
        ),
    )
    after = _slide(reviewed)
    assert after.owner_decided and after.status is before.status


def test_rotate_wraps_around_both_ways(reviewed):
    reviewed.rotate(1, 1, -1)
    wait_until(reviewed, lambda: _slide(reviewed).settings.rotation == 270)
    reviewed.rotate(1, 1, 1)
    wait_until(reviewed, lambda: _slide(reviewed).settings.rotation == 0)


def test_reset_returns_to_auto(reviewed):
    reviewed.edit_slide(1, 1, SlideSettings(rotation=90, brightness_ev=-1.0, grain_strength=0.9))
    wait_until(reviewed, lambda: _slide(reviewed).settings.rotation == 90)
    reviewed.reset_slide(1, 1)
    wait_until(reviewed, lambda: _slide(reviewed).settings == SlideSettings())


def test_fifty_rapid_edits_end_consistent(reviewed):
    values = [-2.0 + 4.0 * i / 49 for i in range(50)]
    for value in values:
        reviewed.edit_slide(1, 1, SlideSettings(brightness_ev=value))
    wait_until(reviewed, lambda: _slide(reviewed).settings.brightness_ev == values[-1])
    wait_until(reviewed, lambda: not _slide(reviewed).updating)
    assert reviewed.state().banner is None


def test_edit_after_raw_removed_gives_banner_and_clears_updating(reviewed):
    reviewed._session.store.update_tray(1, raw_removed=True)
    reviewed.edit_slide(1, 1, SlideSettings(brightness_ev=1.0))
    wait_until(reviewed, lambda: reviewed.state().banner is not None)
    assert "removed" in reviewed.state().banner.message
    wait_until(reviewed, lambda: not _slide(reviewed).updating)
    assert _slide(reviewed).settings == SlideSettings()


def test_dismiss_banner_clears_it(reviewed):
    reviewed.set_status(1, 1, SlideStatus.BLANK)  # refused by the core
    wait_until(reviewed, lambda: reviewed.state().banner is not None)
    reviewed.dismiss_banner()
    wait_until(reviewed, lambda: reviewed.state().banner is None)


ACTIONS = {
    "set_status": lambda c: c.set_status(1, 1, SlideStatus.NEEDS_LOOK),
    "edit_slide": lambda c: c.edit_slide(1, 1, SlideSettings(brightness_ev=1.0)),
    "rotate": lambda c: c.rotate(1, 1, 1),
    "reset_slide": lambda c: c.reset_slide(1, 1),
    "accept_all": lambda c: c.accept_all(),
    "scan": lambda c: c.scan(),
    "rescan": lambda c: c.rescan(1),
}


@pytest.mark.parametrize("name", sorted(ACTIONS))
def test_readonly_batch_refuses_every_write_action(sweep600_dir, tmp_path, name):
    writer = make_controller(sweep600_dir)
    open_new(writer, tmp_path / "b")
    scan_when_ready(writer)
    wait_all_in_review(writer, 1)
    writer.shutdown()
    with BatchStore.open(tmp_path / "b", readonly=True) as peek:
        expected = peek.snapshot()
    c = make_controller(sweep600_dir)
    try:
        c.open_batch(tmp_path / "b", readonly=True)
        wait_until(c, lambda: c.state().batch_name == "Test")
        ACTIONS[name](c)
        wait_until(c, lambda: c.state().banner is not None)
        assert "read-only" in c.state().banner.message
        with BatchStore.open(tmp_path / "b", readonly=True) as after:
            assert after.snapshot() == expected
    finally:
        c.shutdown()


def test_accept_all_on_empty_batch_is_noop(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)
    try:
        open_new(c, tmp_path / "b")
        c.accept_all()
        c.tick()
        assert c.state().trays == () and dataclasses.is_dataclass(c.state())
    finally:
        c.shutdown()
