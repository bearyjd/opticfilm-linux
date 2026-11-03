# Phase B: GL126 / OpticFilm 135i feasibility

Date: 2026-11-02. Local, offline, compile-only investigation.

## Decision

**GO for Option A as the engineering path; NO-GO for registering or using the hardware yet.** The existing GL126 implementation fits current Genesys C++11 interfaces with small compile adaptations. GCC builds with `-Werror` both without USB and with USB plus XML replay. Clang builds with C++ `-Werror`; its strict whole-tree C build encounters an existing `dll.c` warning described below. Nothing found justifies a standalone C rewrite on compilation grounds.

This is not a claim that the complete CI matrix or GL126 scan path passes. musl was not build-tested, and most donor runtime integration remains unapplied. Provenance/licensing decisions remain in Phase A; this report does not resolve them.

## Inputs and isolation

- Only repository changed: `/home/user/.cache/fastfoto-linux-scratch/sane-backends`.
- New local branch: `ot135i-feasibility`, created with `git switch -c ot135i-feasibility master`.
- Both branch HEAD and local master: `f4bd07353837374e076fa52b81e9bf6bb657264e` (`canon_dr: remove accidentally added directory and empty file.`). No new commits. This is the requested local master HEAD, **not** the older `22c9e5337` cited in the plan and not `ff680w-usb-quirks`. No fetch was performed, so no assertion about current remote HEAD is possible.
- The named plan was absent from the main scanner project checkout. Read section 5 Option A and Phase B from the local copy at `scratchpad/sane-wt/docs/superpowers/specs/2026-11-02-sane-opticfilm135i-backend-plan.md`, under the same session directory as this report.
- Donor: `/var/home/user/Documents/vibe-code/plustek-slide-scanner/.local-research/publish/driver-latest/sane/` (read only).
- All evidence and build directories: `<clone>/.phase-b/`. Evidence paths below are relative to that directory's `evidence/` subdirectory.
- Pre-existing `.gitignore`, `.ignore`, `.omc/`, `backend/.omc/` and `.tarball-version` preserved. The pre-existing version file labels the generated tarball `1.2.1`; the source identity is the SHA above.
- No network fetching, package installation, pushes, remote changes or external messages. The stock distcheck saned test attempted a localhost socket and was denied by the sandbox; no network communication succeeded. No hardware registration, physical device discovery, real-scanner command or USB operation. Stock tests use synthetic backends only. USB-enabled artifacts were compiled/linked, not run. Executed tests used no-USB builds. Existing local containers supplied installed build tools/files; the Alpine probe explicitly used `--pull=never --network=none --read-only` with no source or device mounts.

## What was adapted

Copied nine files: four `.cpp` files and five headers. Donor total is 30,799 lines: 24,990 in `gl126_tables.cpp`, 5,809 elsewhere. Adapted copy totals 30,797 lines. SHA-256 hashes and individual counts are in `source-manifest.json`.

Only five existing source/build files changed for integration:

1. `backend/Makefile.am`: four GL126 C++ units and all five headers in `libgenesys_la_SOURCES`, alongside existing GL128 files.
2. `backend/genesys/enums.h`: append GL126 after GL128, preserving existing enum values; declare ASIC stream output.
3. `backend/genesys/enums.cpp`: ASIC stream output, including GL126.
4. `backend/genesys/settings.h`: frame selection and dual-light parity fields.
5. `backend/genesys/low.cpp`: include and command-set factory dispatch.

In copied `gl126.cpp`, remove the obsolete exposure-period assignment to/from fields absent in HEAD; update its comment. The captured GL126 exposure tables remain unchanged. This is sufficient for compilation, not proof of equivalent runtime exposure control.

No ModelId/SensorId or USB model entry was added. `tables_model.cpp`, `genesys.conf.in` and `doc/descriptions/genesys.desc` remain identical to master. The user instruction against hardware registration takes precedence over the plan's model-entry step. GL128 and Epson/FF680W source changes were not imported. See `adaptation-notes.md`, `git-invariants.json` and `independent-review.md`.

## Results and CI implications

