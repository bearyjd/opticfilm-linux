import dataclasses
import json

import pytest

from opticfilm_linux.batch.empty import is_empty_confirmed
from opticfilm_linux.batch.enhance import _merge_slide
from opticfilm_linux.batch.manifest import ManifestFormatError, from_json, to_json
from opticfilm_linux.batch.model import (
    BatchManifest,
    EmptyConfirmation,
    SlideRecord,
    SlideSettings,
    SlideStatus,
    TrayRecord,
    TrayStage,
)
from opticfilm_linux.batch.review import (
    accept_tray,
    apply_slide_settings,
    confirm_empty_frames,
    set_slide_status,
    set_tray_mode,
)
from opticfilm_linux.batch.store import BatchError, BatchStore


@pytest.fixture
def store(tmp_path):
    store = BatchStore.create(tmp_path / "batch", name="Empty evidence")
    slide = SlideRecord(1, status=SlideStatus.BLANK, blank_fingerprint="a" * 64)
    tray = TrayRecord(1, TrayStage.REVIEW, slides=(slide,))
    store.update(lambda manifest: dataclasses.replace(manifest, trays=(tray,)))
    yield store
    store.close()


def _confirm(store, **changes):
    values = {"expected_fingerprints": {1: "a" * 64}, "expected_sweep_version": 1}
    values.update(changes)
    confirm_empty_frames(store, 1, (1,), **values)


def test_confirmation_is_separate_from_owner_review_and_roundtrips(store):
    _confirm(store)
    tray = store.snapshot().trays[0]
    assert is_empty_confirmed(tray, tray.slides[0])
    assert not tray.slides[0].owner_decided
    assert from_json(to_json(store.snapshot())) == store.snapshot()


@pytest.mark.parametrize("change", [{"sweep_version": 2}, {"mode": "negative"}])
def test_confirmation_is_invalidated_by_new_sweep_or_mode(store, change):
    _confirm(store)
    if "mode" in change:
        set_tray_mode(store, 1, change["mode"])
    else:
        store.update_tray(1, **change)
    tray = store.snapshot().trays[0]
    assert not is_empty_confirmed(tray, tray.slides[0])


def test_sweep_change_clears_evidence_and_reverting_cannot_revive_it(store):
    _confirm(store)
    original = store.snapshot().trays[0].slides[0]
    store.update_tray(1, sweep_version=2)
    slide = store.snapshot().trays[0].slides[0]
    assert slide.empty_confirmed is None and slide.blank_fingerprint == ""
    store.update_tray(1, sweep_version=1)
    tray = store.snapshot().trays[0]
    assert tray.slides[0].empty_confirmed is None
    assert not is_empty_confirmed(tray, tray.slides[0])
    # Installing even a new-version marker in the same sweep mutation is refused.
    replacement = dataclasses.replace(original, empty_confirmed=EmptyConfirmation(2, "a" * 64))
    store.update_tray(1, sweep_version=2, slides=(replacement,))
    slide = store.snapshot().trays[0].slides[0]
    assert slide.empty_confirmed is None and slide.blank_fingerprint == ""


@pytest.mark.parametrize("mutation", ["position", "fingerprint", "version"])
def test_stale_confirmation_refuses_changed_frames_atomically(store, mutation):
    tray = store.snapshot().trays[0]
    if mutation == "version":
        store.update_tray(1, sweep_version=2)
    else:
        changes = {"position": 2} if mutation == "position" else {"blank_fingerprint": "b" * 64}
        store.update_tray(1, slides=(dataclasses.replace(tray.slides[0], **changes),))
    before = store.snapshot()
    with pytest.raises(BatchError, match="changed"):
        _confirm(store)
    assert store.snapshot() == before


def test_new_blank_added_while_dialog_open_is_not_confirmed(store):
    tray = store.snapshot().trays[0]
    added = SlideRecord(2, status=SlideStatus.BLANK, blank_fingerprint="b" * 64)
    store.update_tray(1, slides=tray.slides + (added,))
    with pytest.raises(BatchError, match="changed"):
        _confirm(store)
    assert all(slide.empty_confirmed is None for slide in store.snapshot().trays[0].slides)


def test_missing_or_legacy_blank_evidence_cannot_confirm(store):
    tray = store.snapshot().trays[0]
    legacy = dataclasses.replace(tray.slides[0], blank_fingerprint="", owner_decided=True)
    store.update_tray(1, slides=(legacy,))
    with pytest.raises(BatchError):
        _confirm(store, expected_fingerprints={1: ""})
    assert not is_empty_confirmed(store.snapshot().trays[0], legacy)


def test_blank_settings_and_acceptance_do_not_create_confirmation(store):
    with pytest.raises(BatchError, match="blank"):
        apply_slide_settings(store, 1, 1, SlideSettings(rotation=90))
    accept_tray(store, 1, automatic=True)
    slide = store.snapshot().trays[0].slides[0]
    assert slide.status is SlideStatus.BLANK and not slide.owner_decided
    assert slide.empty_confirmed is None


def test_rescuing_blank_clears_confirmation(store):
    _confirm(store)
    set_slide_status(store, 1, 1, SlideStatus.ACCEPTED)
    slide = store.snapshot().trays[0].slides[0]
    assert slide.owner_decided and slide.empty_confirmed is None


def test_content_reclassification_preserves_owned_blank_and_clears_confirmation(store):
    _confirm(store)
    tray = store.snapshot().trays[0]
    legacy = dataclasses.replace(tray.slides[0], owner_decided=True)
    store.update_tray(1, slides=(legacy,))
    _merge_slide(store, 1, tray, legacy, (), False, store.snapshot().defaults)
    slide = store.snapshot().trays[0].slides[0]
    assert slide.status is SlideStatus.BLANK
    assert slide.owner_decided and slide.empty_confirmed is None
    assert not slide.blank_fingerprint


@pytest.mark.parametrize("protection", ["auto_accepted", "exported", "owner_decided"])
def test_late_blank_cannot_replace_reviewed_or_exported_content(store, protection):
    tray = store.snapshot().trays[0]
    content = dataclasses.replace(tray.slides[0], status=SlideStatus.ACCEPTED, **{protection: True})
    store.update_tray(1, slides=(content,))
    _merge_slide(store, 1, tray, tray.slides[0], (), True, store.snapshot().defaults)
    assert store.snapshot().trays[0].slides[0] == content


def test_confirmation_parser_rejects_malformed_evidence():
    manifest = BatchManifest(
        "bad", trays=(TrayRecord(1, TrayStage.REVIEW, slides=(SlideRecord(1),)),)
    )
    data = json.loads(to_json(manifest))
    data["trays"][0]["slides"][0]["empty_confirmed"] = {
        "sweep_version": True,
        "fingerprint": "a" * 64,
    }
    with pytest.raises(ManifestFormatError):
        from_json(json.dumps(data))
    with pytest.raises(ValueError):
        EmptyConfirmation(1, "not-a-digest")
