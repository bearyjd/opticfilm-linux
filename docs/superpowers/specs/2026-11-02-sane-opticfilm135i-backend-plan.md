# SANE backend for the Plustek OpticFilm 135i (USB `07b3:1436`): design and plan

- **Path:** `docs/superpowers/specs/2026-11-02-sane-opticfilm135i-backend-plan.md`
- **Status:** DRAFT for owner review. Desk research only.
- **Inputs:**
  - SANE recon: read-only, local sane-backends HEAD `22c9e5337`, recon notes from 2026-11-02.
  - Driver recon: read-only, driver fork checkout, recon notes from 2026-11-02.
  - Project STATE as of 2026-11-02.

> **Hard rule.** Nothing in this plan is sent anywhere outside this machine without the owner's explicit say-so. That covers sane-devel mail, GitLab MRs, issues or comments, pushes to the owner's GitLab fork, and any contact with upstream `cgillinger`. All phases before the "Upstream" phase are local-only. The steps that need the owner's go are marked **[OWNER GO]**.

---

## 0. TL;DR

1. **SANE does not support the 135i today.** A search of backend sources, `doc/` (including descriptions), tools and tests found no `07b3:1436`, `0x1436`, "OpticFilm 135" or "135i" (SANE recon §4).
2. **Do not start from scratch.** The driver checkout already contains a SANE genesys C++ GL126 implementation (`sane/gl126*.{h,cpp}`):
   - nine files, 30,799 lines in total;
   - 24,990 of those lines are generated tables;
   - 5,809 are handwritten (driver recon §5; `README.md:29–54`).

   The driver identifies the 135i controller as a **Genesys GL126** (`usbio.py:1–4`).
3. **The main open question is where the code lives.** The SANE recon names genesys as "the first compatibility candidate". However, the ASIC enum at SANE HEAD spans GL646/841/842/843/845/846/847/124/128, and **GL126 is not in it**. So the work means adding a new ASIC to genesys, not adding a USB ID.
4. **The fallback is a standalone `opticfilm135i` backend.** The recon estimates it at 6,250–10,200 handwritten C lines, plus about 20–30k generated table lines, plus 1.5–3k test lines (an **UNVERIFIED** estimate).
5. **The biggest non-technical blocker is licensing and authorship, not code:**
   - All existing C++ and Python code is © 2026 Christian Gillinger, the upstream author.
   - Our standing rule forbids touching upstream `cgillinger`.
   - How SANE's linking exception applies to these files is unresolved.

   These are owner decisions (§10).
6. **Recommendation:**
   - Phase A: a provenance and licence audit (desk only).
   - Phase B: a local, compile-only feasibility check of the existing GL126 code against SANE HEAD in the owner's fork, without pushing.
   - Only then decide between genesys and standalone, and whether to involve the original author.

   Production scanning stays on our Python driver throughout.

---

## 1. Goals and non-goals

### Goals

- **G-1.** Produce a SANE backend that lets standard frontends (`scanimage`, and by extension others) scan with the OpticFilm 135i on Linux. "Calibrated" here means the same dark/white calibration the Python driver performs.
- **G-2.** Preserve the driver's safety model in the backend:
  - strict-poll predicates;
  - the failed-session latch;
  - no automatic recovery writes after a failure;
  - power-cycle recovery instructions.

  The driver recon lists these as the highest-risk items to get wrong (§5).
- **G-3.** Make the backend testable without hardware, through `sanei_usb` XML record/replay. SANE reviewers and CI cannot own the scanner.
- **G-4.** Keep a truthful support claim, with no overstatement. All hardware evidence comes from **one unit and one tester** (`README.md:14–21,176–206`).
- **G-5.** Keep the provenance documentation good enough that a SANE reviewer can judge it. The recon recommends documenting capture provenance and unresolved protocol assumptions (SANE recon §6).

### Non-goals (for this plan)

- **NG-1.** Replacing the Python driver in our app. The production pipeline (7200 dpi trays → JPEG → heirloom) keeps using `OPTICFILM_DRIVER_RUNNER=host` and the Python driver. A SANE backend must not block or slow production scanning.
- **NG-2.** Porting the application-level processing into the backend:
  - 110 film reconstruction (`film110` 1,664 lines);
  - dust processing;
  - preview inversion;
  - aperture crop.

  The driver recon notes that this processing "need not all live in a backend".