| Check | Result | Evidence / boundary |
|---|---|---|
| Donor integration patch against HEAD | Does not apply cleanly | `patch-check.log`: conflicts in Makefile, enums, genesys core/header, pipeline, low, USB dispatch, settings; GL128 and exposure changes overlap |
| Original donor `gl126.cpp` with minimal header integration | Fails | `gl126-original-probe.log`: line 1995 references nonexistent `SetupParams::exposure_lperiod` and `Genesys_Settings::exposure_lperiod` |
| Adapted four units, GCC C++11 `-Wall -Werror` | Pass | `gl126-probe.log`, `gl126_{tables,ops,mark}-probe.log` |
| Full GCC build, CI CONFIGURE_NONE, BACKENDS=genesys | Pass | `configure-nousb-regenerated.log`, `make-nousb.log`; actual Genesys shared library and archive produced |
| Full GCC USB + XML replay build, BACKENDS=genesys | Pass | `configure-usb.log`, `make-usb.log`; ELF depends on libusb and libxml2, see `usb-elf-dynamic.txt` |
| Strict Clang C/C++ `-Werror`, no USB | Fails in unchanged C source | `make-clang.log`: `backend/dll.c:817,923`, `-Wgnu-folding-constant` |
| Clang after C-only diagnostic workaround | Pass | `make-clang-c-baseline-workaround.log`; C++ still `-Werror`, including GL126 |
| GCC no-USB `make check` | Pass after config-header restoration | `check-nousb-restored.log`; first attempt and recovery described below |
| GCC and Clang Genesys unit suite | Pass, 1/1 test executable each | `check-genesys-nousb.log`, `check-genesys-clang.log`; existing tests, no registered GL126 sessions |
| Style check on all new/adapted sources | Pass | `style-check.log`; `git diff --check` also passes |
| `make dist`, GL126 file lists | Pass, all nine files present | `make-dist.log`, `dist-gl126-members.txt` |
| `make distcheck` | Blocked in stock localhost saned test | First restricted attempt: `make-distcheck.log`; corrected attempt: `make-distcheck-with-test.log` |
| Alpine/musl | Not build-tested | `musl-toolchain-probe.log`: cached Alpine 3.20.10 has no C/C++ compiler/make; shell, dev and scanner environments also lacked a musl compiler |

The passing Genesys-only `make check` skipped stock `saned.sh` and `scanimage.sh` because the synthetic test backend was not enabled. The corrected distcheck enabled it and exposed the sandbox-denied localhost socket described below. Existing unit passes therefore do not imply those end-to-end tests passed.

These are focused Genesys builds. They do not reproduce Debian 11/12, Fedora 39 or Alpine 3.18 CI images, every backend, or all CONFIGURE_FULL libraries. Host compilers are GCC 15 and Clang 21; SANE selects C++11. The USB build enables libusb/XML, but optional TIFF/JPEG and other FULL dependencies are absent locally. No GL126-specific GCC or Clang C++ warning remains in the exercised builds.

### Errors by category

- **Rebase/API:** the 1,211-line donor integration patch spans 17 existing files and predates overlapping HEAD changes. It cannot be applied wholesale. The exposure assignment is a demonstrated compile failure; enum/factory/settings additions and source lists are also required. Rebase must retain GL128 behavior.
- **Compiler/CI baseline:** Clang 21 treats the existing `dll.c` folded variable-length arrays as errors under `-Werror`. `dll.c` is unchanged from master. A C-only `-Wno-error=gnu-folding-constant` allows the build; this is diagnostic isolation, not a claim that unmodified CI is green. No workaround was added to repository sources.
- **Build environment:** initial default compiler selection hit the shell's ccache wrapper; explicit `/usr/bin/gcc` and `/usr/bin/g++` avoid it. Host shell lacks Autotools and libusb development files. Existing `scanner` container supplies Autotools and installed libusb headers/library, copied into `.phase-b/usb-sdk/` without installation or USB activity.
- **Bootstrap:** `autogen.sh` returned success but its two patch calls reported missing `patch` in that container. Both repository patches were subsequently applied with the shell environment's installed `patch`. Autoconf obsolete-macro and GNU make/manpage-rule warnings are unrelated to GL126.
- **Generated header:** initial `make check` logged `Generating ../include/sane/config.h from .../config.h.in` through the generic `.in` rule, replacing configured macros with the template and causing `u_char`/`sigprocmask` errors. Running `config.status include/sane/config.h` restored it; targeted and full checks then passed. The precise timestamp trigger was not isolated. No source workaround was applied.
- **Packaging/installcheck:** first distcheck selected only `genesys`, so its installed synthetic scanner check failed with `open of device test failed: Invalid argument`. The tarball had already rebuilt and passed `make check`; the synthetic `test` backend must also be selected for installcheck. The corrected attempt with `genesys test` rebuilt the tarball and passed Genesys tests, then failed in stock `testsuite/saned/saned.sh`: its attempted localhost socket creation returned `Operation not permitted` under the sandbox. No network access succeeded. The restriction was not relaxed. Thus full install/uninstall/distclean distcheck completion remains unverified; the observed blocker is the network-requiring test, not a missing GL126 source file.
- **musl:** an evidence gap, not a demonstrated GL126 failure. GL126 uses `sanei_usb`/C++11 and POSIX file APIs rather than directly including libusb or glibc-only headers. `gl126_mark.cpp` uses `gmtime_r`, `O_NOFOLLOW`, `O_CLOEXEC`, `fstat` and `/tmp` state. Their actual target build and behavior still need validation; glibc compilation does not prove musl compatibility.

