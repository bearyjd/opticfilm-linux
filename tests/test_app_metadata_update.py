"""The window shows which exported JPEGs lack the current description or date and updates them
on request, or by themselves when the automatic update setting is on."""

import dataclasses
import hashlib
import os
import threading
import types
from pathlib import Path

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


# --- the automatic update is a background job: it never blocks, yields, resumes --------------


@pytest.fixture
def gated(monkeypatch):
    """Rewrites go through; the second one waits for the test, so a run can be caught midway."""
    from opticfilm_linux.batch import meta_sync

    real = meta_sync.rewrite_xmp_file
    state = types.SimpleNamespace(
        calls=[], entered=threading.Event(), gate=threading.Event(), stop_at=2
    )

    def slow(path, *args, **kwargs):
        state.calls.append(path)
        if len(state.calls) == state.stop_at:
            state.entered.set()
            state.gate.wait(20)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(meta_sync, "rewrite_xmp_file", slow)
    return state


def _caught_midway(c, gated):
    c.apply_meta_edit("batch", "description", "Midway")
    wait_until(c, lambda: gated.entered.is_set())


def test_an_automatic_update_does_not_block_a_scan_and_a_scan_stops_it_and_it_resumes(
    auto_exported, gated, tmp_path
):
    c = auto_exported
    _caught_midway(c, gated)
    banner = c.state().banner.message
    assert banner.startswith("Updating metadata in exported JPEGs") and "of 4" in banner
    assert c.state().can_scan  # not counted as an export or other long job
    with c._lock:
        assert c._holder_refusal() is None and not (c._long_jobs or c._job_futures)
    c.scan()  # not refused; it asks the update to stop after the file it is on
    assert c._meta_cancel.is_set()
    gated.gate.set()
    wait_until(c, lambda: len(c.state().trays) == 2 and c.state().trays[1].stage.value == "review")
    wait_until(c, lambda: c.state().metadata_pending == 0)
    # the first run did 2 files; the resumed one did only the 2 that were not yet done
    assert len(gated.calls) == 4 and len(set(gated.calls)) == 4
    assert all(b"Midway" in p.read_bytes() for p in _jpegs(tmp_path))


def test_an_export_waits_for_the_update_to_stop_and_never_overlaps_it(
    auto_exported, gated, tmp_path, monkeypatch
):
    from opticfilm_linux.app import controller

    c = auto_exported
    during = []
    real_export = controller.run_export

    def watching(*args, **kwargs):
        during.append(c._meta_update_active)  # was the update still going when the export began?
        return real_export(*args, **kwargs)

    monkeypatch.setattr(controller, "run_export", watching)
    _caught_midway(c, gated)
    c.export(tmp_path / "out2", "jpeg")
    threading.Timer(0.5, gated.gate.set).start()
    wait_until(c, lambda: bool(during))
    assert during == [False]
    wait_until(c, lambda: c.state().metadata_pending == 0)  # and it resumed afterwards


def test_rapid_edits_queue_at_most_one_more_pass(auto_exported, gated):
    c = auto_exported
    submitted = []
    real_submit = c._meta_pool.submit
    c._meta_pool.submit = lambda *a, **k: (submitted.append(1), real_submit(*a, **k))[1]
    _caught_midway(c, gated)  # run one is under way
    c.apply_meta_edit("batch", "description", "Second")
    c.apply_meta_edit("batch", "description", "Third")
    wait_until(c, lambda: c.state().undo_label == "Edit metadata" and c._meta_update_due)
    assert len(submitted) == 1  # the edits only kept it due
    gated.gate.set()
    wait_until(c, lambda: c.state().metadata_pending == 0 and not c._meta_update_active)
    assert len(submitted) == 2


def test_the_owner_can_stop_an_automatic_update_and_it_stays_stopped_until_the_next_edit(
    auto_exported, gated
):
    c = auto_exported
    _caught_midway(c, gated)
    c.cancel_metadata_update()
    gated.gate.set()
    wait_until(c, lambda: not c._meta_update_active)
    for _ in range(20):
        c.tick()
    assert c.state().metadata_pending == 2 and not c._meta_update_due
    c.apply_meta_edit("batch", "description", "Again")
    wait_until(c, lambda: c.state().metadata_pending == 0)


def test_the_manual_button_can_be_stopped(exported, gated):
    c = exported
    c.apply_meta_edit("batch", "description", "Manual")
    wait_until(c, lambda: c.state().metadata_pending == 4)
    c.update_exported_metadata()
    wait_until(c, lambda: gated.entered.is_set())
    c.cancel_metadata_update()
    gated.gate.set()
    wait_until(
        c, lambda: c.state().banner is not None and "updated in 2" in c.state().banner.message
    )
    assert c.state().metadata_pending == 2