- **NG-3.** Supporting other OpticFilm models or other 135i units without evidence.
- **NG-4.** Pushing anything to upstream `cgillinger` (standing rule).
- **NG-5.** Publishing raw vendor captures, vendor INI material or vendor DLL-derived content. `docs/driver-design.md:195–199` records publication restrictions.

---

## 2. What the recon establishes

### 2.1 Facts from the SANE tree (HEAD `22c9e5337`)

| Fact | Source |
|---|---|
| No existing 135i reference anywhere | SANE recon §4 |
| Genesys maps OpticFilm 7200→GL842, 7200i/7300/7500i→GL843; the tree also has GL845 OpticFilm models and GL128 (8200i SE) work | `backend/genesys/tables_model.cpp` |
| Genesys is C++11; `README` requires C11; `backend-writing.txt` and `configure.ac:43–47` say C99 | SANE recon §1 |
| Genesys sources: "Transparency Adapter" / "Transparency Adapter Infrared"; no frame/holder/focus options | `genesys.h`, `genesys.cpp` |
| Clearest multi-frame film option model: coolscan3 (`infrared`, `frame`, `frame-count`, focus) | `coolscan3.c:460–966` |
| Standard IR option name is `infra-red`; RGBI frame constants are disabled (`#if 0`) | `include/sane/saneopts.h`, `sane.h` |
| USB record/replay: `--with-usb-record-replay` (libxml2); `fakeusb:`, `fakeusbout:`, `fakeusbdev:` | `configure.ac:491–508`, `backend/dll.c:1289–1350` |
| No existing automatic XML replay tests found; `fail_test()` in `sanei_usb.c` is empty | SANE recon §2 |
| CI: `make-dist`, six compile jobs (incl. no-USB and musl), `make-distcheck`; generally `-Werror`; CI builds from the tarball | `.gitlab-ci.yml` |
| udev rules are generated from `.desc` via `tools/sane-desc -m udev` | `tools/Makefile.am` |
| Reverse engineering from USB traces has precedent | `doc/sane-canon_dr.man:191` |

### 2.2 Facts from our driver

| Fact | Source |
|---|---|
| Controller: Genesys GL126. Endpoints: bulk IN 0x81, bulk OUT 0x02, interrupt IN 0x83 | `usbio.py:1–4,34–39` |
| Existing SANE genesys GL126 C++ code: 9 files / 30,799 lines (24,990 generated) | `sane/gl126*`, `README.md:29–54` |
| "Semantic PARK has hardware evidence via SANE"; packaged v8 changes are offline-prepared and **UNVERIFIED on hardware** | `cli.py:3533–3549`, `sane/wp3-package/README.md:3–40` |
| Python package: 32,481 lines, of which 17,237 are table modules | driver recon §2 |
| Tracer format `of135i-trace/1` JSONL: OUT hex capped at 64 bytes; bulk-IN lengths only | `trace.py:38–100` |
| Offline replay harness: bulk IN replays as **zeros**; virtual time | `docs/replay-harness.md:44–105` |
| 9 compiled traces, 8 calibration binaries; no raw pcap/pcapng | `traces/`, `cal-data/capture/` |
| One scanner, one tester | `README.md:14–21` |

### 2.3 Our own hardware evidence (STATE, our fork via the app/CLI)

- 7200 dpi 4-slide trays ran clean three times (827 s, `poll_timeouts=0`), including under load.
- After the swap series, a full 7200 tray took 854 s, again with `poll_timeouts=0`.
- Five `load --next-strip` swaps ended `0xdc55` idle-homed.
- Two cold first loads went from `0x00/0x4855` to `0xdc55`.

> **Discrepancy to flag.** The driver checkout's docs still say the "four-slot mounted-slide CLI workflow" is **UNVERIFIED on hardware** (`README.md:236–245`). Our STATE evidence was gathered through our app and fork. It is not yet reflected in those docs and has not been exercised through SANE. Any `.desc` or manpage claim must cite only evidence from the SANE backend itself.

