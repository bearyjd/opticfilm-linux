# OpticFilm 135i: Phase A provenance and licence audit

Local draft, 2026-11-02. Engineering evidence and unresolved rights questions;
this is not a legal opinion or permission to publish. Scope: section 3 and the
Phase A deliverable of
[`2026-11-02-sane-opticfilm135i-backend-plan.md`](../superpowers/specs/2026-11-02-sane-opticfilm135i-backend-plan.md).
Only this document is changed. No network, hardware access, contact, push,
commit, licence-header edits or driver-checkout writes are part of this audit.

## Sources and limits

`D:` below means the local driver checkout
`/var/home/user/Documents/vibe-code/plustek-slide-scanner/.local-research/publish/driver-latest/`,
at detached HEAD `eaa6f889f09796bc8f4ddab73cd5daa0bdb273fd`.
File:line references are relative to that checkout unless prefixed otherwise.
It already has an untracked `.omc/` directory; it is not audit output.

`S:` means SANE commit `22c9e5337`, read with `git show` from the local object
database at `/var/home/user/.cache/fastfoto-linux-scratch/sane-backends/`.
That checkout's working HEAD is a different commit (`f4bd073538`); the cited
SANE notices were checked at the plan's commit, without changing its checkout.
Git evidence is cited by commit and path, because commit metadata has no stable
file line number. No remote state has been verified.

The driver repository is **shallow**. HEAD-reachable history begins at boundary
`343065742a9980e7cd203024ae4035a29535a70d`, an import of 168 files. `git blame
--line-porcelain HEAD -- <file>` attributes every surviving line of the nine
SANE files and their generator to that boundary, author Christian Gillinger.
This is not proof he originally wrote every line. Other local refs expose older
development commits; `git log --all -- <paths>` was also examined. Git author,
copyright holder, generator output and AI assistance are separate evidence.

**Owner-supplied update (this task, 2026-11-02):** upstream maintainer
`cgillinger` has since ported our calibration stop gate onto master, credited
`bearyjd` as co-author, and asked about licensing (`GPL-2.0-or-later OR MIT`);
the owner agreed to the port. This supersedes the plan's assumption that no
upstream cooperation has occurred. That newer commit and exchange are not in
the inspected local evidence, so their exact hash, wording and licence scope
remain to be recorded. Agreement to a port is not evidence of a SANE linking
exception grant or a blanket relicensing of the existing C++.

## Findings that change the plan

- All nine GL126 files carry Gillinger's 2026 copyright and
  **GPL-2.0-or-later, without a linking exception** (`D:sane/gl126*.{h,cpp}:1–19`,
  enumerated below). The Python area's MIT option does not automatically cover
  those C++ files (`D:LICENSING.md:7–10`).
- The port notes explicitly say the initial `gl126.{cpp,h,_registers.h}`
  skeleton was **cloned from GL124** (`D:docs/sane-port.md:203–206`). This is
  stronger evidence of derivation than merely including Genesys headers.
  Exact surviving copied expression and missing upstream notices still need
  a comparison against the original base.
- The assumption that existing Genesys sources all carry a linking exception
  is incorrect: `S:backend/genesys/gl124.cpp:1–20`, `gl124.h:1–19`,
  `gl124_registers.h:1–19` and `genesys.cpp:1–32` do not. SANE explicitly says
  individual file notices prevail and not every backend has the exception
  (`S:LICENSE:3–5,17–25`). GPL-only reuse and an exception-bearing submission
  are different requirements.
- The quarantine is **not empty**. Despite its stale docstring,
  `D:of135i/vendor_derived.py:28–35` contains three SA-002-labelled values.
  Static analysis has also already informed a capture-confirmed SANE fix
  (`D:docs/static-analysis.md:39,55–66`). “No current quarantine imports” is
  supportable; “no static-analysis influence ever” is not.
