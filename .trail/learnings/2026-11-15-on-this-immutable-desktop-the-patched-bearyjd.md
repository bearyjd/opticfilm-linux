---
title: "On this immutable desktop the patched scanner backend runs from the home directory, not installed"
author: Bearyjd
date: 2026-11-15
branch: main
cost: { minutes: 14, tokens: 339096 }
id: l-c12f623a
scope: repo
status: active
src: agent
---

## Decided
The owner's Bazzite desktop cannot take the SANE backend install (`/usr/lib64/sane` is read-only even with sudo), so run the patched build from the home directory: `~/sane-135i/lib/libsane-genesys.so.1` symlinked to the build, `~/sane-135i/etc/{dll.conf,genesys.conf}`, and `source ~/sane-135i/env.sh` (LD_LIBRARY_PATH, SANE_CONFIG_DIR, SANE_DEBUG_GENESYS=4) in that terminal only. Confirmed 2026-11-15: `scanimage -L` lists the OpticFilm 135i through our build.
In the code: build is `.local-research/publish/sane-backends` (sane-backends 1d47d7c + `sane/gl126-integration.patch`, gl126 sources symlinked from the `.local-research/publish/upstream-master` checkout at 4efb5bc); `tools/sane_install.sh install` is for mutable systems only.

## Tried and ruled out
`sudo tools/sane_install.sh install` here: dies at "not writable" before changing anything. cgillinger's Test 125 install worked because his machine is mutable Fedora.

## Watch out
Build order is lib, sanei, then `backend libsane-genesys.la`, after symlinking all nine gl126 files; skipping either fails (the old build.sh printed BUILD-OK even when no library was produced, so check the .so exists). genesys.conf needs `usb 0x07b3 0x1436`.