## Commands used

Run from the clone unless a subshell changes directory. Logs retain compiler commands and diagnostics.

```sh
git switch -c ot135i-feasibility master
# Copy gl126*.cpp and gl126*.h only from the donor directory.
# Probe the supplied integration patch with registration hunks excluded:
git apply --check --verbose .phase-b/evidence/integration-no-registration.patch

# Existing local container, no fetching or package installation:
host-spawn -no-pty /usr/bin/podman exec --user 1000 \
  --workdir /home/user/.cache/fastfoto-linux-scratch/sane-backends \
  scanner sh -c './autogen.sh > .phase-b/evidence/autogen.log 2>&1'
patch -N ltmain.sh patches/ltmain.sh.patch
patch -N po/Rules-quot patches/Rules-quot.patch

# Common CONFIGURE_NONE flags used below:
NONE='--without-usb --without-usb-record-replay --without-avahi --without-libcurl --without-poppler-glib --without-snmp --without-systemd --without-v4l --disable-locking'
(cd .phase-b/build-nousb && env CCACHE_DISABLE=1 CC=/usr/bin/gcc CXX=/usr/bin/g++ \
  ../../configure BACKENDS=genesys $NONE)
make -C .phase-b/build-nousb -j4 CFLAGS=-Werror CXXFLAGS=-Werror
(cd .phase-b/build-nousb && ./config.status include/sane/config.h)
make -C .phase-b/build-nousb -j4 check CFLAGS=-Werror CXXFLAGS=-Werror
make -C .phase-b/build-nousb/testsuite/backend/genesys -j4 check CFLAGS=-Werror CXXFLAGS=-Werror

# Offline USB SDK copied from installed files, then extracted under .phase-b/usb-sdk:
host-spawn -no-pty /usr/bin/podman exec --user 1000 scanner \
  tar -chf - /usr/include/libusb-1.0 /usr/lib64/libusb-1.0.so /usr/lib64/libusb-1.0.so.0 \
  > .phase-b/usb-toolchain.tar
tar xf .phase-b/usb-toolchain.tar -C .phase-b/usb-sdk
(cd .phase-b/build-usb && env CCACHE_DISABLE=1 CC=/usr/bin/gcc CXX=/usr/bin/g++ \
  USB_CFLAGS=-I/home/user/.cache/fastfoto-linux-scratch/sane-backends/.phase-b/usb-sdk/usr/include/libusb-1.0 \
  USB_LIBS='-L/home/user/.cache/fastfoto-linux-scratch/sane-backends/.phase-b/usb-sdk/usr/lib64 -lusb-1.0' \
  ../../configure BACKENDS=genesys --with-usb --with-usb-record-replay \
  --without-avahi --without-libcurl --without-poppler-glib --without-snmp \
  --without-systemd --without-v4l --disable-locking CFLAGS=-std=gnu11)
make -C .phase-b/build-usb -j4 CFLAGS='-Werror -std=gnu11' CXXFLAGS=-Werror

(cd .phase-b/build-clang && env CCACHE_DISABLE=1 CC=/usr/bin/clang CXX=/usr/bin/clang++ \
  ../../configure BACKENDS=genesys $NONE)
make -C .phase-b/build-clang -j4 CFLAGS=-Werror CXXFLAGS=-Werror
make -C .phase-b/build-clang -j4 CFLAGS='-Werror -Wno-error=gnu-folding-constant' CXXFLAGS=-Werror
make -C .phase-b/build-clang/testsuite/backend/genesys -j4 check \
  CFLAGS='-Werror -Wno-error=gnu-folding-constant' CXXFLAGS=-Werror

make -C .phase-b/build-nousb dist
# Initial failing distcheck used BACKENDS=genesys. Corrected command:
env CCACHE_DISABLE=1 CC=/usr/bin/gcc CXX=/usr/bin/g++ CFLAGS=-Werror CXXFLAGS=-Werror \
  make -C .phase-b/build-nousb -j4 distcheck \
  DISTCHECK_CONFIGURE_FLAGS="BACKENDS='genesys test' $NONE"
```

