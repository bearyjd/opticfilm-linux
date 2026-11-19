"""Explicit owner recovery of intact raw after a committed cleanup was interrupted.

Recovery never deletes scans or trusts the removal marker as evidence. Recorded folder
membership must be a subset of the recorded cleanup, when available. Legacy manifests
without that inventory may contain only sweep versions up to the recorded current sweep.
The current sweep must decode and split, and any available current raw hashes must match.
Older sweeps may remain partly
deleted; recovery preserves those folders without treating them as current raw evidence.
Legacy scans without hashes need their configured infrared plane;
missing raw cannot be restored by clearing a manifest marker. Delivery files may be missing:
recovery returns the tray to review and requires fresh exports and release approval.
"""

import dataclasses
import weakref
from collections.abc import Callable
from pathlib import Path

from opticfilm_linux.batch.cleanup_state import (
    CLEANUP_LOCK,
    PENDING_CLEANUP_RESETS,
    CleanupPolicyRefused,
    CleanupRefused,
    CleanupRetryRequired,
    ExportVerificationFailed,
    checked_sweep_folders,
    checked_sweep_versions,
    tray_of,
)
from opticfilm_linux.batch.empty import check_raw_coverage, scanned_slides
from opticfilm_linux.batch.export_contract import _release_candidate, _release_defaults
from opticfilm_linux.batch.metadata import without_meta
from opticfilm_linux.batch.model import BatchManifest, SlideStatus, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchError, BatchStore
from opticfilm_linux.pipeline.split import split_positions
from opticfilm_linux.provenance import raw_region_sha256
from opticfilm_linux.sweepstore import load_sweep

_RECOVERY_CHECKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _recovery_inventory(store: BatchStore, tray: TrayRecord) -> tuple[Path, ...]:
    if not tray.raw_removed:
        raise ValueError("no recorded interrupted cleanup")
    folders = tuple(checked_sweep_folders(store, tray.number))
    checked_sweep_versions(tray, folders)
    versions = tuple(int(path.name.removeprefix("sweep-")) for path in folders)
    if len(set(versions)) != len(versions):
        raise ValueError("additional raw sweep folders have duplicate versions")
    if tray.cleanup_sweeps and tray.sweep_version not in tray.cleanup_sweeps:
        raise ValueError("current raw sweep is absent from the recorded cleanup inventory")
    for path in folders:
        if any(child.is_symlink() for child in path.rglob("*")):
            raise ValueError("raw sweep contains a symlink")
    return folders


def interrupted_cleanup_guidance(number: int, *, recoverable: bool) -> str:
    if recoverable:
        return (
            f"Tray {number}: select Verify remaining scans… to recover intact raw, then "
            "review and re-export before freeing raw scans."
        )
    return (
        f"Tray {number}: remaining raw could not be verified. Restore damaged or missing "
        "current raw from its original sweep folders, then select Verify remaining scans…, "
        "review and re-export; otherwise rescan this tray."
    )


def _check_interrupted_cleanup(
    store: BatchStore,
    tray: TrayRecord,
    checked_snapshot: Callable[[BatchStore, TrayRecord, tuple[Path, ...]], None],
) -> None:
    try:
        folders = tuple(checked_sweep_folders(store, tray.number))
        checked_snapshot(store, tray, folders)
    except (OSError, ValueError, BatchError) as exc:
        if tray.cleanup_sweeps:
            raise
        message = (
            f"Cannot finish cleanup for tray {tray.number}: {exc}. Select Verify remaining "
            "scans… to recover intact raw, then review and re-export. If verification fails, "
            "restore the original raw sweep folders or rescan this tray."
        )
        if isinstance(exc, ExportVerificationFailed):
            raise ExportVerificationFailed(tray.number, exc.position, ValueError(message)) from exc
        raise CleanupPolicyRefused(message) from exc


