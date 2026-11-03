# Phase C — local GL126 offline replay scaffolding

Date: 2026-11-02. Branch: `ot135i-feasibility`. Source clone:
`/home/user/.cache/fastfoto-linux-scratch/sane-backends`.

## Result

Implemented and verified GL126-specific Genesys unit coverage, independent
capture-derived wire oracles, a compiled-trace-to-XML converter, and actual
`sanei_usb` XML execution. The model remains **UNREGISTERED**. This closes the
immediate Phase B gap 6 for the tested control programs and failure paths;
it does not establish a registered, functional scanner or finish every broader
Phase C plan example.

GCC no-USB: **3311 assertions, 0 failures**. GCC USB/XML: **3329 assertions,
0 failures**, including three real XML replay runs. Clang no-USB: **3311
assertions, 0 failures**. Each is the existing `genesys_unit_tests` executable,
run through Genesys `make check` (1/1 executable passed). Final commands use
`CXXFLAGS=-Werror`. Initial full GCC builds also used `CFLAGS=-Werror`; targeted
Clang C++ rebuilding avoids the unchanged C baseline warning documented in
Phase B. No whole-tree or musl success is claimed.

Evidence is `<clone>/.phase-c/evidence/`: `rebuild-nousb.log`,
`rebuild-usb.log`, `rebuild-clang.log`, `check-nousb.log`, `check-usb.log`,
`check-clang.log`, `fixture-verification.log`, `distribution.log`,
`style-check.log`, and `automake.log`. Per-assertion logs remain in each
`.phase-b/build-{nousb,usb,clang}/testsuite/backend/genesys/genesys_unit_tests.log`.

## Changes and assertions

- `testsuite/backend/genesys/tests_gl126.cpp`, wired into the existing unit
  executable: status decoding for all 256 status bytes; extended 0x100/0x101
  register setup, write payload, invalid acknowledgement and transport errors.
  An injected `IUsbDevice` constructor exercises production
  `ScannerInterfaceUsb` register handling without a physical USB object.
- Narrow GL126 dispatch: status address 0x101 in `low.cpp`, extended register
  reads/writes in `scanner_interface_usb.cpp`. Other ASIC branches are retained.
- Strict masked/data-ready/class timeout tests use virtual time and assert
  `OpsFailure::PollTimeout` and failing op index. Bad acknowledgement, short bulk
  IN/OUT assert their specific failure codes. Every failing synthetic program
  has a trailing write which must remain unreachable. A masked success permits
  differences outside the mask; a best-effort timeout continues.
- Tests exposed missing persistence after calibration failure. A per-device
  terminal latch and guarded GL126 command hooks now cover protocol errors,
  strict timeout, plain OUT failure and initial state-read failure. The real
  calibration entry point is retried after each injection; initialization,
  begin/gain/load/eject retries cannot write. Strict phase and magazine timeouts
  map to `SANE_STATUS_IO_ERROR`. Existing invalid-request mappings remain.
- `genesys.cpp` now routes GL126 cancel directly through its pass decision and
  skips generic home/eject/power saving. Tests invoke actual
  `sane_cancel_impl`, including repeated cancel, and check scanning/read flags.
- GL126 close uses the shared `gl126::close_session` helper, which discards
  software maps and closes the transport without lamp-off, clear-halt or reset.
  Tests invoke this same helper and assert close=1, reset=0, halt=0, no extra
  writes, and a retained failure latch. Closing an active/unparked pass marks
  the device object failed before dropping pass state. Failed/aborted PARK
  decisions also preserve terminal failure.
- The latch deliberately survives same-object init and close. A transient cold
  status cannot reset it. Recovery requires a power cycle and backend restart
  (device reconstruction), which the error message states. No recovery write
  or automatic latch-reset mechanism was introduced.

The close test covers the shared cleanup helper called by real
`sane_close_impl`; it does not fabricate a registered GL126 handle or claim a
complete `sane_open`→`sane_close` API test.

## Independent trace replay

Source, read-only:
`/var/home/user/Documents/vibe-code/plustek-slide-scanner/.local-research/publish/driver-latest/traces/20260905-vendor-clean-load.trace.json.gz`.
Compressed source SHA-256:
`5c99f58c6b1d513fd72111400fb1114e96aa7a03ea364be87bb1cb468b672bed`.