- The whole driver is not solely authored by Gillinger: available history
  contains substantial `bearyjd` work, including the calibration stop gate.
  Copyright banners alone do not settle ownership of those contributions.

## Nine-file inventory and authorship

In every row, the actual header is © 2026 Christian Gillinger,
GPL-2.0-or-later, warranty disclaimer, **no SANE linking exception**. Each
file's `:1–19` is its own header citation. All ten paths (nine files plus
generator) have only Christian Gillinger as Git author across their 22 distinct
touching commits in available local refs; no `bearyjd`-authored commit touches
this set. HEAD blame has the shallow-boundary limitation described above.
This establishes recorded contribution provenance, not exclusive ownership.

| File and header | Recorded author / production | Derivation and evidence |
|---|---|---|
| `sane/gl126.h:1–19` | Gillinger; handwritten command-set interface | GL124-derived skeleton (`docs/sane-port.md:205`); Genesys inheritance/includes (`gl126.h:42–64`); capture-based flow (`gl126.h:29–36`). |
| `sane/gl126.cpp:1–19` | Gillinger; handwritten hooks | Same GL124 skeleton origin; uses Genesys and generated capture tables (`gl126.cpp:23–29`); Python-equivalent safety model (`gl126.cpp:52–64`). Not a wholly independent implementation of the SANE interface. |
| `sane/gl126_registers.h:1–19` | Gillinger; handwritten register definitions | Same skeleton origin; claims only capture-established names (`gl126_registers.h:21–26`); register/loader facts (`gl126_registers.h:35–60`). Facts and copied source expression must be distinguished. |
| `sane/gl126_ops.h:1–19` | Gillinger; handwritten runner interface | Ordered captured-transfer programs and calibration computations; deliberately Genesys-header-free (`gl126_ops.h:21–35`). No specific GL124 body-copy evidence located for this file. |
| `sane/gl126_ops.cpp:1–19` | Gillinger; handwritten runner implementation | Implements the above interface (`gl126_ops.cpp:21,33–64`); consumes programs emitted from Python captures (`tools/gen_sane_tables.py:30–70`). Header-free does not itself prove independent authorship. |
| `sane/gl126_mark.h:1–19` | Gillinger; handwritten magazine-state interface | Cross-process state and POSIX file-hardening design (`gl126_mark.h:21–64`); renamed from `gl126_lock` in v8 (`sane/wp3-package/README.md:20–28`). No specific GL124 copy identified. |
| `sane/gl126_mark.cpp:1–19` | Gillinger; handwritten magazine-state implementation | Own state-file implementation using POSIX headers (`gl126_mark.cpp:21–30,49–57`); same v8 ancestry. No vendor source derivation claimed by the local record. |
| `sane/gl126_tables.h:1–19` | Machine-generated by Gillinger-authored `tools/gen_sane_tables.py` | Explicit generated notice (`gl126_tables.h:21–32`), emitted types (`gl126_tables.h:43–65`), Python capture-table inputs. Generated source is not authorless or automatically public domain. |
| `sane/gl126_tables.cpp:1–19` | Same generator and author | Generated notice (`gl126_tables.cpp:21–32`), base table (`gl126_tables.cpp:39–44`), captured op programs plus synthesized operations described below. Not a transcription of vendor executable code according to project policy. |

Oldest visible commits are `7d7adf7` for `gl126.cpp`, `gl126.h`,
`gl126_registers.h`, both generated table files and the generator;
`96b2063` for both `gl126_ops` files; and shallow boundary `3430657` for
both `gl126_mark` files. Blame at older available commit `9f7a43c` also
attributes all lines of the eight available paths (excluding both mark files)
to Gillinger. The earlier mark implementation is unavailable in that history;
its rename ancestry comes from the package notes, not a complete Git chain.