### 2.4 Unknowns that block decisions

| ID | Unknown |
|---|---|
| U-1 | How was GL126 identified (chip marking, register signature, inference)? Is it the same on other units? The recon says cross-unit identification is **UNVERIFIED**. |
| U-2 | Which genesys version the existing `sane/gl126*` code was written against, and whether it still compiles against HEAD `22c9e5337`. |
| U-3 | Exact hardware coverage of the existing SANE path. Only "semantic PARK via SANE" is recorded. |
| U-4 | Whether the `sane/gl126*` notices include the SANE linking exception. The recon reports "Gillinger / GPL-2-or-later notices at lines 1–19"; whether an exception is present is not stated. |
| U-5 | Whether `sane/gl126*` copies or adapts code from upstream genesys GL124 files. `gl126.h:21–36` credits a "GL124 comparison/source"; if code was adapted, upstream notices and exceptions must be preserved. |
| U-6 | Which commits in the driver fork, if any, are ours versus Gillinger's. This determines who can relicense what. |
| U-7 | The SANE GitLab MR process and server-side merge requirements (**UNVERIFIED** locally). |
| U-8 | SANE's stance on reverse-engineered register tables of this size. Strict clean-room provenance is **UNVERIFIED** for the driver. |
| U-9 | Meaning of buffer descriptor index 8 on the first image read (`usbio.py:436–482`). |
| U-10 | Bare power-on lamp warm-up curve (`device.py:137–154`). |

---

## 3. Licensing and provenance analysis

> This section is engineering analysis, **not legal advice**. Items marked ⚠ need an owner decision or outside input.

### 3.1 What the code is licensed under

- **Python driver (`of135i/`, tools, Python tests):** GPL-2.0-or-later **OR** MIT (`LICENSING.md:7–14`; `LICENSE-MIT:1–13`).
- **SANE/C++ probes and build/config files:** GPL-2.0-or-later.
- **Docs:** CC-BY-4.0.
- **Single named holder:** © 2026 Christian Gillinger.

### 3.2 Hygiene issues the audit must fix (in our fork first)

- `pyproject.toml:6` declares GPL-2.0-or-later only, which contradicts the dual-licence statement.
- No Python file (30 package, 23 tool, 51 test) carries a per-file notice or SPDX header.
- The five C++ probes, the shell and config files, and `udev/60-of135i.rules` lack legal headers.
- Only `docs/LICENSE:1` has an `SPDX-License-Identifier`.
- `of135i/data/srgb.icc` (588 bytes, generated by Little-CMS through Pillow) says "No copyright, use freely". It is low risk but must be noted if shipped. A backend does not need it.

### 3.3 Compatibility with SANE

- SANE's model, per the recon, is the `backend/dll.c` / `pieusb.c` header: copyright, GPL-2.0-or-later, warranty disclaimer, and the **SANE linking exception**.
- `LICENSE` says individual notices prevail and only *most* backends carry the exception. A universal mandatory-exception policy is **UNVERIFIED**.

Consequences:

1. **GPL-2.0-or-later code can legally be redistributed and modified into SANE** under its own terms. Licence compatibility is not the blocker.
2. ⚠ **Only the copyright holder can add the SANE linking exception.** If SANE reviewers expect the exception (likely for a genesys addition, since existing files carry it, but **UNVERIFIED**), then for Gillinger's code we need either:
   - his agreement, or
   - code that no longer derives from it. That would be a rewrite, and it is impractical for the 25k generated table lines unless we regenerate them ourselves from our own captures.
3. ⚠ **The MIT option on the Python code** lets a C port derived from the Python carry any licence, including GPL-2.0+ with the exception, *provided MIT attribution is kept*. This applies to the Python only, not to the existing C++ (GPL-2.0+ only).
4. ⚠ **If `sane/gl126*` adapts upstream genesys code (U-5),** those parts are already SANE-licensed, and the upstream notices must be preserved (SANE recon §1: "Preserve existing exceptions when adapting their code").
5. **Attribution and courtesy, independent of legality.** Submitting someone else's reverse-engineering work to SANE without telling them is a reputational risk for the owner and for acceptance. Contacting Gillinger conflicts with our standing "never touch upstream cgillinger" rule. Only the owner can resolve this (D-2).

