---
title: "The app runs on the bare-metal desktop with a few user-installed Python packages, no container"
author: Bearyjd
date: 2026-11-16
branch: docs/trail-learning-host-run-20261115
cost: { minutes: 30, tokens: 41129 }
id: l-87fcadd6
scope: repo
status: active
src: agent
---

## Decided
2026-11-15: the Qt app starts on the host (no distrobox, no dev container) and the owner reported it loaded without issues. The host Python is 3.15.0 (Fedora 45 base) and ships PySide6, Kirigami 6.31 and PIL as system packages; the missing ones were installed into the home directory only (no sudo, nothing under /usr).
In the code: `/usr/bin/python3 -m pip install --user --only-binary=:all: pyusb numpy tifffile 'opencv-python-headless<5'` gives numpy 2.5.5 and cv2 4.14.0. Start with `env PYTHONPATH=<repo>/src OPTICFILM_DRIVER_DIR=<repo>/.local-research/publish/driver-latest OPTICFILM_DRIVER_RUNNER=host /usr/bin/python3 -c "from opticfilm_linux.app.qt.app import main; raise SystemExit(main())"`.

## Tried and ruled out
The dev container for the app: it works, but its portal is unreachable ("Failed to register with host portal"), so folder pickers fall back to Qt's own dialog instead of KDE's. Also, the pip --user packages from Nov 1 were for Python 3.14 (`~/.local/lib/python3.14`); the host moved to 3.15, so they were invisible. cv2 5.0 was installed first by default and replaced with 4.x because everything here was tested on 4.x (heirloom pins opencv<5 for a different reason).

## Watch out
Not yet verified from the host run: whether the KDE native Browse dialog appears (the portal still says "App info not found for io.github.bearyjd.OpticFilmLinux", a missing .desktop file), and any load/scan from the host. After a Fedora base update the Python minor version can change again and the user-site packages go missing: re-run the pip line.
