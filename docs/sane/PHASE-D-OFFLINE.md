# Phase D: GL126 / OpticFilm 135i offline integration

2026-11-03. Local work (committed on the local branch only, a21a2309b) in
`/home/user/.cache/fastfoto-linux-scratch/sane-backends`, branch
`ot135i-feasibility`, starting and ending HEAD
`8fd6c041f3dbb5916450bcc2972119c3708a9a2b`.

## Status and scope

The remaining Option A integration is implemented behind a **default-off
compile-time registration gate**. The offline tests cover the generic SANE
lifecycle, transport failures, strict motion waits and synthetic image geometry.
This completes the available offline implementation work, **not Phase D1's
hardware exit criterion**. No calibrated SANE frame has been obtained, and no
resolution is claimed hardware-validated through SANE.

No network, device discovery against real USB, hardware transfers, external
messages, commits, pushes, remote changes or master checkout changes were made.
Master remains `f4bd07353837374e076fa52b81e9bf6bb657264e`. Existing `.gitignore`,
`.ignore`, `.omc`, `.phase-b`, `.phase-c` and other initial scratch artifacts were
preserved. Build evidence and the repeatable local check script are under
`.phase-d/`. The source changes and new test fixtures are left for owner review.

The app checkout's requested `docs/sane` path was absent in this environment.
Inputs were read from the existing `scratchpad/prod-wt/docs/sane/` copies of
PHASE-B-FEASIBILITY, PHASE-C-REPLAY, DECISIONS-D4-D7-D8 and PROVENANCE, plus
`prod-wt/docs/superpowers/specs/2026-11-02-sane-opticfilm135i-backend-plan.md`
section 7. The donor remained read-only at
`/var/home/user/Documents/vibe-code/plustek-slide-scanner/.local-research/publish/driver-latest`.
No app-repository files were edited.

## Implemented

1. **Metadata and registration.** Appended ModelId/SensorId values preserve the
   numbers of existing entries. Added model and eight sensor records for
   600/1200/3600/7200 dpi, visible and IR. The asymmetric donor 2400 dpi profile
   remains in the captured tables but is not advertised. USB `07b3:1436` is
   registered only when `SANE_GENESYS_ENABLE_EXPERIMENTAL_GL126=1` is defined.
   A normal build has no matching model, including mock named-open tests.
   The model remains UNTESTED. No default discovery configuration or supported
   device `.desc` claim was added.
2. **Transport/status.** Retained Phase C's extended registers/status and
   frontend transport adaptations, and connected bulk image reads to
   `gl126::read_image_chunk_usb`. The GL126 source requests the captured chunk
   size, preserving the descriptor/ack/bulk-IN/done protocol rather than generic
   bulk segmentation. Actual USB is replaced by mocks or XML replay in tests.
3. **Generic-flow guards.** GL126 bypasses generic home/TA movement, generic
   shading and register uploads, calibration cache restore/save/files, and
   generic feed/data/home waits. It calibrates on every start via its own
   offset and gain/shading hooks. Its software motor coordinate base is
   initialized explicitly at 3600 dpi: borrowed ADC/GPO/motor table IDs are not
   looked up or sent to the scanner. Generic host/transport exceptions latch
   GL126 sessions, including failures before a calibration-stage record exists.
   Unknown exceptions become SANE IO_ERROR for GL126 only. Start/read and motion
   buttons reject failed sessions before I/O. Read errors report zero bytes.
   Failed open, cancel, repeated cancel and close cannot invoke generic reset,
   lamp, home, eject or recovery writes; closing preserves a terminal latch.
4. **Frontend scope.** Appended Film options preserve prior models' option
   indices and reported counts. V1 exposes tray status, explicit advanced
   `load-film`/`eject-film` button options, and integer `frame` 1–4. A scan
   requires a successful explicit load or matching loaded-state mark; the
   donor's unknown-state scan bypass was removed. Load never happens implicitly
   inside start. The donor next-tray transition after Ejected is refused
   read-only; power-cycle and explicitly load again. There is no next-strip
   option. The guard checks for cold state before refusing an Ejected mark, so
   a real power cycle can select the ordinary cold Load path; warm reload
   remains refused. Any button failure is terminal by design, including this
   read-only UNSUPPORTED refusal: power-cycle **and restart the backend/frontend**
   before loading again. A power cycle alone does not clear the in-process latch.
   IR is a separate `Transparency Adapter Infrared` source, Gray16,
   with no cross-scan cache. Captured full-frame geometry makes frontend crop
   controls inactive.
5. **Image path.** Added GL126-only alignment-size handling, alternating-row
   selection through `push_dual_light_nodes`, and trailing-height crop after
   channel alignment. Even rows are IR and odd rows visible. RGB16 crop stride
   was already correct in this SANE revision, so the donor's shared image-node
   change was unnecessary. No GL128 or other existing ASIC pipeline was edited;
   all new dispatch branches test GL126 explicitly.
