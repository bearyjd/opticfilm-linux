---
title: Design the scanner pipeline for 3600 dpi or higher
author: Bearyjd
date: 2026-10-25
standing: true
---

## Decided
- The user's real scans will be at 3600 dpi. The scanner may reach 7200 dpi.
- Backend, memory use and scratch files must handle that size. Keep scratch files on disk with bounded memory.
- 600 dpi runs are only for safe testing.

_Told to Claude in 2 past sessions here, most recently on Oct 22._