# --- review round 2: no start can slip past a stop-and-wait, export blocks the start, recovery ---


def test_a_stop_and_wait_cannot_miss_an_update_that_is_just_starting(auto_exported, monkeypatch):
    c = auto_exported
    submitted, release = threading.Event(), threading.Event()
    real_run = c._run

    def held_run(*args, **kwargs):
        if kwargs.get("pool") is c._meta_pool:
            submitted.set()  # the update is marked active; its job is not yet on the worker
            release.wait(20)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(c, "_run", held_run)
    c.apply_meta_edit("batch", "description", "Barrier")
    wait_until(c, lambda: submitted.is_set())
    finished = threading.Event()

    def export_hand_over():
        c._pause_metadata_update(wait=True)  # what an export does before it starts
        finished.set()

    thread = threading.Thread(target=export_hand_over)
    thread.start()
    assert not finished.wait(0.5)  # it waits: the update is active although its job is unborn
    release.set()
    thread.join(20)
    assert finished.is_set() and not c._meta_update_active
    assert c._meta_idle.is_set()


def test_an_export_that_is_running_keeps_the_automatic_update_from_starting(
    auto_exported, tmp_path, monkeypatch
):
    from opticfilm_linux.app import controller

    c = auto_exported
    inside, release = threading.Event(), threading.Event()
    real_export = controller.run_export

    def held_export(*args, **kwargs):
        inside.set()
        release.wait(30)
        return real_export(*args, **kwargs)

    monkeypatch.setattr(controller, "run_export", held_export)
    c.export(tmp_path / "out2", "jpeg")
    wait_until(c, lambda: inside.is_set())
    c.apply_meta_edit("batch", "description", "After the export")
    wait_until(c, lambda: c.state().metadata_pending > 0)
    for _ in range(30):
        c.tick()
    assert not c._meta_update_active and c._meta_update_due  # waiting for the export
    release.set()
    wait_until(c, lambda: c.state().metadata_pending == 0)


def test_the_owner_stop_says_it_is_stopped_and_the_stop_button_follows_the_state(
    auto_exported, gated
):
    c = auto_exported
    assert c.state().metadata_updating is False
    _caught_midway(c, gated)
    wait_until(c, lambda: c.state().metadata_updating)
    c.cancel_metadata_update()
    gated.gate.set()
    wait_until(c, lambda: not c.state().metadata_updating)
    wait_until(c, lambda: c.state().banner is not None and "stopped" in c.state().banner.message)
    assert "press Update JPEGs" in c.state().banner.message


def test_a_stop_pressed_while_the_manual_update_is_still_queued_is_not_lost(exported):
    c = exported
    c.apply_meta_edit("batch", "description", "Queued")
    wait_until(c, lambda: c.state().metadata_pending == 4)
    c.update_exported_metadata()
    c.cancel_metadata_update()  # the job has not started: the stop must still count
    wait_until(
        c, lambda: c.state().banner is not None and "updated in 0" in c.state().banner.message
    )
    assert c.state().metadata_pending == 4


def _receipts_match_files(c):
    manifest = c._session.store.snapshot()
    records = [r for t in manifest.trays for s in t.slides for r in s.exports[-1:]]
    return records and all(
        hashlib.sha256(Path(r.export_path).read_bytes()).hexdigest() == r.sha256
        and not r.pending_sha256
        for r in records
    )


def test_a_crash_after_the_rename_is_recovered_and_the_receipt_finished(exported, monkeypatch):
    from opticfilm_linux.batch import meta_sync

    c = exported
    c.apply_meta_edit("batch", "description", "Crash")
    wait_until(c, lambda: c.state().metadata_pending == 4)
    real = meta_sync._store_receipt

    def dies_after_the_rename(store, key, old, new):
        if not new.pending_sha256:  # the final receipt, after the file was replaced
            raise OSError("power cut")
        return real(store, key, old, new)

    monkeypatch.setattr(meta_sync, "_store_receipt", dies_after_the_rename)
    c.update_exported_metadata()
    wait_until(
        c, lambda: c.state().banner is not None and "not changed" in c.state().banner.message
    )
    monkeypatch.setattr(meta_sync, "_store_receipt", real)
    records = [t.slides[0].exports[-1] for t in c._session.store.snapshot().trays]
    assert records[0].pending_sha256  # the plan was written before the file was replaced
    assert not _receipts_match_files(c)  # the files are new, the receipts are not finished
    leftover = Path(records[0].export_path)
    leftover = leftover.with_name(f".{leftover.name}.0123456789ab.tmp")
    leftover.write_bytes(b"killed between creating and renaming")
    os.utime(leftover, (1, 1))
    c.update_exported_metadata()  # recovery: the file is the planned one
    wait_until(c, lambda: c.state().metadata_pending == 0)
    assert _receipts_match_files(c)
    assert not leftover.exists()  # the stale temporary file of an interrupted rewrite is gone