6. **Strict eject.** Historical donor eject replay contains an advisory motor
   completion wait. The frontend adapter now makes that wait strict on a local
   program copy. Timeout latches and stops **before** the donor's final motor-off
   write. Non-motor advisory ready polls retain their documented donor semantics;
   they are not treated as motor-completion evidence. Strict motor polling,
   acknowledgement failures and transport failures stop all following writes.
7. **Documentation/provenance.** Updated `doc/sane-genesys.man` with the gate,
   tray/frame/source semantics, state mark and failure behavior. Restored the
   GL124 base's Voltz notice in gl126.cpp/gl126.h and Kanapickas notice in
   gl126_registers.h, retaining Gillinger's notice and GPL terms. This preserves
   inherited attribution; it does not resolve the memo's exception, authorship,
   publication or maintainer-policy decisions.

## Offline coverage and evidence limits

- Existing capture-derived OPEN/JOG/LOAD wire and XML oracles remain independent
  of the generated C++ tables.
- Added complete cold-init and eject wire/XML/provenance fixtures and
  `gl126_reference_to_fixture.py`. **These two new fixtures are Python-reference
  projections with scripted settled replies, not recordings from hardware.**
  Exact raw capture equivalents for the synthesized cold-init and semantic
  eject bodies do not exist in the available material. The generator runs the
  hand-written external driver bodies against a memory transport, prohibits
  discovery and bulk-IN, and records hashes of the reference modules. The
  external checkout is never modified or imported into `make check`.
- Injected timeout at all nine real cold-init motor polls. Tested the actual
  frontend eject adapter's strict timeout, required slope uploads, absence of
  the final motor-off transfer, persistent latch, zero retry reads, and
  write-free cancel/close. Additional real-adapter regressions distinguish warm
  Ejected reload refusal from cold reload after power cycle, including a state
  change between precondition reads. Historical raw donor eject semantics are separately
  retained in the reference-runner test and clearly distinguished.
- Real SANE open/options/start/read/EOF/cancel/close orchestration runs against
  mocks for all 4 frames × 4 advertised resolutions × 2 sources. Successful
  calibration/motion hooks are synthetic stand-ins; EOF is exercised by
  advancing the delivered-byte counter, rather than draining a complete scan.
  Tests inject failures at load, offset, gain/shading, session-register setup,
  begin and image read. They assert terminal retries and no generic writes.
  GL126 mock reads now traverse the actual image pipeline; the legacy mock
  shortcut is retained for other ASICs.
- Indexed synthetic RGB16 pixels verify both parities, channel alignment,
  multichannel horizontal crop stride, final vertical crop and chunk requests.
  A GL841 control proves the same GL126 fields do not alter its pipeline.
  Existing GL128 and all existing Genesys unit tests still run.
- **Full captured image replay is unavailable.** All nine available compiled
  scan traces omit bulk-IN bytes; the 600 dpi trace contains 4,770 length/timing
  bulk-IN records without payloads. Separate calibration binaries do not make a
  complete scan stream. No pixel bytes or whole-scan XML were invented. New
  supervised SANE XML containing the actual image transfers is required.

The default and enabled table tests, source review of guarded branches, and
unchanged legacy unit tests support regression containment. They do not prove
byte-for-byte behavior on every physical legacy scanner. No such hardware
regression claim is made.

## Local matrix

Commands and complete logs: `.phase-d/run-checks.sh` and
`.phase-d/evidence/final-*.log`. The script deliberately excludes the stock
`sanei_usb_test` (real USB initialization/discovery) and all saned/socket tests.
It runs Genesys tests, four safe SANEI tests, 14 sane-desc modes and the mocked
scanimage cancellation test. No omitted test was silently presented as passed.

| Configuration | Result |
| --- | --- |
| GCC no USB, experimental registration enabled, C/C++ `-Werror` | PASS; 5,250 assertions, zero failures; lifecycle and synthetic images exercised |
| GCC libusb + XML, default registration OFF, C/C++ `-Werror` | PASS; 3,662 assertions, zero failures; absent-model/named-mock-open checks and five XML programs |
| GCC libusb + XML, experimental registration enabled, C/C++ `-Werror` | PASS; 5,280 assertions, zero failures; lifecycle plus five real XML replay programs |
| Clang, default OFF, C++ `-Werror` | PASS; 3,632 assertions, zero failures, with the C-only baseline diagnostic exception below |
| Strict Clang C `-Werror`, forced original dll.c rebuild | FAIL: unchanged dll.c:817 and :923, `-Wgnu-folding-constant` |
| `make dist` and GL126 distribution file audit | PASS; 46 files present and byte-identical |

