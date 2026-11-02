"""Explicit owner recovery of intact raw after a committed cleanup was interrupted.

Recovery never deletes scans or trusts the removal marker as evidence. Recorded folder
membership must be a subset of the recorded cleanup, every remaining sweep must decode and
split, and any available current
raw hashes must match. Legacy scans without hashes need their configured infrared plane;
missing raw cannot be restored by clearing a manifest marker. Delivery files may be missing:
recovery returns the tray to review and requires fresh exports and release approval.
"""

import dataclasses
import weakref
from pathlib import Path

from opticfilm_linux.batch.model import BatchManifest, SlideStatus, TrayRecord, TrayStage
from opticfilm_linux.batch.store import BatchStore
from opticfilm_linux.provenance import raw_region_sha256

_RECOVERY_CHECKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _recovery_inventory(store: BatchStore, tray: TrayRecord) -> tuple[Path, ...]:
    from opticfilm_linux.batch import export

    if not tray.raw_removed or not tray.cleanup_sweeps:
        raise ValueError("no recorded interrupted cleanup inventory")
    folders = tuple(export._checked_sweep_folders(store, tray.number))
    export._checked_sweep_versions(tray, folders)
    versions = tuple(int(path.name.removeprefix("sweep-")) for path in folders)
    if len(set(versions)) != len(versions):
        raise ValueError("additional raw sweep folders have duplicate versions")
    if tray.sweep_version not in tray.cleanup_sweeps:
        raise ValueError("current raw sweep is absent from the recorded cleanup inventory")
    for path in folders:
        if any(child.is_symlink() for child in path.rglob("*")):
            raise ValueError("raw sweep contains a symlink")
    return folders


def can_recover_interrupted_cleanup(
    store: BatchStore, manifest: BatchManifest, tray: TrayRecord
) -> bool:
    """Check recovery for the window, caching only while raw filesystem evidence is unchanged."""
    from opticfilm_linux.batch import export

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
            except (OSError, ValueError, export.CleanupRefused):
                cache[tray.number] = (key, False)
            else:
                cache[tray.number] = (key, True)
        return cache[tray.number][1]
    except (OSError, ValueError, export.CleanupRefused):
        return False


def _recheck_inventory(store: BatchStore, tray: TrayRecord, folders: tuple[Path, ...]) -> None:
    from opticfilm_linux.batch import export

    try:
        if _recovery_inventory(store, tray) != folders:
            raise ValueError("raw sweep folders changed meanwhile")
    except (OSError, ValueError, export.CleanupRefused) as exc:
        raise export.CleanupRetryRequired(
            f"tray {tray.number} raw folders changed meanwhile; retry recovery"
        ) from exc


def _recovery_raw(store: BatchStore, manifest: BatchManifest, tray: TrayRecord, folders) -> None:
    from opticfilm_linux.batch import export

    current = store.sweep_dir(tray.number, tray.sweep_version)
    if current not in folders:
        raise ValueError("current raw sweep is missing")
    for path in folders:
        sweep = export.load_sweep(path)
        regions = export.split_slides(sweep)
        if path != current:
            if manifest.defaults.infrared and sweep.ir is None:
                raise ValueError("configured infrared raw scan is missing from an older sweep")
            continue
        positions = [slide.position for slide in tray.slides]
        if (
            any(type(position) is not int for position in positions)
            or len(positions) != len(regions)
            or set(positions) != set(range(1, len(regions) + 1))
        ):
            raise ValueError("manifest positions must cover every raw position exactly once")
        for slide in tray.slides:
            expected = (
                slide.blank_fingerprint
                if slide.status is SlideStatus.BLANK
                else (slide.exports[-1].raw_sha256 if slide.exports else "")
            )
            if expected:
                if raw_region_sha256(regions[slide.position - 1]) != expected:
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
    from opticfilm_linux.batch import export

    with export._CLEANUP_LOCK:
        evidence = store.snapshot()
        tray = next((t for t in evidence.trays if t.number == number), None)
        if tray is None:
            raise export.CleanupPolicyRefused(f"no tray {number} in this batch")
        try:
            folders = _recovery_inventory(store, tray)
            _recovery_raw(store, evidence, tray, folders)
            if _recovery_inventory(store, tray) != folders:
                raise ValueError("raw sweep folders changed meanwhile")
        except (OSError, ValueError, export.CleanupRefused) as exc:
            raise export.CleanupPolicyRefused(
                f"Cannot recover tray {number}: {exc}; restore the damaged or missing raw "
                "sweep folders or rescan this tray."
            ) from exc

        def reset(manifest: BatchManifest) -> BatchManifest:
            export._compare_cleanup_evidence(manifest, evidence)
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
        export._PENDING_CLEANUP_RESETS.get(store, {}).pop(number, None)


def _cleanup_listable(store: BatchStore, number: int, *, release_first: bool) -> bool:
    from opticfilm_linux.batch import export

    try:
        export.check_cleanup_allowed(store, number, release_first=release_first)
    except (OSError, export.BatchError):
        return False
    return True
