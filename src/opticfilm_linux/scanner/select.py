"""Pick the real scanner backend."""

import os

from opticfilm_linux.scanner.base import ScanError
from opticfilm_linux.scanner.driver import (
    DRIVER_ENV,
    DriverConfig,
    DriverScanner,
    install_signal_cleanup,
)

NEXT_STRIP_ENV = "OPTICFILM_NEXT_STRIP"  # "1" allows the hardware-unproven ``load --next-strip``
EJECT_BUTTON_ENV = "OPTICFILM_EJECT_BUTTON"  # "0" switches the front-panel Eject button off


EJECT_BUTTON_OFF = ("0", "false", "no", "off")


def next_strip_enabled() -> bool:
    return os.environ.get(NEXT_STRIP_ENV) == "1"


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