The Clang passing run uses CFLAGS=`-Werror -Wno-error=gnu-folding-constant`,
CXXFLAGS=`-Werror`. No compiler workaround was added to source. The strict
failure is preserved in `clang-c-baseline.log` and
`full-clang-strict-failure.log`; it is not a GL126 warning or a strict
whole-tree pass. GCC USB uses `-std=gnu11` as in Phase B.

Checks reuse the locally generated configure/Makefiles and existing offline SDK;
there is no package installation or download. Autotools is absent in this
sandbox. The ignored generated test Makefile.in was locally synchronized with
the changed Makefile.am file lists; a later normal autoreconf regenerates it.
The script forces the conditional table and test objects when changing CPPFLAGS
because make does not track flag changes. Initial stale-object mismatches were
diagnosed and corrected, not counted as passes.

Archive: `.phase-b/build-nousb/sane-backends-1.2.1.tar.gz`.
Audit: `.phase-d/evidence/dist-file-list.json`. Full stock distcheck was **not**
run: its saned path uses sockets and its installed synthetic-backend checks
require additional configuration. This turn performs the requested feasible
file-list checks, not a claimed install/uninstall/distclean completion. Exact
Debian/Fedora/musl CI reproduction remains separate; no cached musl compiler
was obtained or fetched.

## Remaining work requiring the scanner

Each step below requires a separately approved, supervised hardware session.
None was run. Keep the Python backend as production default and stop any
production scanner worker before attaching this experimental backend. Use only
the four-slot mounted-slide tray; do not test next-strip or additional holders.

1. Discovery/open only: confirm `scanimage -L` identifies exactly `07b3:1436`,
   inspect options, and verify open/close has no movement.
2. Explicit load: supervise cold initialization, release/reseat and LOAD; check
   status is `loaded -- set Frame, press Scan`. A load wait returning success
   does not itself prove the tray reached Loaded. Do not scan from a released
   or unknown status.
3. First target: calibrated **600 dpi, visible, frame 1**, save image and full
   USB XML including calibration/bulk-IN, compare dimensions, crop, orientation,
   channel alignment and calibration against the Python path on the same slide.
   No cache reuse or skip-calibration option is part of bring-up.
4. If the first frame is clean, validate explicit eject. Test later frames and
   IR parity/alignment only as separately supervised steps. Validate every
   advertised resolution before claiming it supported; omit IR from v1 if
   physical parity/alignment cannot be established. Investigate cross-process
   state with the real device; never clear software state to bypass a latch.
5. Project the newly captured whole-scan XML into a distributable offline oracle
   only after owner provenance/publication review. Add full lifecycle/pixel
   equivalence, measured timeout bounds and repeated cold/warm/reopen coverage.

### Proposed supervised commands — NOT executed

Prepare a dedicated USB/XML build with the experimental macro passed at
configure time so **all** objects share it. Use the existing local toolchain and
USB SDK configuration from Phase B; do not install over the system backend.
For example, run an out-of-tree configure with the Phase B USB flags and
`CPPFLAGS=-DSANE_GENESYS_ENABLE_EXPERIMENTAL_GL126=1`, then build. The USB build
must include `--with-usb --with-usb-record-replay`, `BACKENDS=genesys` and the
existing isolated dependency flags. Do not use the no-USB build for hardware.

A concrete isolated build proposal using the already installed/cached dependencies is:

```sh
repo="$HOME/.cache/fastfoto-linux-scratch/sane-backends"
build="$repo/.phase-d/hardware-build"
sdk="$repo/.phase-b/usb-sdk"
mkdir -p "$build"
(
  cd "$build"
  env CCACHE_DISABLE=1 CC=/usr/bin/gcc CXX=/usr/bin/g++ \
    CPPFLAGS=-DSANE_GENESYS_ENABLE_EXPERIMENTAL_GL126=1 \
    USB_CFLAGS="-I$sdk/usr/include/libusb-1.0" \
    USB_LIBS="-L$sdk/usr/lib64 -lusb-1.0" \
    XML_CFLAGS="-I/usr/include/libxml2 -DWITH_GZFILEOP" XML_LIBS=-lxml2 \
    "$repo/configure" BACKENDS=genesys --with-usb --with-usb-record-replay \
    --without-avahi --without-libcurl --without-poppler-glib --without-snmp \
    --without-systemd --without-v4l --disable-locking \
    CFLAGS="-std=gnu11 -Werror" CXXFLAGS=-Werror
  make -j4
)
```

Stop if configure does not confirm USB and XML replay support. Set the paths to
that dedicated build and a new capture directory before the supervised session:

```sh
capture="/tmp/gl126-phase-e-600-frame1"
mkdir -p "$capture/config"
printf 'genesys\n' > "$capture/config/dll.conf"
printf 'usb 0x07b3 0x1436\n' > "$capture/config/genesys.conf"
export SANE_CONFIG_DIR="$capture/config"
export LD_LIBRARY_PATH="$build/backend/.libs:$HOME/.cache/fastfoto-linux-scratch/sane-backends/.phase-b/usb-sdk/usr/lib64"
export SANE_DEBUG_GENESYS=5
export SANE_DEBUG_SANEI_USB=5

# STEP 1: discovery only, approved separately.
"$build/frontend/scanimage" -L 2> "$capture/discovery.log"
```

Stop and review discovery output. Copy the exact reported genesys device name
(not a guessed USB bus address), then inspect its descriptors:

```sh
device='genesys:libusb:BUS:DEVICE'  # replace with the exact discovered name
"$build/frontend/scanimage" -d "$device" --all-options > "$capture/options.txt" 2> "$capture/options.log"
```

The existing DLL's recording syntax is
`fakeusbout:XML-PATH:genesys:DEVICE`; it is parsed in `backend/dll.c` and records
through sanei_usb. Distinct invocations use distinct XML files:

```sh
# STEP 2: explicit supervised load/reseat only, approved separately.
"$build/frontend/scanimage" \
  -d "fakeusbout:$capture/load.xml:$device" \
  --load-film --dont-scan 2> "$capture/load.log"
"$build/frontend/scanimage" -d "$device" --all-options > "$capture/loaded-options.txt" 2> "$capture/status.log"
```

Stop and inspect tray state/logs before approving the calibrated frame:

```sh
# STEP 3: one calibrated frame only, approved separately.
"$build/frontend/scanimage" \
  -d "fakeusbout:$capture/frame1-600.xml:$device" \
  --source 'Transparency Adapter' --mode Color --depth 16 \
  --resolution 600 --frame 1 --format=pnm \
  > "$capture/frame1-600.pnm" 2> "$capture/frame1-600.log"
```

Only after inspecting the frame and clean completion, approve explicit eject:

```sh
"$build/frontend/scanimage" \
  -d "fakeusbout:$capture/eject.xml:$device" \
  --eject-film --dont-scan 2> "$capture/eject.log"
```

Do not run these as an unattended script or chain them with automatic retries.
The build path, loader paths, option spellings and DLL recording syntax are
proposals checked against local source, not hardware-validated commands.

**Abort rule:** at the first unexpected/grinding sound, stalled transport,
unexpected movement, bad acknowledgement, strict poll timeout, I/O error,
short image or unexplained image corruption, stop the current frontend. Cancel
once if responsive; if motion or noise continues, the operator cuts scanner
power. Do not issue load/eject/home/reset/retry as recovery, even if the next
process sees an apparently idle state. Preserve XML/logs, power-cycle and
restart the backend only after reviewing the failure and authorizing another
session. Do not bypass or delete the failure state to keep scanning.

## Final verification

Final independent rerun counts and residual limitations are appended below.
The USB build is returned to the default-off gate after enabled verification;
use the dedicated configured build above for a separately approved hardware
session. Do not infer an enabled configuration from cached object files.


Final independent verification completed with zero failures:

- GCC no USB, gate enabled: **5,250** assertions.
- GCC USB/XML, gate enabled: **5,280** assertions.
- GCC USB/XML, restored default-off gate: **3,662** assertions.
- Clang default-off: **3,632** assertions, with the documented C-only warning exception.
- All selected safe SANEI, sane-desc and mocked frontend cancellation tests pass
  in each final matrix run.
- All six cold-init/eject reference files regenerate byte-for-byte; evidence is
  `reference-reproducibility.json`.
- Both parent and independent reviewer verified all **46** listed source/test/doc
  archive payloads against the final tree, with no missing files or differences.
  Final tarball SHA-256:
  `ac0fc423b56b370e2de1365d6ae4b50cc1f3e5dd1fb248003363664c57ded2c5`.
- Generated translation edits were saved as evidence and restored byte-for-byte
  to HEAD. Generated Python caches were removed. `git diff --check` passes.
- Final branch/HEAD/master and changed-file inventory are recorded in
  `.phase-d/evidence/final-state.json`. No commits were created.

Independent review: `.phase-d/evidence/independent-final-review.md`. Independent
archive audit: `independent-dist-file-list.json`. Enabled USB results are retained
in `independent-usb-enabled.log`; the reusable USB build is now default OFF and
its final rerun is `independent-usb-default.log`. There are no pending local
build/test jobs. Remaining scanner, missing captured-image, strict Clang C, full
distcheck, musl/CI and publication limitations are exactly those described above.
