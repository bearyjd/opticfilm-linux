"""The window's orientation actions: per-slide mirror and the batch-wide default."""

import pytest

from controller_helpers import (
    make_controller,
    open_new,
    scan_when_ready,
    wait_all_in_review,
    wait_until,
)
from opticfilm_linux.batch.model import TrayStage


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


def test_mirror_toggles_and_keeps_the_other_settings(reviewed):
    reviewed.rotate(1, 1, 1)
    reviewed.mirror(1, 1)
    wait_until(reviewed, lambda: _slide(reviewed).settings.mirror)
    assert _slide(reviewed).settings.rotation == 90
    reviewed.mirror(1, 1)
    wait_until(reviewed, lambda: not _slide(reviewed).settings.mirror)
    assert _slide(reviewed).owner_decided


def test_a_new_batch_starts_with_the_chosen_orientation(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)
    try:
        c.new_batch(tmp_path / "b", "Test", "", None, 600, rotation=270, mirror=True)
        wait_until(c, lambda: c.state().batch_name == "Test")
        assert (c.state().default_rotation, c.state().default_mirror) == (270, True)
    finally:
        c.shutdown()


def test_set_batch_orientation_re_enhances_every_slide(reviewed):
    tokens = {s.position: s.preview_token for s in reviewed.state().slides}
    reviewed.set_batch_orientation(90, True)
    wait_until(
        reviewed,
        lambda: (
            reviewed.state().default_rotation == 90
            and reviewed.state().default_mirror
            and all(s.preview_token != tokens[s.position] for s in reviewed.state().slides)
        ),
    )
    assert reviewed.state().banner is None


def test_set_batch_orientation_names_the_trays_it_could_not_change(reviewed):
    reviewed._session.store.update_tray(1, stage=TrayStage.EXPORTED, raw_removed=True)
    reviewed.set_batch_orientation(180, False)
    wait_until(reviewed, lambda: reviewed.state().banner is not None)
    assert "tray 1" in reviewed.state().banner.message
    assert reviewed.state().default_rotation == 180


def test_set_batch_orientation_while_a_tray_is_busy_gives_a_banner(reviewed):
    reviewed._session.store.update_tray(1, stage=TrayStage.ENHANCING)
    reviewed.set_batch_orientation(90, False)
    wait_until(reviewed, lambda: reviewed.state().banner is not None)
    assert "orientation" in reviewed.state().banner.message
    assert reviewed.state().default_rotation == 0
