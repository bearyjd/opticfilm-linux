"""The lossless preview masters that quick turns are made from, as disk to free with the raw."""

import logging
from pathlib import Path

from opticfilm_linux.batch.store import BatchStore

log = logging.getLogger(__name__)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:  # removed meanwhile (a render replaced or dropped it)
        return 0


def _turn_masters(store: BatchStore, number: int) -> list[Path]:
    return sorted((store.tray_dir(number) / "previews").glob("*-master*.png"))


def drop_turn_masters(store: BatchStore, number: int) -> int:
    """Free the lossless preview masters (a tray without raw scans is never turned again);
    returns the bytes freed."""
    freed = 0
    for path in _turn_masters(store, number):
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:  # the raw scans are gone either way; a leftover master only costs disk
            log.warning("could not remove the preview master %s", path, exc_info=True)
            continue
        freed += size
    return freed


def turn_masters_size(store: BatchStore, number: int) -> int:
    """Bytes the tray's masters take; a master removed meanwhile counts as nothing."""
    return sum(_size(p) for p in _turn_masters(store, number))