The initial SANE skeleton/generator development is visible in `7d7adf7`;
later examples include geometry `8fd5528`/`d41c773`, and infrared fixes
`e28e3d3`/`9f7a43c`. Commit trailers record Claude Opus 5, Opus 4.8 and Fable
5.1 assistance respectively; the shallow import also names Fable 5.1.
These are AI-assistance records, not evidence of additional human rights grants
or of an independent clean-room process.

`tools/gen_sane_tables.py:1–111` has a technical docstring, **no per-file
licence header**. As a `tools/` Python file it falls under the stated
GPL-2.0-or-later OR MIT area (`LICENSING.md:7`), while the banner it emits is
GPL-only (`tools/gen_sane_tables.py:1309–1337`). Its Git author/blame findings
are the same as above. The generator's MIT availability does not by itself
relicense its checked-in output or erase rights in its inputs/templates.

For possible inherited attribution, the locally inspected GL124 sources name
**Stéphane Voltz** (`S:backend/genesys/gl124.cpp:3`, `gl124.h:3`) and
**Povilas Kanapickas** (`S:backend/genesys/gl124_registers.h:3`). These are
candidate inherited notices to trace, not a finding that all their code survives
in GL126. Genesys core has further authors (`S:backend/genesys/genesys.cpp:3–16`).
The GL126-only Gillinger banners do not establish that upstream attribution
was correctly preserved.

## Driver-wide licence and contribution boundaries

| Area | Stated licence / evidence | Limits and hygiene |
|---|---|---|
| `of135i/`, `tools/`, Python `tests/*.py` | GPL-2.0-or-later **OR** MIT (`LICENSING.md:7`; `LICENSE-MIT:3–13`) | Preserve copyright and the full MIT permission notice when selecting MIT. Later contributors' applicable grants must also be established. No blanket MIT grant for `sane/`. |
| `sane/`, C++ `tests/*.cpp` | GPL-2.0-or-later (`LICENSING.md:8`) | Includes integration patches/package and probes; inspect inherited notices for each eventual submitted patch. GPL source can be modified/redistributed with its conditions; permissive relicensing is not granted. |
| `docs/` | CC BY 4.0 (`LICENSING.md:9`; `docs/LICENSE:1–19`) | Attribute copied/adapted prose and indicate changes. Descriptions of protocol facts are distinct from the facts; avoid treating documentation's broad legal assertions as a clearance opinion. |
| Build/config, shell scripts, udev, other material | Default GPL-2.0-or-later (`LICENSING.md:10`) | Area policy supplies a default, not item-specific provenance. Missing headers do not mean public domain. |
| `traces/`, `cal-data/capture/` | No separate asset grant identified; default policy is the only located general statement | Independently confirm capture ownership, permissions, privacy and rights in retained payloads before distribution. |
| `of135i/data/srgb.icc` | Profile generated through Little-CMS/Pillow (`of135i/image.py:64–69`) | Plan §3.2 reports embedded “No copyright, use freely”; this audit does not clear third-party profile/dependency rights. Not needed for backend submission. |

`pyproject.toml:6` advertises only GPL-2.0-or-later despite the Python MIT
option. Python/tool/probe files sampled here lack legal headers; the existing
plan's exact headerless-file counts have not been re-certified. These are
documentation/metadata cleanup candidates, not changes authorized here.
`CONTRIBUTING.md:18–21` states inbound contributions follow the area licences;
that is useful evidence, not a copyright assignment or proof each contributor
accepted every later policy version. Dependencies (`pyproject.toml:8`) need
their own notices if bundled; this is not a dependency-by-dependency audit.

Git shortlog at HEAD reports bearyjd 92 commits, Christian Gillinger 18, and
`cgillinger` 2 (same email as Gillinger); across available local refs the counts
are 97, 287 and 17 respectively. Counts include merges and shallow imports;
they are neither percentages of authorship nor a complete rights-holder list.
The statement © Gillinger (`LICENSING.md:12`; `README.md:683–690`) cannot
override surviving third-party rights or the recorded later contributions.