| Program | Raw half-open op range | Control OUT | Control IN | Bulk OUT |
|---|---:|---:|---:|---:|
| OPEN | [37,88) | 23 | 28 | 0 |
| JOG | [88,170) | 26 | 50 | 6 |
| LOAD | [194,260) | 21 | 41 | 4 |

`gl126_trace_to_fixture.py` reads the compiled JSON directly; it imports neither
the driver nor generated C++ tables. Files under
`testsuite/backend/genesys/data/gl126/` provide `.wire`, `.xml` and JSON
provenance metadata for each program, plus a README. Every control and bulk OUT
byte in these slices is present, including all ten complete slope uploads.
The C++ `run_program` executes its real generated command sequences against
these independent oracles and compares request/value/index, payload, length,
order and exact exhaustion. Deliberate payload corruption must fail.

The USB/XML build additionally enables replay **before** `sanei_usb_init` and
opens only the XML path. Every OUT is checked against the independent oracle
before forwarding to `sanei_usb`, and IN replies are compared explicitly,
because stock `fail_test()` is empty. A synthetic final IN sentinel proves the
XML cursor consumed precisely the program slice.

Polls are normalized to the last recorded reply once: the compiled traces
retain unique responses but not their original repetition order. These runs do
not reproduce captured timing or poll counts. Separate synthetic tests exercise
timeout predicates. The converter rejects bulk IN/unsupported transfers rather
than inventing image data. All nine regenerated files compare byte-identically;
missing-image rejection passes. All ten fixture/metadata/README files survive
the focused Automake `distdir` step verbatim; the converter and new C++ source
are in the distribution list. Full `make distcheck` was not rerun; Phase B's
stock localhost-socket limitation still applies.

## Reproduction

From the clone, using Phase B's existing configured build trees:

```sh
# Existing local container's installed Automake; no fetch/install/network:
host-spawn -no-pty /usr/bin/podman exec --user 1000 \
  --workdir /home/user/.cache/fastfoto-linux-scratch/sane-backends \
  scanner automake --foreign testsuite/backend/genesys/Makefile
# Repeat for kind=nousb, usb, clang:
make -C .phase-b/build-$kind/backend -j4 libgenesys.la CXXFLAGS=-Werror
make -C .phase-b/build-$kind/testsuite/backend/genesys -j4 check CXXFLAGS=-Werror
# Example independent fixture regeneration:
python3 testsuite/backend/genesys/gl126_trace_to_fixture.py TRACE 37 88 /tmp/open
make -C .phase-b/build-nousb/testsuite/backend/genesys distdir \
  distdir="$PWD/.phase-c/dist-root/testsuite/backend/genesys"
git diff --check
```

## Boundaries, failed attempts and remaining work

- An early mock confused basic reg 0x01 and extended reg 0x101 (shared index,
  different wValue), and injected OUT failure during a pre-write read. Those
  test-harness bugs were fixed; assertions now distinguish the actual paths.
  The first injected-transport build also exposed a helper typed to concrete
  `UsbDevice`; it now accepts `IUsbDevice`.
- A focused `distdir` initially used a directory too shallow for Automake's
  `../../../depcomp` path. The sandbox denied that out-of-root copy; using the
  normal `testsuite/backend/genesys` nesting succeeds. No external file changed.
- Standalone semantic cold-init/eject programs still lack independent captured
  equivalence oracles. JOG includes a captured eject movement but is not a
  standalone eject claim. Semantic eject intentionally differs from raw vendor
  framing, so no manufactured fixture was substituted.
- Image bytes/hashes, geometry, EOF, complete calibration and full SANE
  open/start/read/cancel/close lifecycle remain unverified. No registered model,
  generic scan integration or image-pipeline completion was added.
- Physical timing, sensors, motor behavior, lamp warm-up and cross-unit behavior
  require later, separately owner-approved hardware work. No hardware outcome
  follows from these offline passes.
- Fixtures contain capture-derived command facts and device EEPROM replies.
  They remain local material; Phase A provenance/publication clearance and
  owner approval are still required before any publication.

No network, physical USB/device discovery, hardware command, package install,
commit, push or external message was performed. Existing Phase B modifications
were preserved. `tables_model.cpp`, `tables_sensor.cpp`, `genesys.conf.in` and
`doc/descriptions/genesys.desc` still match local master; tests also assert that
neither 07b3:1436 nor any GL126 model occurs in the USB model table. The local
branch and HEAD remain unchanged. Source style checks and `git diff --check`
pass. Independent review is recorded separately by the parent reviewer.
