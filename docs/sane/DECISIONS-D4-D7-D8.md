# Decision memo: SANE backend for OpticFilm 135i (D-4, D-7, D-8)

- **Date:** 2026-11-02
- **For:** Owner
- **Status:** Local draft. Nothing here is sent, pushed or contacted.
- **Inputs:** Plan (2026-11-02), Phase A provenance audit, Phase B feasibility report.

---

## Summary

| Decision | Recommendation |
|---|---|
| **D-4** Architecture | **Option A: add GL126 to `backend/genesys`.** Keep B as a fallback if an exit trigger fires. C stays private only. |
| **D-7** v1 scope | **4-slot mounted-slide tray only. Frames 1–4.** Resolutions are added only as each is verified through SANE. **Load/eject as button options. Next-strip deferred.** |
| **D-8** IR | **IR as a separate source ("Transparency Adapter Infrared").** It is one dual-light pass that returns the IR plane. Caching comes later. If parity/alignment can't be verified in Phase E, **ship v1 without IR.** |
| GUI | Keep the Python driver as the default. Add a SANE implementation behind a scanner-backend interface. Map holder moves, frames and IR onto SANE options. |

---

## D-4: Architecture after Phase B

### Options

- **A. Genesys GL126 ASIC.** Reuse the existing `sane/gl126*` code.
- **B. Standalone `opticfilm135i` C backend.** Port from the Python driver.
- **C. Private wrapper** around the Python driver.

### Recommendation: A

Reasons:

1. **Phase B found no compile-level reason to rewrite.**
   - The nine GL126 files build against genesys with `-Werror` after one adaptation: removing the obsolete `exposure_lperiod` assignment.
   - The required integration touched only five files (Makefile, enums ×2, settings, low.cpp).
   - Both GCC builds passed: no-USB, and USB + XML replay. The Clang C++ build passed.
   - The single Clang failure is in the unchanged `dll.c` (`-Wgnu-folding-constant`), not in GL126.
2. **B costs far more for no demonstrated gain.**
   - B is estimated at 20–40 engineering days plus 3–6 hardware sessions for bring-up alone.
   - Phase B puts the remaining Option A budget at about 14–27 days.
3. **The licensing picture is less blocking than the plan assumed.**
   - The GL126 files are GPL-2.0-or-later without the linking exception.
   - However, the genesys files they derive from (`gl124.*`, and `genesys.cpp`) also carry no exception at SANE `22c9e5337`.
   - SANE's `LICENSE` says each file's own notice prevails.
   - So a GPL-only genesys extension may be acceptable. This is **UNVERIFIED with maintainers (U-7, U-8)**.
4. **Upstream has now cooperated.** The owner reports that `cgillinger` ported our stop gate, credited `bearyjd` as co-author, and asked about licensing. That weakens the main reason for B, which was "no contact, so rewrite". It does **not** grant an exception or a relicence.

### Conditions attached to A

- **Restore inherited notices.** The GL126 skeleton was "cloned from GL124". Any surviving GL124 expression needs its upstream notices back (Voltz, Kanapickas) before any submission.
- **Owner decisions stay separate.** D-2 (Gillinger) and D-3 (header) remain owner decisions. Choosing A does not settle them.

### What would change this to B

- **Phase C replay tests show the latch can't be guaranteed.** The risk is that genesys generic flows can't guarantee strict polling and *no writes after a failure*. One known conflict: current non-sheetfed startup invokes home, but GL126 `move_back_home` refuses to.
- **SANE maintainers require the exception for a genesys addition, and it cannot be obtained** for Gillinger's code and for any GL124-derived portions.
- **Genesys maintainers reject a capture-table-driven ASIC.** This would come out of sane-devel contact, which is **[OWNER GO]**.
- **The full rebase breaks GL128 or other models,** and the breakage can't be contained.

### Risks of A

- **The donor patch does not apply.** It is 1,211 lines across 17 files and conflicts with HEAD. The remaining work is a real rebase.
- **Placeholder IDs.** The ADC, GPIO and motor IDs are borrowed from the OpticFilm 7200. Nobody has yet proved that generic genesys paths never consume them.
- **Registration is the dangerous step.** Registering USB `07b3:1436` before the transport, status and guard work lands would make unsupported generic paths reachable on real hardware.
- **CI coverage is incomplete:**
  - musl is untested;
  - the Clang `dll.c` baseline is unresolved;
  - `distcheck` stopped at the sandboxed localhost `saned` test.
