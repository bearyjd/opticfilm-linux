"""Whether the app runs inside a Flatpak sandbox, and how to reach the host from there."""

from pathlib import Path

FLATPAK_INFO = "/.flatpak-info"  # Flatpak mounts this file into every sandbox
# Runs a command on the host. Needs --talk-name=org.freedesktop.Flatpak (see packaging/flatpak).
HOST_PREFIX = ("flatpak-spawn", "--host")


def in_flatpak() -> bool:
    """Only the info file counts: an environment variable can leak in from a parent process."""
    return Path(FLATPAK_INFO).exists()
