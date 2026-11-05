"""Turning or flipping a slide turns its existing preview at once instead of re-enhancing it."""

import dataclasses
import shutil
import threading

import numpy as np
import pytest
from PIL import Image

from opticfilm_linux.batch import reorient as reorient_module
from opticfilm_linux.batch import runner as runner_module
from opticfilm_linux.batch.enhance import reenhance_slide
from opticfilm_linux.batch.events import SlideUpdated, TrayFailed
from opticfilm_linux.batch.export import export_batch
from opticfilm_linux.batch.model import BatchDefaults, SlideSettings, SlideStatus
from opticfilm_linux.batch.previews import write_preview
from opticfilm_linux.batch.runner import BatchRunner
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.scanner.fake import FakeScanner

TIMEOUT = 120
TOLERANCE = 2.0  # mean absolute difference, 8-bit levels: one extra JPEG generation


@pytest.fixture(scope="module")
def template(tmp_path_factory, sweep600_dir):
    """Two trays (eight slides) scanned and enhanced, in review."""
    root = tmp_path_factory.mktemp("turn") / "batch"
    store = BatchStore.create(root, name="T", defaults=BatchDefaults(dpi=600))
    runner = BatchRunner(store, FakeScanner(sweep600_dir))
    runner.scan_tray()
    assert runner.wait_scans_idle(TIMEOUT)
    runner.scan_tray()
    assert runner.wait_idle(TIMEOUT)
    runner.close()
    store.close()
    return root


@pytest.fixture
def store(tmp_path, template):
    shutil.copytree(template, tmp_path / "b")
    with BatchStore.open(tmp_path / "b") as opened:
        yield opened


class _Renders(list):
    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.gate.set()


@pytest.fixture
def renders(monkeypatch):
    """Every full re-enhance the runner starts, by key; clear ``calls.gate`` to hold them."""
    calls = _Renders()

    def recording(store, number, position):
        calls.append((number, position))
        assert calls.gate.wait(TIMEOUT)
        reenhance_slide(store, number, position)

    monkeypatch.setattr(runner_module, "reenhance_slide", recording)
    return calls


@pytest.fixture
def runner(store, sweep600_dir, renders):
    opened = BatchRunner(store, FakeScanner(sweep600_dir))
    yield opened
    opened.close()


def _slide(store, number, position):
    tray = next(t for t in store.snapshot().trays if t.number == number)
    return next(s for s in tray.slides if s.position == position)


def _keys(store):
    return [(t.number, s.position) for t in store.snapshot().trays for s in t.slides]


def _pixels(store, number, position, kind="after"):
    with Image.open(store.preview_path(number, position, kind)) as image:
        return np.asarray(image.convert("RGB")).astype(np.float32)


def _events(runner):
    found = []
    while not runner.events.empty():
        found.append(runner.events.get_nowait())
    return found


def _rerendered(store, number, position):
    """What a full re-enhance makes of the slide with its stored settings."""
    reenhance_slide(store, number, position)
    return _pixels(store, number, position)


def _assert_matches_full_render(store, number, position):
    turned = _pixels(store, number, position)
    full = _rerendered(store, number, position)
    assert turned.shape == full.shape
    assert float(np.abs(turned - full).mean()) < TOLERANCE


@pytest.mark.parametrize(
    "settings",
    [
        SlideSettings(rotation=90),
        SlideSettings(rotation=180),
        SlideSettings(rotation=270, mirror=True),
        SlideSettings(mirror=True),
    ],
)
def test_turning_one_slide_matches_a_full_re_enhance(store, runner, renders, settings):
    before = _pixels(store, 1, 2, "before")
    runner.edit_slide(1, 2, settings)
    slide = _slide(store, 1, 2)
    assert slide.settings == settings and not slide.preview_stale
    assert renders == []  # no re-enhance was started: the turn is done already
    assert SlideUpdated(1, 2) in _events(runner)
    assert np.array_equal(_pixels(store, 1, 2, "before"), before)  # the raw view is never turned
    _assert_matches_full_render(store, 1, 2)


def test_turning_keeps_status_flags_and_owner_fields(store, runner):
    old = _slide(store, 1, 1)
    runner.edit_slide(1, 1, dataclasses.replace(old.settings, rotation=90))
    new = _slide(store, 1, 1)
    assert (new.status, new.flags, new.owner_decided) == (old.status, old.flags, True)
    assert new.blank_fingerprint == old.blank_fingerprint


def test_four_quick_turns_end_where_they_started(store, runner, renders):
    start = _pixels(store, 1, 3)
    for _ in range(4):  # as the app does: each turn reads what is stored now
        current = _slide(store, 1, 3).settings
        runner.edit_slide(
            1, 3, dataclasses.replace(current, rotation=(current.rotation + 90) % 360)
        )
    assert _slide(store, 1, 3).settings.rotation == 0 and renders == []
    assert float(np.abs(_pixels(store, 1, 3) - start).mean()) < 2 * TOLERANCE


def test_other_changes_keep_the_background_re_enhance(store, runner, renders):
    runner.edit_slide(1, 1, SlideSettings(rotation=90, brightness_ev=0.5))
    assert runner.wait_idle(TIMEOUT)
    assert renders == [(1, 1)]


