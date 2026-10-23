"""Pick the real scanner backend."""

from opticfilm_linux.scanner.base import ScanError
from opticfilm_linux.scanner.driver import DRIVER_ENV, DriverConfig, DriverScanner


def real_scanner() -> DriverScanner:
    """The OpticFilm 135i through the community driver; says what is missing if it is not there."""
    config = DriverConfig.from_env()
    if not config.available():
        where = config.driver_dir or "the Python path"
        raise ScanError(
            f"the scanner driver (of135i) was not found in {where}; "
            f"set {DRIVER_ENV} to a checkout of opticfilm135i-linux"
        )
    return DriverScanner(config)
