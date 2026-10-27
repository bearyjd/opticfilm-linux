"""Offline verdict for a hardware-run folder: ``uv run python scripts/check_spike.py DIR``."""

import sys

from opticfilm_linux.spikecheck import main

if __name__ == "__main__":
    sys.exit(main())