def can_recover_interrupted_cleanup(
    store: BatchStore, manifest: BatchManifest, tray: TrayRecord
) -> bool:
    """Check recovery for the window, caching only while raw filesystem evidence is unchanged."""
    try:
        folders = _recovery_inventory(store, tray)
        members = tuple(sorted(path for folder in folders for path in (folder, *folder.rglob("*"))))
        stats = tuple(
            (path, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            for path in members
            for st in (path.lstat(),)
        )
        key = (tray, manifest.defaults, stats)
        cache = _RECOVERY_CHECKS.setdefault(store, {})
        if tray.number not in cache or cache[tray.number][0] != key:
            try:
                _recovery_raw(store, manifest, tray, folders)
            except (OSError, ValueError, CleanupRefused):
                cache[tray.number] = (key, False)
            else:
                cache[tray.number] = (key, True)
        return cache[tray.number][1]
    except (OSError, ValueError, CleanupRefused):
        return False


def _recheck_inventory(store: BatchStore, tray: TrayRecord, folders: tuple[Path, ...]) -> None:
    try:
        if _recovery_inventory(store, tray) != folders:
            raise ValueError("raw sweep folders changed meanwhile")
    except (OSError, ValueError, CleanupRefused) as exc:
        raise CleanupRetryRequired(
            f"tray {tray.number} raw folders changed meanwhile; retry recovery"
        ) from exc


def _recovery_raw(store: BatchStore, manifest: BatchManifest, tray: TrayRecord, folders) -> None:
    current = store.sweep_dir(tray.number, tray.sweep_version)
    if current not in folders:
        raise ValueError("current raw sweep is missing")
    sweep = load_sweep(current)
    regions = split_positions(sweep)
    check_raw_coverage(tray, regions)
    for slide in scanned_slides(tray):
        expected = (
            slide.blank_fingerprint
            if slide.status is SlideStatus.BLANK
            else (slide.exports[-1].raw_sha256 if slide.exports else "")
        )
        if expected:
            if raw_region_sha256(regions[slide.position]) != expected:
                raise ValueError("current raw scan differs from its recorded hash")
        elif manifest.defaults.infrared and sweep.ir is None:
            raise ValueError("configured infrared raw scan is missing")


def _recovered_tray(tray: TrayRecord, *, automatic: bool) -> TrayRecord:
    slides = tuple(
        dataclasses.replace(
            slide,
            exported=False,
            export_path="",
            export_size=(),
            export_sha256="",
            empty_confirmed=None,
        )
        for slide in tray.slides
    )
    return dataclasses.replace(
        tray,
        raw_removed=False,
        cleanup_sweeps=(),
        stage=TrayStage.REVIEW,
        slides=slides,
        auto=("pending" if automatic else "held") if tray.auto not in ("", "off") else tray.auto,
        auto_error="",
        auto_failed=(),
    )


def recover_interrupted_cleanup(store: BatchStore, number: int) -> None:
    """Return verified current raw to review after an explicit owner action."""
    with CLEANUP_LOCK:
        evidence = store.snapshot()
        tray = next((t for t in evidence.trays if t.number == number), None)
        if tray is None:
            raise CleanupPolicyRefused(f"no tray {number} in this batch")
        try:
            folders = _recovery_inventory(store, tray)
            _recovery_raw(store, evidence, tray, folders)
            if _recovery_inventory(store, tray) != folders:
                raise ValueError("raw sweep folders changed meanwhile")
        except (OSError, ValueError, CleanupRefused) as exc:
            raise CleanupPolicyRefused(
                f"Cannot recover tray {number}: {exc}; restore the damaged or missing raw "
                "sweep folders or rescan this tray."
            ) from exc

        def reset(manifest: BatchManifest) -> BatchManifest:
            _compare_cleanup_evidence(manifest, evidence, number)
            _recheck_inventory(store, tray, folders)
            return dataclasses.replace(
                manifest,
                first_tray_released=False,
                released_render=None,
                trays=tuple(
                    _recovered_tray(t, automatic=manifest.defaults.auto_export)
                    if t.number == number
                    else t
                    for t in manifest.trays
                ),
            )

        store.update(reset)
        PENDING_CLEANUP_RESETS.get(store, {}).pop(number, None)


def _record_cleanup_complete(store: BatchStore, tray: TrayRecord) -> None:
    """Clear automatic progress only while the cleanup's recorded owner is unchanged."""
    if tray.auto in ("", "off"):
        return
    store.update_tray_if(
        tray.number,
        lambda current: (
            current.raw_removed
            and (current.auto, current.auto_error, current.auto_failed)
            == (tray.auto, tray.auto_error, tray.auto_failed)
        ),
        auto="freed",
        auto_error="",
        auto_failed=(),
    )


def _compare_cleanup_evidence(
    manifest: BatchManifest, evidence: BatchManifest, number: int, *, release_first: bool = False
) -> None:
    def policy_key(batch: BatchManifest) -> tuple:
        automatic = batch.defaults.auto_export or any(
            t.auto not in ("", "off") for t in batch.trays
        )
        released = batch.first_tray_released and (
            batch.released_render == _release_defaults(batch.defaults)
        )
        candidate = (
            _release_candidate(batch, number)
            if (release_first or (automatic and not released))
            else None
        )
        return (
            batch.defaults,
            batch.first_tray_released,
            batch.released_render,
            without_meta(tray_of(batch, number)),  # a description edit needs no retry
            candidate,
            automatic,
            tuple(without_meta(t) for t in batch.trays if t.number != number and t.raw_removed),
        )

    if policy_key(manifest) != policy_key(evidence):
        raise CleanupRetryRequired("batch changed meanwhile; retry cleanup with current settings")


def _cleanup_listable(
    store: BatchStore,
    number: int,
    *,
    check_allowed: Callable[..., TrayRecord],
    release_first: bool,
) -> bool:
    try:
        check_allowed(store, number, release_first=release_first)
    except (OSError, BatchError):
        return False
    return True