Specifically, bearyjd authored the stop gate (`2df5878201458d72a7e286370b9eedf8a0774cae`),
deadline correction (`c7aca15ab011a57206810381357e8039ef7430a0`), shared-poll
budget correction (`f462b0bc971d3606cf87bed877d2db2020ba7d2c`) and default-on
change (`006012db31328cdf259b1b12c24c6c542c3788f2`). These local commits have
no co-author trailers; they are separate from the newer upstream port reported
by the owner. Current behaviour is documented at `of135i/device.py:231–239`
and `_cal_wait_scanstop = "enforce"` at `of135i/device.py:747`.

## Capture, INI and static-analysis provenance

The documented setup is QuickScan in a Windows 11 VM, with Linux-host usbmon
capturing traffic from the physical scanner (`tools/gen_load_table.py:4–7`).
The general reverse-engineering account is `README.md:634–652`. This supports
a capture-derived implementation, not a strict two-team clean-room claim.
One author's capture and analysis work feeds his implementation. Capture
ownership and lawful acquisition are project assertions, not independently
proved by this desk audit.

- **USB captures:** register batches, USB transfer order, poll replies, motor
  slopes, image-transfer lengths and calibration injection points. Compiled
  traces retain control and bulk-OUT bytes, but bulk-IN lengths rather than
  image payloads (`tools/compile_trace.py:2–14,26–31`). Base-table constants
  claim byte-exact capture verification (`of135i/tables_base.py:1–6`). This
  does not establish a copyright exemption for every ordered table/program.
- **Vendor INI:** initial encoding and register dumps were cross-checked with
  `Scanapi_07b3_1436.ini` (`docs/protocol-notes.md:3–4,20–25`); loader-sensor
  mapping is explicitly attributed to vendor config
  (`docs/protocol-notes.md:669–671`; `of135i/device.py:2454–2463`). Do not
  describe all protocol knowledge as learned solely from captures. No vendor
  INI file is needed for the documented C++ generator.
- **Vendor DLL analysis:** policy allows facts only, forbids copied code and
  code structure (`docs/static-analysis.md:14–22`). SA-001 concerns
  `Scanapi_x64.dll` 1.4.12, function `0x180086250`; it led back to own
  captures and a missing reg-0x32 write, confirmed in Python and SANE by
  Test 98 (`docs/static-analysis.md:39,55–66`). SA-002 concerns the loader
  event thread at `0x180137760` and hardware Test 97
  (`docs/static-analysis.md:40`). Historical influence remains even after a
  value is promoted into capture-derived code.
- **Current quarantine:** three SA-002 hypotheses, `REG101_PRESENT_BIT`,
  `REG101_MOTOR_BIT`, `WATCH_REGS` (`of135i/vendor_derived.py:28–35`). The
  leading “empty” docstring is stale (`:3`), and SA-002's documented measured
  status is not identical to those hypothesis labels. Promotion requires own
  capture plus logged hardware confirmation (`docs/static-analysis.md:24–33`).
  The import-isolation tests can establish a current boundary, not absence of
  all historical vendor influence or a clean-room certification.

Publication restrictions remain explicit: do not publish vendor
INI/plist/framework files, pcaps or decoded personal photos
(`docs/driver-design.md:195–199`). No tracked `.pcap`/`.pcapng` was found in
the driver. Their absence neither proves capture lineage nor authorizes
publishing other derived data. Do not attach private raw inputs to a submission.

## Table-generation chain and reproducibility

1. Own-device vendor traffic → usbmon pcaps → tshark field dumps → gzip JSON
   op lists via `tools/compile_trace.py:2–17,34–50`. Schema: `cw`, `cr`,
   coalesced `poll`, `bo`, `bi` with `dt`; bulk-IN image content is omitted.