- **The build was tested on a different commit.** Phase B ran on local master `f4bd0735`, not the plan's `22c9e5337`. No fetch was done, so remote HEAD is unknown.
- **The quarantine is not empty.** It holds three SA-002 values, and static analysis has already influenced one SANE fix (reg 0x32). Provenance text must say so.

---

## D-7: v1 scope

### Options

- **Holders:**
  - 4-slot mounted-slide tray only; or
  - tray plus strip and other holders.
- **Resolutions:**
  - all five tables (600/1200/2400/3600/7200); or
  - only those verified through SANE.
- **Holder actions:**
  - implicit, inside `sane_start`;
  - explicit options; or
  - not exposed.

### Recommendation

**Holder: 4-slot mounted-slide tray only.**
- It is the only holder with hardware evidence in our STATE.
- Even that evidence came from our app and fork, not through SANE. The driver README still says the four-slot workflow is UNVERIFIED.
- The donor magazine code claims up to six frames. That is not evidence of holder coverage.
- **Unknown:** which other holders the app supports, and what hardware evidence exists for them. The inputs do not say.

**Frame selection:** an integer `frame` option, 1–4, following the coolscan3 `frame`/`frame-count` model.
- Phase B notes the compiled frame field only defaults to 1 and is not yet wired to any control.

**Resolutions: none are claimed until each passes a SANE-path hardware session.**
- **First target: 600 dpi.** It is the bring-up and replay-fixture size in plan §6.
- **Second target: 7200 dpi.** It is the production-proven setting through the Python driver (827–854 s, `poll_timeouts=0`).
- **1200 and 3600:** add if verified in Phase E.
- **2400:** ship only if exposed honestly as 3600 × 2400, using separate x/y resolution options. Otherwise omit it from v1.
- **Unknown:** what hardware evidence exists for 1200, 2400 and 3600 on any path.

**Load and eject:** `SANE_TYPE_BUTTON` options, advanced capability, with no implicit motion inside `sane_start`.
- Explicit actions keep the safety model legible.
- They let the GUI sequence motion.
- They match "no automatic writes after failure": any button-action failure latches the session, and later calls return `SANE_STATUS_IO_ERROR`.
- **Unknown:** how `scanimage` presents button options in practice. Check this in Phase E.

**Next-strip: not in v1.**
- The Python-path evidence is five swaps ending at `0xdc55`.
- The next-strip default is still in flight (PR4).
- Add it as a further button only after it is verified through SANE.

**Magazine state.** Cross-process state is held by `gl126_mark`, a hardened state file in `/tmp`. Each `scanimage` call is a separate process, so this file is how state carries between calls. Document it in the manpage, and validate its POSIX calls on musl.

### What would change this

- **Add holders** if a SANE-path hardware session verifies load, all frames and eject on them.
- **Add next-strip to v1** if it passes in a SANE session before Phase E closes.
- **Move to implicit load in `sane_start`** if testing shows frontends can't drive the button options usably. This needs Phase E hardware evidence that implicit motion is safe with the latch.

### Risks

- **Narrow scope.** A tray-only v1 can't serve the GUI's full batch workflow. That is acceptable, because production stays on the Python driver (NG-1).
- **Uncommon pattern.** Motion triggered from `sane_control_option` is unusual and may draw review comments.
- **Mixed-driver desync.** If the Python driver moves the holder, the `/tmp` mark state can go out of sync with the hardware. See the GUI section.

---

## D-8: IR delivery model

### Facts

- **Hardware:** dual-light mode delivers interleaved rows, with even rows IR and odd rows visible.
- **SANE:** RGBI frame constants are disabled (`#if 0`). Genesys precedent is a separate source, "Transparency Adapter Infrared".
- **Tables:** plain and IR are separate scan programs (ten sensor variants: five resolutions × transparency/IR).
- **Alignment:** the backend must apply `round(24*dpi/7200)` with both ends cropped, which needs a bounded line buffer.
- **Code status:** the donor's `push_dual_light_nodes` and parity fields exist, but are not connected in Phase B.

### Options

