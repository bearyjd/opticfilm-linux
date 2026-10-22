import pytest

from opticfilm_linux.sweepstore import save_sweep
from synthetic import make_sweep


@pytest.fixture
def synthetic_sweep_dir(tmp_path):
    """A stored 600 dpi four-slide sweep with a few dust specks."""
    directory = tmp_path / "sweep"
    save_sweep(make_sweep(dpi=600, dust=12), directory)
    return directory


@pytest.fixture(scope="session")
def sweep600_dir(tmp_path_factory):
    """A stored 600 dpi four-slide sweep, shared by the whole session (read-only)."""
    directory = tmp_path_factory.mktemp("sweep600") / "sweep"
    save_sweep(make_sweep(dpi=600), directory)
    return directory