2. `tools/gen_tables.py:2–24,44–51,78–95` chooses phase boundaries and live
   injection sites for plain/IR and multi-DPI Python tables. DPI modules
   identify the input trace and generator (e.g. `of135i/tables_dpi600.py:4–19`);
   some POSITION phases are synthesized from multiple trace sections, not
   replayed unchanged. `tools/gen_load_table.py:2–39` separately derives
   OPEN/JOG/LOAD from the clean 2026-09-05 load trace. Available history names
   Gillinger for these scripts (initial release `0ad5720`, DPI `09f3ca1`,
   load revisions `80e76c9`, `1d38cf5`, `a679224`); their Python-area licence
   is GPL-2.0-or-later OR MIT (`LICENSING.md:7`).
3. `tools/gen_sane_tables.py:124–145` imports those modules plus device,
   holder and image helpers. It emits flattened register-pair tables **and**
   ordered op programs retaining reads, polls and boundaries
   (`tools/gen_sane_tables.py:11–70`). Thus the generated banner's claim
   “Reads and polls are not reproduced here” is incomplete for modern output
   (`sane/gl126_tables.h:25–28`).
4. Calibration/shading injections replace unit-specific captured values;
   covered bulk payloads are stripped, while other captured replies/payloads
   may remain as provenance (`tools/gen_sane_tables.py:53–66,82–106`).
   Cold-init is constructed from Python's capture-derived implementation,
   not a captured Phase (`tools/gen_sane_tables.py:1006–1023`). Generated
   source combines captured facts, selection/structure, templates and
   synthesized operations; it is not just a neutral numeric dump.
5. Generator → `sane/gl126_tables.{h,cpp}` with its explicit GPL-only
   banner (`tools/gen_sane_tables.py:1309–1337`). Changes to output notices
   would also need corresponding generator-template changes after rights
   clearance; hand-editing output alone would be overwritten.

Read-only checks on 2026-11-02 used `PYTHONDONTWRITEBYTECODE=1 python -B`:
`tools/gen_sane_tables.py --check` reported **generated SANE tables are up to
date**, and `tests/test_vendor_isolation.py` reported **4 tests passed**.
The check branch compares in memory and returns before the write branch
(`tools/gen_sane_tables.py:1891–1909`); isolation checks examine import and
generation paths and source mentions (`tests/test_vendor_isolation.py:39–79`).
This verifies Python-input-to-C++ reproduction and current isolation only.
Raw-pcap-to-Python reproduction and byte-exact agreement with original vendor
traffic were not reverified. No normal generation command was run.

## Relicensing boundaries and an exception-bearing SANE submission

Existing GPL-2.0-or-later grants permit compliant copying, modification and
distribution; they do not grant us authority to remove other authors' notices,
convert GPL-only expression to MIT, or add permissions for somebody else's
GPL-only work. GPL terms on preserving notices and modified distribution are
in `D:LICENSE:78–105`. No assignment was located. The owner can grant rights
only for contributions they own or are authorized to license, including their
stop-gate work; co-author credit does not transfer rights in either direction.

An MIT-derived implementation may be distributed under compatible GPL terms
while retaining MIT copyright and permission notices (`D:LICENSE-MIT:3–13`).
That provides a possible route for Python-derived work with an explicit SANE
exception on new contributions. It does not make the existing GPL-only C++
MIT, nor clear copied Genesys expression or every generated input. Rebuilding
tables from MIT inputs needs a documented choice of inputs, templates and
licences, rather than a new label on existing output.

For the requested **GPL-2.0-or-later plus SANE linking exception** submission:

1. Identify the exact contributed files and base commit, including changes in
   integration patches. Preserve all inherited notices and exceptions that
   actually apply. Compare the GL124 skeleton and surviving code against the
   original SANE base; resolve Voltz/Kanapickas and other inherited attribution.