### 3.4 Provenance of the protocol knowledge

- **Origin:** reverse-engineered from USB captures of the Windows driver. QuickScan ran in a Windows 11 VM, captured through host usbmon (`README.md:637–652`, `tools/gen_load_table.py:4–7`).
- **Vendor INI material** is named in `docs/protocol-notes.md:1–4`.
- **Vendor DLL static analysis** is permitted for *facts only*, with no copying of code or structure (`docs/static-analysis.md:5–32`). Quarantine (`vendor_derived.py`) currently has no findings. `LICENSING.md:23–29` says the SANE backend uses none of the quarantined facts.
- **Strict clean-room: UNVERIFIED.** The same person or people studied captures and wrote code. That is normal for SANE reverse-engineered backends (precedent: `sane-canon_dr`), but it must be stated plainly.
- **Generated tables (17k Python / 25k C++ lines)** are replayed command programs derived from captures (`gl126_tables.{h,cpp}:21–32` documents generation). Reviewers may ask for:
  - the generator;
  - the input format;
  - a statement that inputs are our own captures of our own device, with no vendor files.

  Our compiled traces are in-repo, but raw pcaps are absent and are under publication restrictions (`docs/driver-design.md:195–199`).

**Deliverable for Phase A:** a `PROVENANCE.md` draft (local) containing:

- the capture setup;
- what was taken from captures, from INI files, and from static analysis;
- the quarantine status;
- the table-generation pipeline;
- a list of assumptions not understood (for example U-9, the unnamed bits 02/10/20 of register 0x101).

---

## 4. SANE contribution process and integration checklist

The process is partly **UNVERIFIED**. `doc/backend-writing.txt` describes mailing-list and patch inclusion, while `doc/releases.md` and `newsfragments/README.md` mention MRs.

### 4.1 Process (as documented locally)

1. **[OWNER GO]** Contact sane-devel early and explain the plan and its open-source nature (`backend-writing.txt:10–40`). The guide also suggests asking the manufacturer for protocol information. Whether to contact Plustek is an owner decision (D-6).
2. Develop in the owner's GitLab fork. A local branch is fine at any time; pushing is **[OWNER GO]**.
3. **[OWNER GO]** Open an MR, or request inclusion for review and compilation testing (`backend-writing.txt:482–489`).
4. CI must pass all jobs: `make-dist`, the six compile jobs, and `make-distcheck`. No job is allowed to fail.

### 4.2 Integration checklist

The list below covers the standalone-backend case. In the genesys case, the subset is the model/ASIC registration within `backend/genesys/`, plus the `.desc` entry, plus the newsfragment.