def test_after_a_crash_a_foreign_edit_is_still_refused(exported, monkeypatch, tmp_path):
    from opticfilm_linux.batch import meta_sync

    c = exported
    c.apply_meta_edit("batch", "description", "Foreign")
    wait_until(c, lambda: c.state().metadata_pending == 4)
    real = meta_sync._store_receipt
    monkeypatch.setattr(
        meta_sync,
        "_store_receipt",
        lambda store, key, old, new: (
            real(store, key, old, new)
            if new.pending_sha256
            else (_ for _ in ()).throw(OSError("power cut"))
        ),
    )
    c.update_exported_metadata()
    wait_until(
        c, lambda: c.state().banner is not None and "not changed" in c.state().banner.message
    )
    monkeypatch.setattr(meta_sync, "_store_receipt", real)
    victim = sorted(_jpegs(tmp_path))[0]
    victim.write_bytes(victim.read_bytes() + b"edited by another program")
    edited = victim.read_bytes()
    c.update_exported_metadata()
    wait_until(
        c,
        lambda: (
            c.state().banner is not None and c.state().banner.message.endswith("1 not changed.")
        ),
    )
    assert victim.read_bytes() == edited and c.state().metadata_pending == 1


def test_a_failed_second_rewrite_after_a_crash_keeps_the_file_updatable(
    exported, monkeypatch, tmp_path
):
    from opticfilm_linux.batch import meta_sync

    c = exported
    c.apply_meta_edit("batch", "description", "Crash")
    wait_until(c, lambda: c.state().metadata_pending == 4)
    real_store = meta_sync._store_receipt
    monkeypatch.setattr(
        meta_sync,
        "_store_receipt",
        lambda store, key, old, new: (
            real_store(store, key, old, new)
            if new.pending_sha256
            else (_ for _ in ()).throw(OSError("power cut"))
        ),
    )
    c.update_exported_metadata()  # every file is replaced, no receipt is finished
    wait_until(c, lambda: c.state().banner is not None and "power cut" in c.state().banner.detail)
    monkeypatch.setattr(meta_sync, "_store_receipt", real_store)

    c.apply_meta_edit("batch", "description", "Second")  # the owner edits again
    wait_until(c, lambda: c.state().undo_label == "Edit metadata")
    real_rewrite = meta_sync.rewrite_xmp_file

    def fails_before_the_rename(*args, before_replace=None, **kwargs):
        def plan_then_fail(digest, size):
            before_replace(digest, size)
            raise OSError("disk full")

        return real_rewrite(*args, before_replace=plan_then_fail, **kwargs)

    monkeypatch.setattr(meta_sync, "rewrite_xmp_file", fails_before_the_rename)
    c.update_exported_metadata()
    wait_until(c, lambda: c.state().banner is not None and "disk full" in c.state().banner.detail)
    monkeypatch.setattr(meta_sync, "rewrite_xmp_file", real_rewrite)

    victim = sorted(_jpegs(tmp_path))[0]
    victim.write_bytes(victim.read_bytes() + b"edited by another program")
    c.update_exported_metadata()  # the other three are still accepted and updated
    wait_until(
        c,
        lambda: (
            c.state().banner is not None
            and c.state().banner.message.endswith("3 JPEGs. 1 not changed.")
        ),
    )
    assert c.state().metadata_pending == 1  # the foreign edit is still refused
    others = [p for p in _jpegs(tmp_path) if p != victim]
    assert all(b"Second" in p.read_bytes() for p in others)


def test_a_queued_update_cancelled_by_shutdown_leaves_the_idle_event_set(auto_exported):
    c = auto_exported
    blocker = threading.Event()
    c._meta_pool.submit(blocker.wait, 20)  # the worker is busy: the update only queues
    c.apply_meta_edit("batch", "description", "Never runs")
    wait_until(c, lambda: c._meta_update_active)
    assert not c._meta_idle.is_set()
    c._meta_pool.shutdown(wait=False, cancel_futures=True)
    wait_until(c, lambda: c._meta_idle.is_set() and not c._meta_update_active)
    blocker.set()
