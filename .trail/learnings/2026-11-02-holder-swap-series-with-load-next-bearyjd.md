---
title: Holder swap series with load --next-strip passed on hardware (2026-11-01)
author: Bearyjd
date: 2026-11-02
branch: docs/ux-design-and-plan
cost: { minutes: 27, tokens: 674947 }
touches:
  - ".omc/STATE.md"
---

## Decided
2026-11-01, owner present: five consecutive swaps (eject, owner swaps and seats the holder, `of135i load --next-strip`) all completed (eject 1-2 s, load 5-6 s, status 0xdc55, idle-homed), with no jog and no reinsert prompt. A 600 dpi slide-1 scan after each swap framed the slide in the same place (right edge identical, top edge within 3 px, about 0.13 mm), and a full 7200 dpi four-slide tray after the series scanned clean (854 s, poll_timeouts=0 on every frame). So `load --next-strip` is safe to make the default swap path.

## Tried and ruled out
Piping stdin into the interactive cold `of135i load`: the driver says a pipe ends it at the prompt (exit 130) and requires a real terminal; a PR5-style in-app first load needs a pty or a read-write FIFO, not stdin=PIPE.

## Watch out
One unit, one operator, five swaps: a solid series, not a reliability figure. Cold first loads through the app were not tested in this series. Stop the Eject watcher before driver commands and restart it afterwards.