| Option | Pros | Cons |
|---|---|---|
| **Separate IR source** | Stateless; matches genesys precedent; every frontend understands it | RGB+IR means two physical passes; IR-to-RGB registration across passes **unmeasured** |
| **Cached IR from one dual pass** | One motion; IR rows inherently aligned with RGB | Invisible to standard frontends; cache invalidation (options, frame, cancel, latch); memory size unknown |
| **No IR in v1** | Smallest scope; avoids risk R-6 | Loses a feature the GUI relies on |

### Recommendation

**v1 exposes IR as the "Transparency Adapter Infrared" source.** It runs one dual-light pass and returns only the aligned IR plane, as 16-bit gray.

Reasons:
- It follows existing genesys convention.
- It keeps no state across `sane_start` calls, which suits the failure latch and cancel handling.
- It exercises the same dual-light path a cache would need later.

**Cached delivery is a post-v1 optimisation.** It must keep the same option semantics and needs Phase C replay tests for invalidation.

**Fallback:** if parity and alignment can't be verified on hardware through SANE within the Phase E budget, **ship v1 with no IR** rather than delay v1.

### What would change this

- **Prioritise caching over the separate source** if a hardware session shows the two-pass approach misregisters IR against RGB beyond what dust processing tolerates.
  - The tolerance is **unknown**; the app's dust code defines it.
  - The same applies if dual-pass time at 7200 dpi is unacceptable. Per-frame timing is **unknown**.
- **Fall back to no IR** if Phase C or E can't validate row parity.

### Risks

- **Time:** two passes roughly double time for RGB+IR. This is an inference; it is not measured.
- **Accuracy:** the alignment crop is done in the backend, so a parity error yields silently wrong IR.
- **Hardware unknown:** whether the hardware has an IR-only lamp mode, as opposed to dual-light. Not stated in the inputs.

---

## How the GUI would use a SANE backend later

1. **Add an abstraction layer.** Introduce a scanner-backend interface in the GUI.
   - **Today's Python driver** remains the default implementation (NG-1).
   - **A SANE implementation** is added alongside it, via a libsane binding or a `scanimage` subprocess. The binding choice is open.
2. **Map current GUI features to SANE:**

   | GUI feature | SANE mechanism |
   |---|---|
   | Discovery | `sane_get_devices` |
   | Holder load and eject | Button options |
   | Next-strip | Button option, once added |
   | Slot selection | `frame` option |
   | Resolution | Resolution option(s), limited to the verified list |
   | IR | `source` = "Transparency Adapter Infrared" (later: cached) |
   | Raw 16-bit data | `sane_read` → app pipeline |

   The GUI should build its controls from option descriptors, so unverified features simply don't appear.
3. **Tray sweep is a structural unknown.** Does today's driver capture the tray in one sweep and crop apertures in the app?
   - If so, a per-frame SANE model changes the motion pattern and timing.
   - Options are a "whole-holder" geometry, or accepting per-frame scans.
   - Check the driver before Phase E.
4. **The batch pipeline stays in the app.** Inversion, dust removal, 110 reconstruction, aperture crop, colour and JPEG output all stay there (NG-2). The pipeline consumes SANE frames instead of driver buffers.
5. **Failure handling must match the safety model.**
   - On `SANE_STATUS_IO_ERROR`, show the power-cycle instruction.
   - **Never retry automatically.**
6. **Exclusivity:**
   - Never run both drivers against the scanner concurrently.
   - Stop `opticfilm-eject-watch.service` as the standing rules already require.
   - Do not move the holder with one driver and continue with the other. The `/tmp` mark state is not shared with the Python driver. **Unknown:** whether the Python driver keeps any equivalent state.
7. **When to switch:** only after SANE-path evidence covers what the batch workflow needs:
   - load and all frames;
   - next-strip;
   - eject;
   - the production resolution;
   - IR.

   Until then, SANE is an interop path for other frontends, not a replacement.

---

## Open items the owner should know about

- **Verify the Phase B result at the plan's commit.** The plan cites `22c9e5337`; Phase B built on `f4bd0735`.
- **Record the upstream exchange locally.** The recent `cgillinger` port and licensing exchange should be saved, with its scope. It is not in the inspected evidence.
- **SANE's exception policy for genesys additions is unverified,** as is its stance on table-driven ASICs. Both need sane-devel contact, which is **[OWNER GO]** and covered by D-11.