def test_a_stale_preview_is_re_enhanced_not_turned(store, runner, renders):
    renders.gate.clear()  # the first re-enhance cannot finish before the turn arrives
    runner.edit_slide(1, 1, SlideSettings(brightness_ev=0.5))
    runner.edit_slide(1, 1, SlideSettings(rotation=90, brightness_ev=0.5))
    renders.gate.set()
    assert runner.wait_idle(TIMEOUT)
    assert renders == [(1, 1), (1, 1)] and not _slide(store, 1, 1).preview_stale
    _assert_matches_full_render(store, 1, 1)


def test_a_missing_preview_is_re_enhanced(store, runner, renders):
    store.preview_path(1, 1, "after").unlink()
    runner.edit_slide(1, 1, SlideSettings(rotation=90))
    assert runner.wait_idle(TIMEOUT)
    assert renders == [(1, 1)]


def _set_slide(store, key, **changes):
    def change(manifest):
        trays = tuple(
            dataclasses.replace(
                t,
                slides=tuple(
                    dataclasses.replace(s, **changes) if (t.number, s.position) == key else s
                    for s in t.slides
                ),
            )
            for t in manifest.trays
        )
        return dataclasses.replace(manifest, trays=trays)

    store.update(change)


def test_a_blank_slide_is_not_turned(store, runner):
    _set_slide(store, (1, 1), status=SlideStatus.BLANK)
    old = store.snapshot()
    _set_slide(store, (1, 1), settings=SlideSettings(rotation=90), preview_stale=True)
    assert runner._turn_previews(old, [(1, 1)]) == frozenset()
    assert _slide(store, 1, 1).preview_stale


def test_bulk_turn_of_eight_slides_turns_every_preview(store, runner, renders):
    keys = _keys(store)
    assert len(keys) == 8

    def turn(settings):
        step = -90 if settings.mirror else 90
        return dataclasses.replace(settings, rotation=(settings.rotation + step) % 360)

    result = runner.edit_many(keys, turn)
    result = runner.edit_many(keys, turn)
    assert runner.wait_idle(TIMEOUT)
    assert renders == [] and not result.skipped
    for key in keys:
        assert (
            _slide(store, *key).settings.rotation == 180 and not _slide(store, *key).preview_stale
        )
    _assert_matches_full_render(store, *keys[0])


def test_undoing_a_bulk_turn_turns_back(store, runner, renders):
    keys = _keys(store)
    result = runner.edit_many(keys, {"mirror": True})
    runner.restore_many(result.before)
    assert runner.wait_idle(TIMEOUT)
    assert renders == []
    assert all(not _slide(store, *k).settings.mirror for k in keys)
    _assert_matches_full_render(store, *keys[-1])


def test_batch_orientation_turns_every_preview(store, runner, renders):
    assert runner.set_batch_orientation(90, True) == ()
    assert runner.wait_idle(TIMEOUT)
    assert renders == []
    assert not any(s.preview_stale for t in store.snapshot().trays for s in t.slides)
    _assert_matches_full_render(store, 2, 4)


def test_a_failed_turn_keeps_the_old_preview_and_falls_back(store, runner, renders, monkeypatch):
    path = store.preview_path(1, 1, "after")
    original = path.read_bytes()

    def broken(rgb8, target):
        target.write_bytes(b"half")  # a partly written file must never be published
        raise OSError("disk full")

    monkeypatch.setattr(reorient_module, "write_preview", broken)
    renders.gate.clear()  # hold the fallback so the untouched preview can be checked
    runner.edit_slide(1, 1, SlideSettings(rotation=90))
    assert any(isinstance(e, TrayFailed) and "disk full" in e.message for e in _events(runner))
    assert path.read_bytes() == original
    assert sorted(p.name for p in path.parent.glob("01-after*")) == ["01-after.jpg"]
    renders.gate.set()
    assert runner.wait_idle(TIMEOUT)
    assert renders == [(1, 1)]  # the full re-enhance still brings the preview up to date
    assert not _slide(store, 1, 1).preview_stale


def test_rotate_then_export_renders_from_raw_with_the_new_orientation(store, runner, tmp_path):
    store.update(
        lambda m: dataclasses.replace(
            m,
            trays=tuple(
                dataclasses.replace(
                    t,
                    slides=tuple(
                        dataclasses.replace(s, status=SlideStatus.ACCEPTED) for s in t.slides
                    ),
                )
                for t in m.trays
            ),
        )
    )
    with Image.open(store.preview_path(1, 1, "before")) as image:
        width, height = image.size  # the raw slide, as scanned
    runner.edit_slide(1, 1, SlideSettings(rotation=90))
    export_batch(store, tmp_path / "out", "jpeg", keys=frozenset({(1, 1)}))
    record = _slide(store, 1, 1)
    assert record.exported and record.exports
    written = record.exports[-1]
    assert (written.width > written.height) == (height > width)  # turned a quarter
    assert max(written.width, written.height) >= max(width, height)  # full size, not a preview


def test_a_full_size_preview_is_turned_without_a_re_enhance(store, runner, renders):
    """(e) A 1600 px preview, as a 7200 dpi scan makes: turned at once, nothing submitted."""
    rng = np.random.default_rng(3)
    big = rng.integers(0, 255, size=(1067, 1600, 3), dtype=np.uint8)
    write_preview(big, store.preview_path(1, 4, "after"))
    runner.edit_slide(1, 4, SlideSettings(rotation=270))
    assert renders == [] and not _slide(store, 1, 4).preview_stale
    assert not runner.work_pending()  # no re-enhance job was queued
    with Image.open(store.preview_path(1, 4, "after")) as image:
        assert image.size == (1067, 1600)
