# Flatpak (draft)

`io.github.bearyjd.OpticFilm.json` describes the app as a Flatpak. It is a draft: it is **not
buildable yet** because every `PLACEHOLDER_*` value (wheel URLs and hashes, the driver commit, the
app commit) must be replaced by a real pin first. The test `tests/test_flatpak_manifest.py` lints
the manifest offline and runs `flatpak-builder --show-manifest` on it when that tool is installed.
No build has been run.

- The scanner driver (`cgillinger/opticfilm135i-linux`) is an external dependency fetched by pinned
  commit. Nothing from Plustek or the vendor driver is vendored here.
- Raw USB access uses `--device=all`; the narrower `--device=usb` has not been tried on this scanner.
- No network at runtime. The Qt app uses the KDE runtime and the PySide6 base app; the CLI
  `ofscan` is installed too.
- The sandbox cannot run `distrobox` (the app's default load command), so the load step must be
  run through `OPTICFILM_LOAD_COMMAND` or from a host terminal. Untested.
