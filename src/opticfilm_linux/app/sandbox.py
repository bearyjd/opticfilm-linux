"""Whether the app runs inside a Flatpak sandbox, and how to reach the host from there."""

import os
from pathlib import Path

FLATPAK_INFO = "/.flatpak-info"  # Flatpak mounts this file into every sandbox
# Runs a command on the host. Needs --talk-name=org.freedesktop.Flatpak (see packaging/flatpak).
HOST_PREFIX = ("flatpak-spawn", "--host")


def in_flatpak() -> bool:
    return bool(os.environ.get("FLATPAK_ID")) or Path(FLATPAK_INFO).exists()
