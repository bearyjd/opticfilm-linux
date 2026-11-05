"""The window shows which exported JPEGs lack the current description or date and updates them
on request, never by itself."""

import pytest

from controller_helpers import (
    make_controller,
    open_new,
    scan_when_ready,
    wait_all_in_review,
    wait_until,
)
from opticfilm_linux.app.messages import meta_update_text
from opticfilm_linux.batch.meta_sync import MetaUpdateResult
from opticfilm_linux.batch.store import BatchStore


@pytest.fixture
def exported(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    wait_all_in_review(c, 1)
    c.select_tray(1)
    c.accept_all()
    c.export(tmp_path / "out", "jpeg")
    wait_until(c, lambda: c.state().export is not None and c.state().export.finished)
    yield c
    c.shutdown()


def _pending(c):
    return [s.position for s in c.state().slides if s.metadata_pending]


def test_a_tray_edit_shows_badges_and_the_update_clears_them(exported, tmp_path):
    c = exported
    assert c.state().metadata_pending == 0 and _pending(c) == []
    c.set_tray_meta(1, "description", "Lake trip")
    wait_until(c, lambda: c.state().metadata_pending > 0)
    count = c.state().metadata_pending
    assert _pending(c) == [s.position for s in c.state().slides if s.exported]
    sheet = [s.metadata_pending for s in c.state().trays[0].slides]
    assert sheet == [s.metadata_pending for s in c.state().slides]
    c.update_exported_metadata()
    wait_until(c, lambda: c.state().metadata_pending == 0)
    wait_until(c, lambda: c.state().banner is not None and "updated in" in c.state().banner.message)
    assert c.state().banner.message.startswith(f"Description and date updated in {count} JPEG")
    paths = list((tmp_path / "out").rglob("*.jpg"))
    assert paths
    for path in paths:
        assert b"<ofscan:Description>Lake trip</ofscan:Description>" in path.read_bytes()


def test_nothing_is_rewritten_without_the_request(exported, tmp_path):
    c = exported
    before = {p: p.read_bytes() for p in (tmp_path / "out").rglob("*.jpg")}
    c.set_tray_meta(1, "date", "1979")
    wait_until(c, lambda: c.state().metadata_pending > 0)
    c.accept_all()  # any later command: still no rewrite
    wait_until(c, lambda: not c.state().scan_pending)
    assert {p: p.read_bytes() for p in before} == before
    with BatchStore.open(tmp_path / "b", readonly=True) as peek:
        assert all(
            not r.meta_signature for t in peek.snapshot().trays for s in t.slides for r in s.exports
        )


def test_the_summary_names_each_file_that_was_not_changed():
    result = MetaUpdateResult(((1, 1),), tuple(((1, p), "the file changed") for p in range(2, 9)))
    message, detail = meta_update_text(result)
    assert message == "Description and date updated in 1 JPEG. 7 not changed."
    assert detail.startswith("tray 1, slide 2: the file changed; tray 1, slide 3")
    assert detail.endswith("and 2 more")
    assert meta_update_text(MetaUpdateResult((), ())) == (
        "Description and date updated in 0 JPEGs.",
        "",
    )
