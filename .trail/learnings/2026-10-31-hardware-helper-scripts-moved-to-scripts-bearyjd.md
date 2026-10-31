---
title: Hardware helper scripts moved to scripts/hardware
author: Bearyjd
date: 2026-10-31
branch: chore/hardware-scripts
cost: { minutes: 44, tokens: 114013 }
touches:
  - scripts/hardware/hw_run.sh
  - scripts/hardware/README.md
---

The supervised hardware helpers (hw_run.sh, ab_gate_test.sh, tray_and_armc_test.sh) now live tracked in scripts/hardware/, replacing the old .local-research/tools/ copies. hw_run.sh needs OPTICFILM_DRIVER_DIR; the two driver scripts refuse to start without a real terminal confirmation unless OPTICFILM_HW_ASSUME_YES=1 is set by a supervising wrapper.
