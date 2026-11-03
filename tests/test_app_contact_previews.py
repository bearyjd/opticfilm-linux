"""Preview versions are discovered on workers only when slide events need them."""

import dataclasses
from types import SimpleNamespace
from unittest.mock import Mock

from opticfilm_linux.app.controller import Controller
from opticfilm_linux.app.preview_tokens import PreviewTokens
from opticfilm_linux.batch.events import SlideUpdated
from opticfilm_linux.batch.model import BatchManifest, SlideRecord, TrayRecord, TrayStage


def _manifest():
    return BatchManifest(
        "Sheet",
        trays=tuple(
            TrayRecord(n, TrayStage.REVIEW, slides=(SlideRecord(1), SlideRecord(2))) for n in (1, 2)
        ),
    )


def test_cache_reads_every_tray_once_then_only_updated_slide(tmp_path):
    paths = {}
    for number in (1, 2):
        for position in (1, 2):
            for kind in ("before", "after"):
                path = tmp_path / f"{number}-{position}-{kind}.jpg"
                path.write_bytes(b"preview")
                paths[number, position, kind] = path
    store = Mock()
    store.preview_path.side_effect = lambda *key: paths.get(key, tmp_path / "missing.jpg")
    cache = PreviewTokens()
    manifest = _manifest()
    first = cache.read(store, manifest)
    assert len(first) == 8
    assert store.preview_path.call_count == 8
    assert cache.read(store, manifest) == first
    assert store.preview_path.call_count == 8
    cache.invalidate(2, 1)
    paths[2, 1, "after"].unlink()
    second = cache.read(store, manifest)
    assert (2, 1) not in second and (2, 1, "before") in second
    assert store.preview_path.call_count == 10
    added = TrayRecord(3, TrayStage.SCANNING, slides=(SlideRecord(1),))
    cache.read(store, dataclasses.replace(manifest, trays=manifest.trays + (added,)))
    assert store.preview_path.call_count == 12


def test_slide_updated_invalidates_preview_cache_without_disk_access():
    controller = Controller(lambda _: None)
    try:
        previews = Mock()
        controller._session = SimpleNamespace(previews=previews)
        with controller._lock:
            controller._apply_event(SlideUpdated(7, 3))
        previews.invalidate.assert_called_once_with(7, 3)
    finally:
        controller._session = None
        controller.shutdown()
