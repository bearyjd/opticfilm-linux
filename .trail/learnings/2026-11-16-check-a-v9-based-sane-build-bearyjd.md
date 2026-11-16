---
title: "Check a v9-based SANE build against the repo tests from before the holder option, not current master"
author: Bearyjd
date: 2026-11-16
branch: docs/trail-learning-sane-v10-trim-20261116
cost: { minutes: 34, tokens: 48654 }
id: l-684e3633
scope: repo
status: active
src: agent
touches:
  - ".omc/STATE.md"
  - docs/HARDWARE-DAY.md
---

## Decided
The held SANE v10 candidate 0008 (logging) was cut to the DBG lines and the timed_out record only; the offline-test mock hooks and the commit-message claim about a probe and golden files were dropped, because the maintainer reviewed it and nothing in the series used or contained them (2026-11-16). The probe and goldens exist only in the owner's local checkouts, not in his repository.
In the code: patches `sane/wp3-package/next/0008-*.patch` and `0009-*.patch` on branch sane-v10-prep (cgillinger repo PR #84); trimmed 0008 touches only `backend/genesys/gl126.cpp`, `gl126_ops.cpp`, `gl126_ops.h`. Built on the v9 tip 18bea9b32 via the bundle in `sane/wp3-package/`.

## Tried and ruled out
Running current master's tests (4efb5bc) against a v9-based build: `test_sane_magazine` fails 2 and `test_sane_open_params` fails 1, because those tests now check the `holder` option and `Genesys_Settings::slide_holder` added in repo commit e14cf9e (PR #87). The v9 series has neither. Use tests from e41f2e9 (42 and 7 pass) for a v9-based build.

## Watch out
`SANE_BACKENDS_DIR=<clone with the series applied and libsane-genesys built> python3 -m pytest -q tests/test_sane_magazine.py tests/test_sane_open_params.py`; build order lib, sanei, then backend. Pushing to PR #84's branch changes what the maintainer sees upstream: needs the owner's go and the final text shown first.
