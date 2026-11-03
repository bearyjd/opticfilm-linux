"""Automatic progress never replaces a concurrently changed tray state."""

import dataclasses

import pytest

from opticfilm_linux.app import auto as automatic
from opticfilm_linux.batch import export as exports
from opticfilm_linux.batch.model import SlideStatus
from test_app_auto import pipeline

pytest_plugins = ("test_app_auto",)


def race_transition(store, monkeypatch, target):
    update = store.update_tray_if
    raced = []

    def racing_update(number, condition, **changes):
        if not raced and changes.get("auto") == target:
            raced.append(True)
            update(number, lambda t: True, auto="off", auto_error="owner changed progress")
        return update(number, condition, **changes)

    monkeypatch.setattr(store, "update_tray_if", racing_update)
    return raced


@pytest.mark.parametrize("target", ["exported", "verified", "held", "blocked", "failed", "off"])
def test_run_preserves_concurrent_progress_at_every_transition(store, monkeypatch, target):
    auto = pipeline(store)
    auto.arm(1)
    if target == "off":
        store.update(
            lambda m: dataclasses.replace(
                m, defaults=dataclasses.replace(m.defaults, auto_export=False)
            )
        )
    if target == "blocked":
        tray = auto._tray(1)
        slide = dataclasses.replace(
            tray.slides[0], status=SlideStatus.NEEDS_LOOK, owner_decided=True
        )
        store.update_tray(1, slides=(slide, *tray.slides[1:]))
    if target == "failed":
        monkeypatch.setattr(
            automatic,
            "export_batch",
            lambda *a, **k: (_ for _ in ()).throw(OSError("export failed")),
        )
    raced = race_transition(store, monkeypatch, target)
    auto.run(1)
    assert raced
    assert auto._tray(1).auto == "off"
    assert auto._tray(1).auto_error == "owner changed progress"
    assert store.sweep_dir(1, 1).exists()


@pytest.mark.parametrize("retry", [False, True])
def test_cleanup_completion_preserves_concurrent_progress(store, monkeypatch, retry):
    auto = pipeline(store)
    auto.arm(1)
    auto.run(1)

    def changed_cleanup(*args, **kwargs):
        store.update_tray(1, auto="off", auto_error="owner changed progress")
        if retry:
            raise exports.CleanupRetryRequired("cleanup must retry")

    monkeypatch.setattr(automatic, "cleanup_raw", changed_cleanup)
    auto.confirm_free_held(1)
    assert auto._tray(1).auto == "off"
    assert auto._tray(1).auto_error == "owner changed progress"
    assert store.sweep_dir(1, 1).exists()


def test_empty_hold_preserves_concurrent_progress(store, monkeypatch):
    auto = pipeline(store)
    tray = auto._tray(1)
    blank = dataclasses.replace(tray.slides[0], status=SlideStatus.BLANK)
    store.update_tray(1, slides=(blank, *tray.slides[1:]))
    auto.arm(1)
    raced = race_transition(store, monkeypatch, "held")
    auto.run(1)
    assert raced
    assert auto._tray(1).auto == "off"
    assert auto._tray(1).auto_error == "owner changed progress"
