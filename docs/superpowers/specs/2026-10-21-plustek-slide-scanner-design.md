# Native Linux support for the Plustek OpticFilm 135i — design

Status: draft for review. Date: 2026-10-21. Working name: `opticfilm-linux` (builder's call; rename freely).

## Intent and why

The owner has a Plustek OpticFilm 135i 35mm film and slide scanner. They load a tray, the scanner scans four slides per pass, and today that only works in Windows because the vendor driver and software (QuickScan Plus, optional SilverFast 9) are Windows/macOS only. No Linux driver is listed on Plustek's support page, and web searching found no SANE support for the 135i.

Goal: scan trays of slides natively on Linux, get enhanced results comparable to what QuickScan Plus produces, and publish it as an open-source public GitHub repo, in the same spirit and structure as the owner's `fastfoto-linux` project (`../epson`): a thin scanner layer plus an open re-implementation of the PC-side enhancement pipeline, with a CLI and a KDE/Qt desktop app, packaged as a Flatpak.

## Goals

1. Scan a loaded tray (four slides per pass) from Linux with no Windows involved.
2. Reproduce the QuickScan Plus feature set the owner relies on (see "Enhancement scope").
3. Produce finished files in a chosen folder (TIFF, PNG or JPEG), one per slide.
4. Work with the scanner's Positive, Negative and Custom profiles, and with the hardware's infrared channel for dust/scratch removal.
5. Public repo, GPL-compatible open license, CI, Flatpak packaging, honest README.

## Non-goals

- Reproducing SilverFast 9's full feature set.
- Supporting scanners other than the OpticFilm 135i/135 in the first release (the 135 without infrared is a likely cheap extension, untested).
- Mac or Windows support.
- Bundling, redistributing or copying any Plustek code, binaries, firmware, ICC profiles or documentation.

## Anti-goals

- A tool that only works for the author's one scanner unit or one slide type.
- Anything that depends on running Windows software, Wine or a VM at scan time.
- A driver that can brick or damage the scanner. Protocol experiments must be reversible and must never write firmware.

## Facts established (sources: Plustek User's Guide v1.3, Plustek support page, owner's installer file)

| Item | Value |
|------|-------|
| Sensor / light | CCD, LED |
| Optical resolution | 7200 dpi (Windows 64-bit only per vendor; 3600/2400/1800/1200/600 also offered) |
| Bit depth | 48-bit colour in, 24/48-bit out; 16-bit grey in, 8/16-bit out |
| Infrared | Built in on the 135i (not on the 135) |
| Interface / power | USB 2.0; 24 V DC, 0.75 A |
| Scan area | 35 mm x 226 mm; holders for mounted slides and for strips; panoramic holder optional |
| Buttons | Eject, Scan x3 (Positive, Negative, Custom) |
| Speed | ~6 min at 3600 dpi with IR; ~25 min at 7200 dpi with IR |
| Vendor app features | Positive/Negative/Custom profiles, auto exposure, ICC profile, IR scratch/dust removal, Edit screen with unspecified filters, JPEG/TIFF/PNG/BMP output, hand-off to Photoshop/GIMP |
| Installer | `OpticFilm_135i_x64_12L_V6.2.0.1.exe`, InstallShield-style wrapper holding a self-extracting RAR v5 of ~101 MB; contents not yet listed (may be password-protected) |

Observed on the owner's unit, 2026-10-21, powered on and plugged into the dev machine (read from sysfs, no commands sent to it):

| Item | Value |
|------|-------|
| USB ID | `07b3:1436`, manufacturer "Plustek INC", product string "Film Scanner(A81)" |
| Link | USB 2.0 high speed (480 Mbps), one configuration, one interface |
| Interface class | Vendor-specific (0xff/0xff/0xff), so no standard class driver applies |
| Endpoints | Bulk OUT 0x02 and Bulk IN 0x81 (512-byte packets), Interrupt IN 0x83 (1-byte packets, likely button or status events) |
| Kernel driver bound | None |

A web search for this ID in the SANE material found nothing. That means it is unconfirmed, not ruled out: the next step is to check SANE's current device list and try a SANE build against the device.

Captured on 2026-10-21: the vendor software (QuickScan Plus, Windows 11 VM, scanner passed through) scanning an **empty four-slide holder at 600 dpi**, recorded on the host with usbmon and filtered to the scanner only (28,175 transfers, 406 s). Findings, in our own words:

- The scanner is driven through a standard register interface of the Genesys family: control transfers carrying lists of register/value pairs, buffer transfers, and end-of-access commands. This matches the community driver's documentation.
- The timeline is: long idle status polling, setup and table upload, **one continuous image sweep of about 50 s** (700 bulk transfers, 33 MB, no gap over 0.41 s), then a park/eject sequence of about 48 s. The sweep time matches the manual's 52 s at 600 dpi with infrared.
- There is **no per-slide positioning**. The whole holder is scanned in place in a single sweep; separating the four slides is a software crop. The community driver's author reached the same conclusion independently from an empty-holder test.
- The vendor software's 600 dpi register values match the saved vendor config's "Speed2" table (32 of 39 registers; the 7 mismatches come from one sub-table whose format is not understood). Positive-profile registers match 6 of 8, with two plausibly dynamic ones differing.
- Not captured: a scan with real slides, 3600 dpi, and any infrared or exposure behaviour on film.

Not yet known: how the vendor software finds the four slide windows (fixed geometry vs. detection on the image), the exact Edit-screen filters, and whether other units behave the same.

## Constraints

- Hardware is not currently powered (owner is finding the right 24 V adapter). The scanner is the last integration step, so everything before it must be testable without hardware.
- Development machine is Fedora-family immutable Linux (Bazzite): no `lsusb` or SANE tools installed by default; use containers, toolbox or Flatpak for tools.
- Installer and any downloaded files are untrusted: inspect in isolated directories, never execute on the dev machine.
- Reverse engineering is for interoperability with hardware the owner owns. Findings documented in the repo are protocol facts written in our own words, not vendor material.

## Architecture (builder's call unless stated; mirrors fastfoto-linux)

Layers, each independently testable:

1. **Scanner transport** — the only layer that touches hardware. Interface: list devices, open, set profile/resolution/bit depth/IR, run a scan, return raw frames (RGB plus optional IR plane), eject holder, power-saving control. Two implementations behind that interface:
   - `FakeScanner` — replays sample images; used by all tests and by development before hardware is available.
   - Real backend — chosen by the discovery outcome below.
2. **Pipeline** — pure functions over image arrays: tray split into slides, crop/straighten, exposure and colour/fade correction, positive/negative conversion, IR dust and scratch removal, sharpening, grain reduction, ICC handling. Immutable data flow (new image per step), no hardware knowledge.
3. **Output** — file naming (date/album/sequence like fastfoto-linux), format choice, metadata, optional keeping of raw 16-bit scans.
4. **Interfaces** — CLI first (`opticfilm devices | scan | process`), then a PySide6/Kirigami desktop app sharing one session object, as fastfoto-linux does.
5. **Packaging** — Flatpak (KDE runtime) plus a user-level installer; udev rule for device access shipped with the package.

Language/UI default: Python managed with `uv`, PySide6 for the GUI, NumPy/OpenCV-class libraries for the pipeline. Chosen to match the owner's existing project and tooling; a compiled core is allowed later if 48-bit/7200 dpi performance demands it.

## Phases

Each phase ends with something runnable and tested.

0. **Discovery (needs a powered scanner, one short session).** Plug in; read USB ID from sysfs; try SANE (`sane-backends` current, genesys/plustek/gt68xx families) and report how far it gets. Outcome decides path A or B below.
1. **Project skeleton + fake scanner + CLI.** Repo, CI, license, README, `FakeScanner`, `devices` and `scan` commands working against sample images.
2. **Pipeline MVP.** Tray split, crop/straighten, auto exposure, positive/negative, output files. Tested on real or stand-in scans (any public-domain slide scans can stand in).
3. **Enhancement parity.** Colour/fade restoration, sharpening, grain reduction, ICC; IR dust/scratch removal (stand-in IR data until the scanner is available).
4. **Real scanner backend** (path A or B).
5. **Desktop app + Flatpak.**
6. **Hardening.** Tuning against real scans, docs, release.

### Prior art found 2026-10-21 (changes the driver plan)

- `cgillinger/opticfilm135i-linux` (GPL-2.0-or-later or MIT): an experimental Python/pyusb driver and SANE genesys backend for this exact scanner (`07b3:1436`). Tested on one unit. Scans film strips of up to six frames at 600–7200 dpi, 16-bit TIFF/PNM, with the infrared channel and automatic dust removal, negatives as raw linear output with a basic positive preview, and front-panel buttons. Known rough edges: power-cycling after an interrupted scan, USB drop-off after about five minutes idle, slow at 3600 dpi. **Mounted slides and panorama are not supported.** Its SANE merge request (sane-project/backends !1032, submitted 2026-09-29, last edited 2026-10-16) is open with no reviewer comments, and says the four-slide holder was tried only with one slide in one position.
- Current mainline SANE (sane-backends 1.4.0) does not recognise `07b3:1436`; `scanimage -L` finds nothing. (Tested 2026-10-21 in a Fedora 44 container.)
- `bazfp/OpenOptic` (MIT): a browser-based suite for a different scanner (OpticFilm 7600i, GL843). Not usable for the 135i, but its infrared texture-aware inpainting and multi-exposure ideas are worth studying.
- `jboneng/pyopticfilm`: PyUSB driver for other OpticFilm models (GL842/GL843/GL845/GL128); not confirmed for the 135i.
- No forum or mailing-list threads about Linux use of the 135i were found.

### Driver path (replaces the earlier Path A / Path B)

Build on `opticfilm135i-linux` as the scanner layer instead of writing a driver. Pin a known version, treat it as an external dependency behind our scanner interface, and contribute fixes upstream. The missing piece is **four-slide mounted-holder support**, which the owner needs. The community driver's roadmap (item C2) lists it as in progress as of 2026-09-18, with the empty-holder mechanics and one mounted-slide test already done, so **coordinate with the author instead of duplicating the work**. What remains there is a slide profile: scan the whole holder in one sweep and crop four slides in software, with positive handling, infrared-keyed cropping and dust removal. Our own capture (see "Captured on 2026-10-21") independently supports that approach. The owner's real four-slide trays and a second scanner unit are things we can offer the project: it has only been tested on one unit. Phases 1–3 do not depend on any of this.

Next hardware steps, in order, each read-only first: (1) a second capture with real slides at 3600 dpi, payload truncated to keep the file small; (2) check how the slide windows are found; (3) try the community driver on the owner's unit, strip holder first, only after reading its hardware-safety notes.

The owner will use trays that hold four slides each; strip scanning is secondary and mostly covered by the existing driver.

## Enhancement scope

Must match (derived from the vendor guide): Positive/Negative/Custom profiles, auto exposure, ICC output profile, IR scratch/dust removal, resolutions 600–7200 dpi, 8/16-bit output, TIFF/PNG/JPEG. Likely also (builder's call, pending the vendor's Edit-screen filter list): rotate, sharpening, grain reduction, colour/fade correction.

Out of scope for v1: face-based auto-upright, red-eye, albums UI beyond simple naming (all exist in fastfoto-linux and can be borrowed later).

## Testing and quality

Follow the owner's standing rules: tests first, 80% coverage, immutable data patterns, small files, no secrets in the repo. Hardware-dependent tests are marked and skipped in CI; everything else runs against `FakeScanner`.

## Risks

- **Driver unknown (high).** Path B could take most of the project's effort, and a 2025 hardware refresh (new lenses) may differ from the 2020/2021 documents the owner supplied.
- **Four-slide tray behavior unknown (medium).** Whether it is one long scan to be split or four positioned scans changes the scanner layer's shape.
- **IR dust removal (medium).** Needs real IR data to tune; algorithm quality is only judged on real scans.
- **Legal/ethical (low-medium).** Keep the repo free of vendor material; protocol notes in our own words.
- **Slow scans (low).** 7200 dpi takes ~25 minutes; the app must run scans in the background and survive interruption.

## Left to the builder

Project name, exact libraries, CLI flags, file naming scheme details, and how to structure the Qt app, as long as they follow fastfoto-linux conventions and the architecture above.

## Calls made without the owner (please check)

- Scanner frames carry slide-position metadata, so the pipeline works whether a pass returns one strip or separate slides. Resolved by the builder check; revisit after Phase 0.
- Pipeline processes in tiles/streaming from the start, because a full tray at 7200 dpi is tens of gigabytes. Default working resolution is 3600 dpi; 7200 dpi is opt-in.
- Infrared dust removal is developed against synthetic defects first and judged on real scans later; it is acceptable for it to ship as "experimental" until then.
- Phase 0 is partly done: the device enumerates (see "Observed on the owner's unit"). The remaining Phase 0 work is the SANE attempt and the USB protocol capture decision.
- Python + PySide6 + `uv` + Flatpak, copied from fastfoto-linux.
- Name `opticfilm-linux`.
- GPL-3.0-or-later licence, matching fastfoto-linux.
- Fake scanner first, hardware last.
- Enhancement list beyond the vendor guide's explicit features.
