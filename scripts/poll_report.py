"""Offline poll-timeout report for run folders: ``uv run python scripts/poll_report.py DIR...``."""

import sys

from opticfilm_linux.pollreport import main

if __name__ == "__main__":
    sys.exit(main())