Direct probes also used `/usr/bin/g++ -std=c++11 -Wall -Werror -fPIC`, the no-USB generated include directory, `include/`, `backend/`, `backend/genesys/`, and `-DBACKEND_NAME=genesys`, to compile each GL126 `.cpp` independently. The original donor `gl126.cpp` was compiled with these same include paths to isolate the exposure mismatch. No compiled GL126 command set was instantiated against hardware.

## Gap to a registered, functional 07b3:1436 model

**A USB ID is the small part.** The donor patch supplies most missing code, but it must be rebased and verified together before activation:

1. **Metadata:** ModelId/SensorId names and stream cases; model record and USB mapping; ten sensor variants (five resolutions × transparency/IR). The donor requests 16-bit output, RGB line shifts, `DISABLE_SHADING_CALIBRATION | HOST_SIDE_GRAY`, and a deliberately non-sheetfed model. Its borrowed OpticFilm 7200 ADC/GPIO/motor IDs are placeholders; the assertion that generic paths never consume them needs offline proof. Discovery config and truthful `.desc`/documentation follow only after explicit scope expansion.
2. **Transport/status:** GL126 extended-register handling for 0x100/0x101, status reader, frontend writes, descriptor/ack/bulk image reads. Current `scanner_read_status` throws unsupported and the USB dispatcher lacks GL126 handling. The factory switch alone is insufficient.
3. **Generic-flow guards:** current non-sheetfed startup invokes home, while GL126 `move_back_home` explicitly refuses it. Rebase donor guards for homing/TA motion, default shading upload, calibration-cache reuse, register programming, scan waits and close/cancel cleanup. Safety depends on the complete call path preserving strict polling and no writes after failure; compilation does not establish this.
4. **Film/frontend:** load/eject/status/frame options and propagation. The compiled frame field merely defaults to 1; no controls are wired in this branch. Donor magazine code claims up to six frames; that does not establish hardware coverage of the application's holders.
5. **Image path:** connect `push_dual_light_nodes`, per-chunk USB reading, geometry/crop handling and IR/visible parity. Existing GL128 and other model pipelines must remain unchanged. Resolve IR delivery and 2400-dpi geometry against the plan.
6. **Offline coverage:** rebase transport mocks, add GL126-specific success/failure tests, failure-latch/strict-poll tests, and control replay. Current passing Genesys tests do not exercise a GL126 model. Full image replay and later owner-approved hardware validation remain separate phases.

`independent-review.md` confirms these gaps independently. Registering a model before this work would make unsupported generic paths reachable; this feasibility branch intentionally leaves registration absent.

## Final workspace checks

Build-generated changes to 26 tracked `po/*.po` translation files were saved as `generated-po.patch` and restored byte-for-byte from HEAD. Only the five integration files listed above, nine GL126 copies and `.phase-b/` artifacts are additions to the initial workspace state. `git diff --check` passes. No commits were created, and hardware-registration/Epson source files match local master. The original branch remains untouched. Independent final review: APPROVE, with build limitations and source scope checked against the evidence; see `independent-review.md`.

## Effort re-estimate

Engineering-day estimates remain judgment, not measured delivery guarantees:

- The narrow compile question is answered in this local investigation. It does **not** require a new 6–10k-line C backend.
- Budget **2–4 additional days** for rebasing the rest of the donor integration, auditing generic dispatch/guards against GL128, and closing exact CI/musl/packaging reproduction. A metadata-only registration edit is roughly **0.5–1 day within that**, but would not satisfy functional readiness.
- Keep **3–5 days** for Phase C offline replay and failure tests. Compile success does not reduce this safety work.
- Keep **3–6 days plus 1–2 owner-approved hardware sessions** for Option A bring-up, **5–10 days plus further sessions** for v1 scope, and **1–2 days** for final review. No hardware work was authorized or performed here.
- Remaining Option A engineering budget: approximately **14–27 days**, excluding any unresolved Phase A work, owner wait time and upstream review. The prior 15–29-day overall estimate should not be sharply reduced just because the compiler port is small. A standalone rewrite's estimated 20–40-day port is not warranted by the observed compiler/API gap.

Before treating Option A as CI-ready: obtain an offline musl C++ toolchain (or later authorize exact CI reproduction), resolve the strict Clang C baseline in the intended compiler image, and validate the complete GL126 integration without enabling real devices. No upstream action is proposed or authorized by this report.
