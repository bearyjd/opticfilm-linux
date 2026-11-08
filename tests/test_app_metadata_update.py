"""The window shows which exported JPEGs lack the current description or date and updates them
on request, or by themselves when the automatic update setting is on."""

import dataclasses

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


@pytest.fixture(params=[False], ids=["manual"])
def exported(request, sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir, update_exported_metadata_automatically=request.param)
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


# --- bulk scopes reaching exported slides, and the automatic update -------------------------


@pytest.fixture
def auto_exported(sweep600_dir, tmp_path):
    c = make_controller(sweep600_dir)  # the automatic update is on by default
    open_new(c, tmp_path / "b")
    scan_when_ready(c)
    wait_all_in_review(c, 1)
    c.select_tray(1)
    c.accept_all()
    c.export(tmp_path / "out", "jpeg")
    wait_until(c, lambda: c.state().export is not None and c.state().export.finished)
    yield c
    c.shutdown()


def _jpegs(tmp_path):
    return list((tmp_path / "out").rglob("*.jpg"))


def _frozen(c):
    """Everything a metadata edit must leave alone."""
    manifest = c._session.store.snapshot()
    return [
        (
            t.number,
            t.stage,
            t.auto,
            t.raw_removed,
            [dataclasses.replace(s, meta=None, exports=(), export_sha256="") for s in t.slides],
        )
        for t in manifest.trays
    ] + [manifest.defaults]


def test_the_default_is_on_and_a_batch_edit_marks_exported_jpegs_out_of_date(exported):
    c = exported  # automatic update off here: the pending state stays visible
    assert c.update_metadata_enabled is False
    frozen = _frozen(c)
    c.apply_meta_edit("batch", "description", "Whole batch")
    wait_until(c, lambda: c.state().metadata_pending > 0)
    assert _pending(c) == [s.position for s in c.state().slides if s.exported]
    assert all(s.exported for s in c.state().slides if s.metadata_pending)
    assert _frozen(c) == frozen  # settings, status, decisions and the render are untouched
    assert c.state().banner is None or "Updating" not in c.state().banner.message


def test_with_the_setting_on_a_batch_edit_updates_the_exported_jpegs_by_itself(
    auto_exported, tmp_path
):
    c = auto_exported
    assert c.update_metadata_enabled is True
    frozen = _frozen(c)
    c.apply_meta_edit("batch", "description", "Lake trip")
    wait_until(c, lambda: c.state().banner is not None and "updated in" in c.state().banner.message)
    wait_until(c, lambda: c.state().metadata_pending == 0)
    for path in _jpegs(tmp_path):
        assert b"<ofscan:Description>Lake trip</ofscan:Description>" in path.read_bytes()
    assert _frozen(c) == frozen
    c.undo()  # one undo step puts the batch back, and the files follow
    wait_until(c, lambda: c._session.store.snapshot().description == "")
    wait_until(c, lambda: c.state().metadata_pending == 0)
    wait_until(
        c, lambda: all(b"Lake trip" not in p.read_bytes() for p in _jpegs(tmp_path)), timeout=20
    )


def test_a_tray_range_and_append_also_follow_automatically(auto_exported, tmp_path):
    c = auto_exported
    c.apply_meta_edit("tray_range", "date", "1975", first=1, last=1)
    wait_until(c, lambda: all(b"1975" in p.read_bytes() for p in _jpegs(tmp_path)))
    c.apply_meta_edit("append", "description", "with Anna")
    wait_until(c, lambda: all(b"with Anna" in p.read_bytes() for p in _jpegs(tmp_path)))
    wait_until(c, lambda: c.state().metadata_pending == 0)


def test_the_setting_off_leaves_the_files_alone_and_the_button_still_works(exported, tmp_path):
    c = exported
    before = {p: p.read_bytes() for p in _jpegs(tmp_path)}
    c.apply_meta_edit("batch", "description", "Quiet")
    wait_until(c, lambda: c.state().metadata_pending > 0)
    c.accept_all()
    wait_until(c, lambda: not c.state().scan_pending)
    assert {p: p.read_bytes() for p in before} == before
    c.update_exported_metadata()
    wait_until(c, lambda: c.state().metadata_pending == 0)
    assert all(b"Quiet" in p.read_bytes() for p in _jpegs(tmp_path))


def test_turning_the_setting_on_catches_up_on_jpegs_already_out_of_date(exported, tmp_path):
    c = exported
    c.apply_meta_edit("batch", "description", "Late")
    wait_until(c, lambda: c.state().metadata_pending > 0)
    assert c.set_update_metadata(True) is True
    wait_until(c, lambda: c.state().metadata_pending == 0)
    assert all(b"Late" in p.read_bytes() for p in _jpegs(tmp_path))


def test_a_scan_in_progress_holds_the_automatic_update_back_until_it_ends(auto_exported, tmp_path):
    c = auto_exported
    c._scans_queued = 1  # a scan has been submitted and not yet handed over
    c.apply_meta_edit("batch", "description", "Held")
    wait_until(c, lambda: c.state().metadata_pending > 0)
    wait_until(c, lambda: c._meta_update_due)
    assert all(b"Held" not in p.read_bytes() for p in _jpegs(tmp_path))
    c._scans_queued = 0
    c._resume_metadata_update()
    wait_until(c, lambda: c.state().metadata_pending == 0)
    assert all(b"Held" in p.read_bytes() for p in _jpegs(tmp_path))


def test_a_file_that_cannot_be_updated_is_reported_in_the_banner(auto_exported, tmp_path):
    c = auto_exported
    victim = sorted(_jpegs(tmp_path))[0]
    victim.write_bytes(victim.read_bytes() + b"changed by someone else")
    c.apply_meta_edit("batch", "description", "Reported")
    wait_until(
        c, lambda: c.state().banner is not None and "not changed" in c.state().banner.message
    )
    assert "tray 1" in c.state().banner.detail


def test_a_read_only_batch_is_never_rewritten(auto_exported, tmp_path):
    c = auto_exported
    directory = tmp_path / "b"
    c.close_batch()
    wait_until(c, lambda: c.state().batch_name is None)
    before = {p: p.read_bytes() for p in _jpegs(tmp_path)}
    c.open_batch(directory, readonly=True)
    wait_until(c, lambda: c.state().readonly)
    c._queue_metadata_update_if_due()
    with pytest.raises(ValueError, match="read-only"):
        c.apply_meta_edit("batch", "description", "Nope")
    c.update_exported_metadata()  # the button on a read-only batch says why, writes nothing
    wait_until(c, lambda: c.state().banner is not None)
    assert {p: p.read_bytes() for p in before} == before


def test_preview_counts_without_writing_and_refuses_what_apply_refuses(exported):
    c = exported
    assert c.preview_meta_edit("batch", "description", "x") == "Changes 4 slides"
    assert c._session.store.snapshot().description == ""
    with pytest.raises(ValueError, match="no tray 9"):
        c.preview_meta_edit("tray", "description", "x", tray=9)
    with pytest.raises(ValueError, match="description only"):
        c.preview_meta_edit("append", "date", "1980")
