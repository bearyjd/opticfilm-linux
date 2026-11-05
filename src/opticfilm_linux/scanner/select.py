"""Pick the real scanner backend."""

import os

from opticfilm_linux.scanner.base import ScanError
from opticfilm_linux.scanner.driver import (
    DRIVER_ENV,
    DriverConfig,
    DriverScanner,
    install_signal_cleanup,
)

NEXT_STRIP_ENV = "OPTICFILM_NEXT_STRIP"  # explicit off values disable ``load --next-strip``
EJECT_BUTTON_ENV = "OPTICFILM_EJECT_BUTTON"  # "0" switches the front-panel Eject button off


# Skipping openings changes what the motor scans and is untested on hardware: off by default.
SKIP_EMPTY_ENV = "OPTICFILM_SKIP_EMPTY"  # on: offer "Slides in this holder" (scans 1..N)
SKIP_GAPS_ENV = "OPTICFILM_SKIP_GAPS"  # on, with SKIP_EMPTY: any set of openings, e.g. 1,3,4
# The app's own eject after a good tray (plan rungs H5/H7/H8, untested on hardware): off.
AUTO_EJECT_ENV = "OPTICFILM_AUTO_EJECT"


EJECT_BUTTON_OFF = ("0", "false", "no", "off")
SWITCH_ON = ("1", "true", "yes", "on")


def _switched_on(name: str) -> bool:
    """Off unless explicitly on: an unreadable value must not enable an untested path."""
    return os.environ.get(name, "").strip().lower() in SWITCH_ON


def skip_empty_enabled() -> bool:
    return _switched_on(SKIP_EMPTY_ENV)


def skip_gaps_enabled() -> bool:
    """Only meaningful with ``skip_empty_enabled``; the controller combines the two."""
    return _switched_on(SKIP_GAPS_ENV)


def auto_eject_enabled() -> bool:
    """Read once, when the controller is made; never passes ``--eject`` to the driver."""
    return _switched_on(AUTO_EJECT_ENV)


def next_strip_enabled() -> bool:
    """On by default; explicit off values provide an operator kill switch."""
    return os.environ.get(NEXT_STRIP_ENV, "").lower() not in EJECT_BUTTON_OFF


def eject_button_enabled() -> bool:
    """On unless explicitly switched off: an unreadable value must not disable a safe default."""
    return os.environ.get(EJECT_BUTTON_ENV, "").strip().lower() not in EJECT_BUTTON_OFF


def real_scanner() -> DriverScanner:
    """The OpticFilm 135i through the community driver; says what is missing if it is not there."""
    config = DriverConfig.from_env()
    if not config.available():
        where = config.driver_dir or "the Python path"
        raise ScanError(
            f"the scanner driver (of135i) was not found in {where}; "
            f"set {DRIVER_ENV} to a checkout of opticfilm135i-linux"
        )
    install_signal_cleanup()  # a killed app must not leave the driver holding the scanner
    return DriverScanner(config)
