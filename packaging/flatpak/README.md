# Flatpak (draft)

`io.github.bearyjd.OpticFilmLinux.json` describes the app as a Flatpak. It is a draft: it is **not
buildable yet** because the app commit (`PLACEHOLDER_COMMIT_APP`) is only pinned in the last
PR. The Python wheels are pinned (below). The test `tests/test_flatpak_manifest.py` lints
the manifest offline and runs `flatpak-builder --show-manifest` on it when that tool is installed.
No build has been run.

- The scanner driver is an external dependency: the owner's fork `bearyjd/opticfilm135i-linux`,
  pinned at commit `eaa6f88` (full sha in the manifest). That commit has the calibration stop gate
  default `enforce`; upstream `cgillinger` master (`6270867`) has the gate off by default and exits
  4 on slide scans here, so it is not used. Nothing from Plustek or the vendor driver is vendored here.
- Raw USB access uses `--device=all` (it also covers the GPU). It works on the host, but nothing has
  run inside the sandbox. Narrowing to `--device=usb` would need `--device=dri` alongside; untested.
- No network at runtime. The Qt app uses the KDE runtime 6.11 and the PySide6 base app (also 6.11),
  which provides numpy, so numpy is not a wheel source here. The base app's
  `/app/cleanup-BaseApp.sh` runs as a cleanup command; the CLI
  `ofscan` is installed too.
- Wheels (pinned 2026-11-04 from PyPI's JSON API, by hand, newest releases): pillow 13.0.0 (cp313
  manylinux_2_28), tifffile 2026.10.15, opencv-python-headless 5.0.0.93 (`cp37-abi3`, which installs
  on cp313) and pyusb 1.3.1. pip runs with `--no-deps`, so each `Requires-Dist` was checked: the
  BaseApp's numpy 2.3.5 satisfies tifffile `numpy>=2.1` and opencv `numpy>=2` (no upper bound);
  imagecodecs is not needed (uncompressed TIFFs only). The import inside the sandbox is still
  unverified.
- The sandbox cannot run `distrobox` (the app's default load command), so the load step must be
  run through `OPTICFILM_LOAD_COMMAND` or from a host terminal. Untested.
- `flatpak-builder --show-manifest` only parses the file; it is not a build check.
- Still missing before this is a complete package: a `.desktop` file, AppStream metainfo and an
  icon, the app commit pin, and a check
  of what the driver needs at runtime (it is copied as is, nothing is built).
- Scanning from inside the Flatpak is unverified.
