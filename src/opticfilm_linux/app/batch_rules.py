"""Pure batch rules shared by the app and the command line (no Qt, no controller)."""

import os
import stat
import tempfile
from pathlib import Path

from opticfilm_linux.batch.layout import location_is_active
from opticfilm_linux.batch.model import BatchManifest
from opticfilm_linux.batch.store import BatchError


def check_export_directory(batch: Path, directory: Path) -> None:
    try:
        resolved = directory.resolve()
        batch = batch.resolve()
        for component in (directory, *directory.parents):
            if component.is_symlink() and not component.exists():
                raise BatchError(f"export folder {directory} contains a broken symbolic link")
    except (OSError, RuntimeError, ValueError) as exc:
        raise BatchError(f"cannot use export folder {directory}: {exc}") from exc
    if resolved.is_relative_to(batch):
        raise BatchError(f"export to a folder outside the batch folder, not {directory}")

    # Validate the closest existing parent without creating the user's destination.
    parent = resolved
    while True:
        try:
            mode = parent.stat().st_mode
            break
        except FileNotFoundError:
            parent = parent.parent
        except NotADirectoryError as exc:
            raise BatchError(f"export folder {directory} is not a directory") from exc
        except (OSError, ValueError) as exc:
            raise BatchError(f"cannot use export folder {directory}: {exc}") from exc
    if not stat.S_ISDIR(mode):
        raise BatchError(f"export folder {parent} is not a directory")
    if not mode & 0o222 or not os.access(parent, os.W_OK | os.X_OK):
        raise BatchError(f"export folder {parent} is not writable")
    try:
        with tempfile.TemporaryFile(dir=parent) as probe:
            probe.write(b"\0")
            probe.flush()
    except OSError as exc:
        raise BatchError(f"export folder {parent} is not writable: {exc}") from exc


def box_section_refusal(
    manifest: BatchManifest, box: int | None, section: str | None
) -> str | None:
    """Why a box/section change must wait, or None. The rule matches Finish box."""
    wanted_box = manifest.box if box is None else box
    wanted_section = manifest.section if section is None else section
    if (wanted_box, wanted_section) == (manifest.box, manifest.section):
        return None
    if not manifest.collection:
        return "this batch has no collection, so it has no box or section"
    if any(t.location is not None and location_is_active(manifest, t) for t in manifest.trays):
        return "the current section already has trays; finish the box or section in the app first"
    return None