- [ ] `backend/opticfilm135i.c` (optional `.h`, backend-prefixed helpers), GNU style (`indent -gnu`), about 80 columns.
- [ ] Add `configure.ac:ALL_BACKENDS` and `backend/dll.conf.in`; create `backend/opticfilm135i.conf.in`.
- [ ] `backend/Makefile.am`:
  - `BACKEND_CONFS` (config filenames; the guide's checklist is stale here);
  - `be_convenience_libs`;
  - `be_dlopen_libs`;
  - `libopticfilm135i_la_SOURCES`;
  - wrapper sources;
  - flags;
  - dist of the conf template.
- [ ] `doc/descriptions/opticfilm135i.desc`: USB `0x07b3 0x1436`, with the *truthful* status keyword per `doc/descriptions.txt`. The exact keyword is to be checked; minimal or untested is likely appropriate given one unit.
- [ ] `doc/sane-opticfilm135i.man`; register it in `doc/Makefile.am`; reference it in `doc/sane.man`.
- [ ] `AUTHORS`; `po/POTFILES` if `SANE_I18N()` is used.
- [ ] `newsfragments/<name>.backend` (Towncrier; full issue URLs; no MR links).
- [ ] udev rule: generated from the `.desc`. Our `udev/60-of135i.rules` is *not* contributed.
- [ ] Local checks: `tools/style-check.sh`, `make check`, `make distcheck`, `make -C doc html-pages`.
- [ ] Builds must pass with `-Werror`, including with `--without-usb` and on musl. Reproduce the Fedora clang job locally.

---

## 5. Architecture options

### Option A: new GL126 ASIC inside `backend/genesys` (C++11)

Reuse and adapt the existing `sane/gl126*` code.

- **Pros:**
  - The code already exists: about 5.8k handwritten lines plus about 25k generated.
  - Genesys already handles OpticFilm models and transparency/IR sources.
  - The SANE recon names genesys as the first candidate.
  - Genesys has its own unit and session-testing machinery.
  - The C++ requirement is already met by the host.
- **Cons and risks:**
  - GL126 is not in the HEAD ASIC enum, so a new ASIC must be added to a 45k-line, 81-file subsystem.
  - Genesys has no holder/frame/eject concepts; the 135i needs loader/jog/eject/PARK flows.
  - The driver's strict-poll and failure-latch model may not map cleanly onto genesys's generic flows.
  - Licence and author issues (§3.3) apply in full, because the code *is* Gillinger's.
  - The genesys maintainers' appetite for a capture-replay-table-driven ASIC is **UNVERIFIED**.
- **Size:** adapting rather than writing. Effort is dominated by rebasing onto HEAD, `-Werror`/CI hygiene, filling the table-generation provenance, and hardware re-validation.

### Option B: standalone `backend/opticfilm135i` in C (C99/C11 per `configure.ac`)

Port from the Python driver, which is MIT-optionable, borrowing film-workflow option concepts from coolscan3 and pieusb.

- **Pros:**
  - Freedom to model holder, frame, load and eject, plus the safety latch, exactly as the driver does.
  - A clean licensing path is possible: MIT-derived, so we choose the header, keeping attribution.
  - No dependency on genesys internals.
- **Cons:**
  - Largest effort: 6,250–10,200 C lines, plus 20–30k generated tables, plus 1.5–3k test lines (**UNVERIFIED** estimate, driver recon §5).
  - Duplicates the Genesys family knowledge that reviewers may want consolidated.
  - Needs a C table generator.
  - The SANE recon's justification for a separate backend ("until the 135i protocol is identified") is weakened by the GL126 identification. Reviewers may ask "why not genesys?"

### Option C: thin SANE wrapper around our Python driver (subprocess/IPC)

- **Pros:**
  - Fastest to a working `scanimage` on *our* machine.
  - Zero protocol re-implementation, and it reuses the battle-tested safety code.
- **Cons:**
  - A Python, pyusb and numpy runtime dependency inside a SANE backend is a poor fit for SANE's build and CI model:
    - compiled backends;
    - a no-USB build variant;
    - `-Werror`;
    - distcheck from the tarball.
  - Upstream acceptance is **UNVERIFIED** but should be assumed unlikely.
  - `sanei_usb` replay cannot exercise it, because USB happens in Python.
- **Use:** out-of-tree only, for personal frontend interop, if ever wanted.

### Option D: no SANE work; keep the Python driver plus app

This is the baseline. It is listed so that the "do nothing" choice is explicit.

### Recommendation

Phase A (audit) and Phase B (local compile feasibility of Option A) come first, because they are cheap and resolve U-2, U-4, U-5 and U-6.

- **Option A** if:
  - the code builds against HEAD with modest work, **and**
  - the owner resolves the author and licence question.
- **Option B** if:
  - the licence route through Gillinger is closed, **or**
  - genesys integration proves structurally wrong for the loader/latch model.

  The existing C++ can then serve as a design reference only where licence permits.
- **Option C** only as a private convenience. It should never be the upstream path.

### Cross-cutting design points (any in-tree option)

- **All USB goes through `sanei_usb`.** This is required for replay. Use endpoints 0x81/0x02/0x83; control requests `40/04/0083`, `c0/04/008e`, `40/04/0082`, `40/0c/008c|008d`; 16,384-byte bulk chunks; IN timeout 60 s, OUT 5 s.
- **Safety model.** Port strict versus non-strict polls verbatim:
  - strict deadline `max(3×captured_duration×travel_scale, 1 s)`, 4 ms interval;
  - non-strict 0.3 s floor with leniencies;
  - LOAD/POSITION/PARK/eject masks (FB, F0/F1, `(high&E3)==E0`, `(high&21)==20`).

  Latch the session failed on any strict failure. After failure the backend issues **no** recovery writes and reports `SANE_STATUS_IO_ERROR` with a power-cycle instruction in the log and manpage.
- **Slope RAM.** Re-upload slope RAM (0x1000c000 / 0x10010000) before every positioning. Stale slopes caused grinding.
- **IR delivery.** The hardware's dual-light mode delivers interleaved rows: even rows IR, odd rows visible. RGBI frames are disabled in SANE. Choices:
  - (i) Genesys-style `source = "Transparency Adapter Infrared"` as a separate gray scan pass;
  - (ii) one physical pass, with the backend caching the IR plane and serving it on a following `sane_start`.

  This is a design decision (D-8). Row alignment, `round(24*dpi/7200)` with both ends cropped, must happen in the backend before IR averaging, so a bounded line buffer is needed.
- **Resolution quirk.** "2400 dpi" is 3600 × 2400 with no resampling. Expose it honestly: x/y resolution options, or document it.
- **Options.**
  - Standard: mode, depth (16-bit native), resolution 600/1200/2400/3600/7200, geometry, preview, `infra-red`.
  - Backend-specific: holder/frame, load/eject, possibly next-strip.

  Only expose what the backend itself has verified on hardware.
- **Out of the backend.** 110 film, dust removal, positive inversion and colour interpretation stay in the frontend or app (NG-2).

---

## 6. Protocol-replay testing approach

### 6.1 What we have and why it is not enough

| Asset | Usable for | Gap |
|---|---|---|
| 9 compiled traces (`traces/*.trace.json.gz`) | write order, poll counts, register sequences | derived/compiled; not `sanei_usb` XML |
| `of135i-trace/1` JSONL recorder | new captures of our own driver | OUT payloads capped at 64 B; bulk IN = lengths only; interrupts not converted |
| Offline harness | Python regression | bulk IN replays as zeros; no image correctness |
| 8 calibration binaries | numerical calibration regression | not wire-level |
| Wire-baseline hashes, startup fixture | order pinning | Python-side only |

So there is no existing artifact that can be converted losslessly into a full `sanei_usb` XML capture. Shading uploads (45,856 / 46,080 bytes) and image data are truncated or absent.

### 6.2 Plan

1. **Build the fork locally** with `--with-usb-record-replay` (libxml2).
2. **Synthetic first (no hardware).** Write a small converter from the compiled traces to `sanei_usb` XML for *control-only* sequences: startup, status reads, load and eject command streams. Use these to unit-test command emission and poll-predicate handling, including failure injection. Examples:
   - a status word that never satisfies the strict mask must latch the session failed;
   - after the latch, no further writes may occur.
3. **Real captures (hardware, owner present, [OWNER GO]).** Run the new backend under `fakeusbout:/path/capture.xml:<backend>:<device>`:
   - for 600 dpi only, the smallest geometry that exercises calibration and a few lines of image;
   - for load, eject and PARK.

   This produces complete XML, including bulk IN, with **no vendor software involved**. That provenance is clean: our backend, our device.
4. **Assertions.** `fail_test()` in `sanei_usb.c` is empty, so the test binary must assert explicitly on:
   - `sane_get_parameters` values;
   - SANE status codes;
   - image byte hash (or bytes);
   - `sane_cancel` behaviour mid-scan;
   - EOF;
   - "no writes after failure latch".
5. **Wiring.** Add the test program to `testsuite/backend/Makefile.am`, with captures in `EXTRA_DIST`. They must survive `make distcheck`.
6. **Capture size budget.** Higher-resolution captures (7200 dpi trays are about 14 minutes) are far too large for the repo. Test them on hardware only and log the results in docs. Do not fixture them.
7. **Provenance check on fixtures.** Captures contain the replayed command tables and our device's calibration data. Confirm under D-5 that publishing them is acceptable. They contain no vendor files, but they encode capture-derived programs.
8. **Not covered by replay:** physical timing, motor behaviour, lamp warm-up, cross-unit variation. State this in the manpage and MR.

### 6.3 Hardware test rules (inherited from our standing rules)

- Hardware testing only with the owner present and the owner's go.
- Stop `opticfilm-eject-watch.service` first and restart it after.
- Run read-only `of135i doctor` before the first command.
- Abort rule: if the motor grinds or stalls, **power OFF**.
- Never use `pkill` or `pgrep -f`.
- Schedule backend hardware sessions *between* production batches. A failed session needs a power cycle.

---

## 7. Phased plan with effort estimates

All estimates are **rough and UNVERIFIED**. They are engineering-days of Codex/API builder time plus our review, not calendar time. Hardware phases are additionally gated on owner availability.

| Phase | Content | Exit criteria | Est. | Hardware | Sends anything? |
|---|---|---|---|---|---|
| **A. Audit** | Provenance and licence audit: resolve U-4, U-5, U-6; inventory `sane/gl126*` notices and GL124-derived parts; draft `PROVENANCE.md`; fix licence hygiene in *our* fork (SPDX, `pyproject` discrepancy) via a normal PR | Audit doc; owner decides D-1/D-2/D-3 | 1–2 d | no | no |
| **B. Feasibility** | In a local branch of the owner's fork checkout: drop `sane/gl126*` into `backend/genesys`, add the ASIC enum and model entry, build at HEAD `22c9e5337` with `-Werror`, clang, `--without-usb`, `make distcheck` | Builds clean or a list of blockers; resolves U-2; go/no-go A vs B | 2–4 d | no | no (local only) |
| **C. Replay infra** | `--with-usb-record-replay` build; trace→XML control converter; test binary skeleton with explicit assertions; failure-latch tests | Control-path replay tests pass in `make check` locally | 3–5 d | no | no |
| **D1. Bring-up (A path)** | Rebase and adapt; `scanimage -L`; 600 dpi single frame; capture XML | Calibrated 600 dpi frame equivalent to the Python driver; capture saved | 3–6 d + 1–2 HW sessions | yes **[OWNER GO]** | no |
| **D2. Bring-up (B path, instead of D1)** | C port: USB, safety, runner, load/eject/PARK, calibration, scan/IR, geometry, SANE options, table generator | Same as D1 | 20–40 d + 3–6 HW sessions | yes **[OWNER GO]** | no |
| **E. Scope to v1** | Resolutions 600–7200, IR per D-8, holder/frame/eject options per D-7; replay fixtures for 600 dpi; manpage, `.desc`, newsfragment | Owner-approved feature list verified on hardware *through SANE*; full local CI matrix green | 5–10 d + 2–4 HW sessions | yes | no |
| **F. Pre-submission review** | Independent Codex review plus Opus API review of the licence, provenance and safety model; owner reads the MR text and manpage | Both APPROVE; owner approves wording | 1–2 d | no | no |
| **G. Upstream** | sane-devel contact; push to the GitLab fork; MR; respond to review | Owner decides each step | open-ended | maybe | **yes, only on [OWNER GO] per step** |

**Total to "ready to submit", excluding G:**
- **Option A:** about 15–29 engineering days plus 3–6 hardware sessions.
- **Option B:** about 30–60 engineering days plus 5–10 hardware sessions.

The table hours are guesses anchored on the recon's line counts. Upstream review time is unknown and not under our control.

**Priority:** below the production pipeline. That covers the remaining items in the in-flight STATE list:
- PR4 and the next-strip default (in flight);
- PR7a/7b;
- PR5 (in-app first load);
- PR8 and PR9.

Phases A–C do not touch hardware and can run in parallel with those PRs.

---

## 8. Risks

| # | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| R-1 | Licence/exception and authorship blocks the reuse of the existing C++ (§3.3) | med / high | Phase A first; D-2 decides; Option B fallback via MIT-derived port |
| R-2 | Reviewers reject large capture-derived opaque tables | unknown / high | Ship the generator plus documented pipeline; regenerate from our own captures; early sane-devel contact (on GO) |
| R-3 | GL126 identification wrong or unit-specific (U-1) | low–med / high | Document the evidence; claim only one tested unit; minimal `.desc` status |
| R-4 | Genesys generic flows mis-handle strict polls or the failure latch, leading to grinding or a stuck state | med / high (hardware) | Keep the driver's predicates verbatim; replay tests for failure paths before any hardware; abort rule |
| R-5 | Stale slope RAM or calibration buffers reproduce known grinding or bad shading | med / high | Mandatory slope re-upload; stale-calibration guard (note: in the driver this guard is itself **UNVERIFIED**, `cli.py:3482–3532`) |
| R-6 | Dual-light row alignment or IR delivery doesn't fit the SANE frame model | med / med | D-8 decided early; follow the genesys IR-source precedent |
| R-7 | Hardware sessions disrupt production or leave the scanner in need of a power cycle | med / med | Schedule between batches; doctor first; watcher stop/restart |
| R-8 | Replay fixtures embed material with publication restrictions | low–med / med | Only our-backend captures; D-5 review before any push |
| R-9 | CI matrix failures (`-Werror`, musl, no-USB, distcheck file lists) | high / low | Reproduce the CI configs locally in Phase B |
| R-10 | Our driver docs lag our evidence (four-slot tray UNVERIFIED in the README vs verified in STATE), leading to inconsistent claims | high / low | The SANE `.desc` and manpage cite SANE-path evidence only |
| R-11 | Upstream process unknown (U-7); long or no review | unknown / med | Treat upstream acceptance as a bonus; the local backend still has value |
| R-12 | Bus factor: one unit, one tester, maintenance after merge | high / med | State it plainly; owner decides on maintainer commitment (D-9) |
| R-13 | Effort estimates wrong by 2× or more | med / med | Phase B go/no-go; re-estimate after D1/D2 bring-up |

---

## 9. What changes in our repos (all via normal PRs, our repos only)

- **This plan:** a docs PR in `bearyjd/opticfilm-linux`.
- **Licence hygiene (Phase A):** a PR in *our* driver fork. Never pushed to upstream `cgillinger`.
- **SANE work:** a local branch in the owner's sane-backends fork checkout. It is separate from `~/.cache/fastfoto-linux-scratch/sane-backends` (branch `ff680w-usb-quirks`, FastFoto/epsonds work). Do not mix the two series in one branch or MR.

---

## 10. Decisions needed from the owner

| ID | Decision | Options | Blocking phase |
|---|---|---|---|
| **D-1** | Pursue SANE at all, and at what priority relative to the production pipeline? | go now (A–C in parallel) / later / never (Option D) | all |
| **D-2** | Gillinger: may we contact him about the SANE port (co-authorship, linking exception, his own plans), given the standing "never touch upstream cgillinger" rule? | contact / don't contact (implies Option B MIT-derived rewrite and no reuse of his GPL-only C++ without his consent on the exception) | after A |
| **D-3** | Licence header for new code | GPL-2.0+ with SANE exception (SANE norm) / GPL-2.0+ only | A |
| **D-4** | Architecture after Phase B | A genesys / B standalone / C private wrapper only | after B |
| **D-5** | What may be published: captures of our backend, compiled traces, calibration binaries, generator, provenance text | per item | before G |
| **D-6** | Contact Plustek for protocol information, as the SANE guide suggests? | yes / no | before G |
| **D-7** | v1 scope | e.g. 35 mm slides tray only vs plus strip holder; load/eject/next-strip exposed as options; which resolutions | E |
| **D-8** | IR delivery model | separate IR pass / cached IR from one dual pass / no IR in v1 | E |
| **D-9** | Who maintains the backend after a merge, and whose name and email goes in `AUTHORS`, the manpage and sane-devel mail | owner / other | G |
| **D-10** | Hardware time budget for backend bring-up | number of sessions; never during production batches | D1/D2 |
| **D-11** | When and how to talk to SANE | sane-devel first vs MR first; timing | G |

**Default if the owner says nothing:** do nothing beyond merging this document. Phases A–C require D-1, and every outbound step requires its own explicit go.