2. Obtain documented authority covering each relevant contributor's original
   expression for the exception, or use an independently justified permissible
   source/reimplementation route. Gillinger's own new code and bearyjd's own
   code can have separate grants; neither alone settles third-party GPL-only
   portions. No permission is inferred from the recent port discussion.
3. Use the actual SANE exception text as the reference
   (`S:backend/dll.c:18–37`): it addresses linking, preserves other GPL
   reasons, and addresses submissions/modifications. Preserve GPL, copyright,
   warranty and MIT notices as applicable. Adding an exception to GL126 files
   alone does not establish that the entire Genesys library is exception-covered.
4. Ask the owner to resolve the intended submission's exception scope before
   any future contact. A universal mandatory-exception policy is **not proved**
   by local sources; the existing GPL-only Genesys sources make that an open
   question. A GPL-only contribution may still be compatible with existing
   Genesys, but cannot be advertised as meeting the requested exception model.
5. Prepare a source/input manifest with hashes, generator and input schema,
   capture setup, retained/synthesized data distinctions, static-analysis
   promotion evidence, AI-assistance disclosure where required, and permission
   to publish each asset. Keep prohibited raw/vendor material out. Have the
   owner review attribution and rights wording before any submission.

Local driver documentation already records an earlier SANE submission and v8
package (`D:docs/sane-port.md:39–42`; `D:sane/wp3-package/README.md:3–11,34–40`).
This audit creates no new submission and verifies no online status. That series
is additional historical evidence to reconcile, not proof that the current
files' licence/attribution questions have been accepted or resolved.

## Unresolved protocol assumptions to carry forward

- First image-read buffer descriptor `wIndex=8`: semantics unknown and retained
  verbatim (`D:of135i/usbio.py:436–444`; `D:docs/sane-port.md:538–539`).
- Reg 0x101 masks 0x02/0x10/0x20 remain unnamed in current driver code
  (`D:of135i/device.py:555–567`). Older GL124-name inferences
  (`D:docs/protocol-notes.md:688–707`) are not confirmed GL126 semantics.
- Valid-words/scan-count registers and their role in Genesys reads remain an
  explicitly recorded risk (`D:docs/sane-port.md:532–535`); that historical
  list is not a current hardware-verification report.
- Opaque register writes and cross-unit calibration/geometry assumptions
  remain; naming only observed registers is deliberate
  (`D:sane/gl126_registers.h:21–26`). Capture reproduction does not explain
  every byte, establish cross-unit safety, or certify current SANE behaviour.

## Open questions for the owner

1. Can you preserve the newer upstream stop-gate commit hash and licensing
   exchange locally, including exactly what the port agreement granted? Was
   the dual licence explicitly agreed for bearyjd's contribution, and was
   any SANE linking exception discussed? No contact is authorized by this audit.
2. Who owns each bearyjd contribution, and are there employer, collaborator or
   other rights to account for? Can the intended dual licence/exception grant
   be recorded explicitly without implying ownership of Gillinger's work?
3. Which SANE base was the initial skeleton copied from, what copied expression
   remains, and which upstream notices must be restored? Can fuller history
   and the v8 series be audited locally before selecting the submission tree?
4. Do we actually need an exception-bearing standalone backend, or is a
   GPL-only Genesys extension the intended target? Who can authorize the
   exception for all affected portions, if the former remains required?
5. Who made and owns each capture, using what device/software versions and
   permissions? Which compiled traces, calibration binaries and derived tables
   may be published under an explicit grant? Raw pcaps/vendor files remain
   restricted; a request for provenance is not publication permission.
6. Can the INI-derived mappings and SA-001/SA-002 promotions be reconciled with
   their capture/test evidence, including the stale quarantine documentation
   and hypothesis labels? Is outside rights review needed for any retained
   capture-derived program or vendor-analysis result?
7. Which attribution, metadata and generator-header fixes should be prepared
   later, and under which licences? This Phase A document neither changes
   those files nor settles the answers on behalf of their rights holders.
