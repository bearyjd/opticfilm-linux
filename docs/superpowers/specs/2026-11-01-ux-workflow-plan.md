# UX workflow plan: fewest clicks per tray, bulk selection, restyle

Status: plan for the owner's review. Date: 2026-11-01. Nothing here is built. Mode: DELIBERATE
(irreversible raw deletion and motor-adjacent sequencing are in scope).
Companion design: `docs/superpowers/specs/2026-11-01-hands-free-scan-to-jpeg-design.md` (called
"the hands-free design" below). This plan references its states and does not re-specify them.

Out of scope: the scanner driver and every motor path. Every holder move below is an existing
driver command (`eject`, `load`, `load --next-strip`) run exactly as today. The only new driver
interaction is feeding a newline to the stdin of the existing interactive `of135i load`. The
terminal flow does the same thing.

---

## (a) The current click path for one tray (from the code)

### Facts from the code that shape everything below

- `state.build_state`: `can_scan` has **no** `jobs_running` term, and `holder._reserved_scan`
  refuses only on `_holder_busy`. A scan **already overlaps** an export or free-space job.
- `runner.BatchRunner` has separate `_scan_pool` and `_enhance_pool`. Enhancing tray N **already
  overlaps** scanning tray N+1.
- The blocker is the holder. `holder._holder_refusal` returns `WAIT_EXPORT` while
  `_long_jobs or _job_futures`, and `can_eject`/`can_load_next` exclude `jobs_running` (PR #41,
  `tests/test_app_holder_export_lock.py`). The PR text gives no technical reason: the export job
  touches only the store and the CPU, never USB.
- `controller._act_on_eject_button` **drops a front-panel Eject press silently** when `can_eject`
  is false. Today a press during an export or free-space job does nothing and shows nothing.
- `review.accept_all` and `export.export_batch` are batch-wide. There is no per-tray or per-slide
  filter.
- `ExportDialog` defaults to `lastExportFormat` = pref or `"tiff16"`, so the first JPEG export
  needs a format change.
- `ExportDialog` result text is `"Done. %1 slides were written."`. With nothing pending it says
  0 and gives no reason. Only a settings or mode change clears `exported`
  (`export._mark_exported`).
- `LoadHelpDialog` steps 1-6 begin with a power cycle and a terminal paste. `Load next holder`
  is disabled unless `OPTICFILM_NEXT_STRIP=1` (`Controller._next_strip`).
- `controller._on_stage` auto-selects a tray only when `_selected is None`. A new tray's slides
  are not shown unless the owner clicks it in `TrayQueue`.
- Perf (`docs/performance.md`, 7200 dpi, 4 slides): batch enhance 42-68 s, export 32-39 s,
  peak RSS 5.4 GB per slide. Hardware tray scan at 7200 dpi: 827 s. Hardware at 3600 dpi:
  about 300-360 s.

### One-time setup (StartPage)

| # | Action | Clicks |
|---|--------|--------|
| 1 | Browse... then pick the folder in FolderDialog | ~3 |
| 2 | Type the name | keys |
| 3 | Resolution combo to 7200 (default 3600) | 2 |
| 4 | Create batch | 1 |
| 5 | First load: "How to load the scanner", Copy command, power cycle, seat loosely, terminal paste + Enter, wait for the jog, pull out and reinsert, Enter, switch back to the window | 3 clicks + 3 terminal actions + window switch |

### Per tray, steady state (tray N >= 2, today's defaults: next-strip off)

| # | Action | Clicks | Wait / pain |
|---|--------|--------|-------------|
| 1 | Scan | 1 | Wait 5-6 min (3600) or ~14 min (7200), then enhance about 1 min. |
| 2 | (Click the tray in TrayQueue to see it) | 0-1 | Not auto-selected after tray 1. |
| 3 | Accept all | 1 | Batch-wide, so it also accepts slides in older trays the owner meant to re-check. |
| 4 | Export..., Export, (wait), Close | 3 | First time: +3 for the folder, +2 for JPEG. **Eject is disabled and front-panel presses are dropped while it runs.** |
| 5 | Free disk space..., (wait), Remove raw scans, (wait), Close | 3 | Holder blocked again. No verification beyond the `exported` mark. |
| 6 | Eject holder (or the front-panel button) | 1 | |
| 7 | Swap the holder by hand | physical | |
| 8 | Load: help dialog, copy, terminal, power cycle per the dialog text, reinsert, Enter | ~6 + power cycle | With `OPTICFILM_NEXT_STRIP=1`: 1 click ("Load next holder"). |
| 9 | Scan | 1 | Loop. |

**Total per tray: 10 clicks with next-strip on. About 15 actions plus a power cycle without
it.** There are 3 attended waits: after the scan, after the export, after freeing. The scanner
also sits idle during steps 4-5 because the holder cannot move. This breaks owner requirement
(1): the owner must look at the app after every tray.

---

## (b) The optimal basic workflow

Target: after one-time setup, **1 click per tray** ("Load & scan") plus the physical swap. With
owner decision G1 (below) and auto-eject at scan end, the holder is already out when the owner
comes back.

### Screen 1: Start (new batch card)

The fields are the same as today, plus three new controls. Folder, format and size are
remembered from the last batch through prefs.

- **Resolution**: segmented `[ 3600 | 7200 ]`, with a "More..." menu for 600/1200/2400. An
  estimate line under it, from constants in `messages.py`: "7200 dpi: about 14 min and 5.6 GB
  raw per tray; JPEG about 25 MB per slide (4x 3600), scans take about 2.4x longer."
- **JPEG size** (visible when 7200): segmented `[ Full 9600x6500 | 3600 size 4800x3250 ]`.
- **Album folder**: the export folder, stored in the batch (`BatchDefaults.export_dir`). It must
  be outside the batch folder, as `export_batch` already enforces.
- **Hands-free** switch (default on for new batches) and **Keep raw data** switch (default off).
  Their meaning is in the hands-free design.
- Primary pill button: **Create batch**. It opens Screen 2.

### Screen 2: Main, first load (scanner cold or not loaded)

When `scanner == NOT_READY` and the holder latch is not `LATCHED` (latch branch), the content
area shows a **First load card** instead of the help dialog:

1. "Slide the holder in loosely, then press Load." Buttons: [Load] [I loaded it in a terminal].
2. Running: "Moving the holder... do not touch it." (driver jog)
3. Prompt detected: "Pull the holder fully out, push it in to the stop, then press Done."
   Buttons: [Done] [Cancel]. Cancel shows a warning: "Cancelling needs a power cycle
   afterwards."
4. "Loading..." then "Loaded." If Hands-free is on, the scan starts by itself; otherwise the
   card turns into the Load & scan bar.

### Screen 2: Main, steady state

```
+------------------------------------------------------------------------------------------+
| Family trips  7200 dpi - JPEG full - Hands-free        (Dark|Light)  [Batch settings] [Close] |
+------------------------------------------------------------------------------------------+
| (o) Scanner ready - holder out         [  Load & scan  ]  [ Eject ]     tray 13 next      |
| [!] Tray 7: export failed, raw kept.  [Retry export] [Details] [Dismiss]                  |
+------------------+--------------------------------------------------------+-------------+
| TRAYS            | CONTACT SHEET (all trays, newest first)                | INSPECTOR   |
| 12 Scanning 61%  | Tray 12  [....] [....] [....] [....]   scanning 8:31     | 3 selected  |
| 11 Exporting 2/4 | Tray 11  [img ] [img ] [img ] [img ]   exporting 2/4     | (Accept)(Un)|
| 10 Done - freed  | Tray 10  [img ] [img*] [img ] [img ]   done - raw freed  | Rotate < >  |
|  1 Done - HELD   | ...                                                    | Mirror      |
| -------------    | Tray 1   [img ] [img ] [img ] [img ]   done - raw kept   | Bright ---o |
| Flagged (3)  >   |                                                        | Colour --o- |
| Failed  (1)  >   |                                                        | [Reset]     |
|                  |                                                        | [Re-export] |
+------------------+--------------------------------------------------------+-------------+
| 44 exported - 3 flagged - 1 failed - raw held: tray 1 [Check & free tray 1] - 212 GB free  |
+------------------------------------------------------------------------------------------+
```

- The top bar holds one primary action. Its label follows the state:
  - scanner READY and idle: **Scan**
  - holder out (`NOT_READY`, latch `RELEASED`) and next-strip allowed: **Load & scan**
  - cold: **First load...** (opens the card)
  - interrupted: **Restart scanner...** (see d)
- Keyboard: `Enter` triggers the primary action when the focus is not in a text field.
- **Eject** is always reachable and is enabled during exports (see PR3). With **Auto-eject at
  scan end** on (batch setting, part of decision G1), the holder comes out by itself after the
  last frame.
- The contact sheet replaces the "one tray + AdjustPanel" view, so the owner can select across
  trays. Clicking a thumbnail opens a large preview in the Inspector, with Before/After as a
  segmented toggle.
- Status footer: counts, raw held (the first-tray hold from the hands-free design) with its one
  button, and free disk space.

### Minimum-click path

| Phase | Owner actions |
|-------|---------------|
| Setup | Type the name, click Create (folder, format and size remembered): 1 click + typing |
| First load | Load, reinsert, Done: 2 clicks |
| Tray 1 | Automatic scan, enhance, accept, export. Raw kept (first-tray hold). Holder auto-ejects. |
| Each later tray | Swap the holder, **Load & scan**: **1 click** |
| Once, any time | Check tray 1 JPEGs, **Check & free tray 1**: 1 click |
| Without G1 (next-strip not default) | Each tray: front-panel Eject, swap, terminal load, Scan |

---

## (c) Bulk selection model

### State (Qt-free, testable): new `src/opticfilm_linux/app/selection.py`

```python
SlideKey = tuple[int, int]            # (tray number, position 1..4)

@dataclass(frozen=True)
class Selection:
    keys: frozenset[SlideKey] = frozenset()
    anchor: SlideKey | None = None    # start of a Shift range
    focus: SlideKey | None = None     # keyboard cursor, shown in the Inspector
```

Pure functions return a new `Selection`; nothing is mutated:
- `click(sel, key)`
- `toggle(sel, key)`
- `extend(sel, key, order)`
- `select_all(order)`
- `select_tray(sel, n, order, additive)`
- `select_where(order, pred)`, used for Flagged and Failed
- `clear()`
- `move_focus(sel, delta, order, extend)`
- `prune(sel, order)`, which drops keys that no longer exist, for example after a re-scan

`order` is the list of **selectable** keys in display order: trays by number, positions 1-4.
Slides of trays in SCANNING/SCANNED/ENHANCING are not selectable. Blank slides are selectable
but skipped by Accept.

The controller holds `_selection: Selection` under `_lock`. `AppState` gains `selection:
frozenset[SlideKey]`, `focus: SlideKey | None`, `selection_summary` (count, how many accepted,
mixed or common settings for the Inspector) and `undo_label: str | None`.

### Mouse and keyboard (in QML, passing `event.modifiers` to `bridge.selectSlide(tray, pos, mods)`)

| Input | Effect |
|-------|--------|
| Click | Select only this slide; anchor = focus = it |
| Ctrl-click | Toggle; anchor = it |
| Shift-click | Range from anchor to it, **across trays** (linear order); replaces the selection |
| Ctrl-Shift-click | Add the range to the existing selection |
| Click a tray header / TrayQueue row | Select that tray's 4 slides (Ctrl adds, Shift ranges trays) |
| Ctrl-A | Select all selectable slides of the batch |
| Esc / click empty sheet | Clear |
| Arrow keys | Move focus (Left/Right within a row and onward to the next tray, Up/Down by tray); Shift extends |
| Space | Toggle Accept on the selection (all accepted -> pending, otherwise -> accepted) |
| `[` `]` / `M` | Rotate left/right, mirror the selection |
| Ctrl-Z | Undo the last bulk action |
| "Flagged (n)" / "Failed (n)" in the sidebar | `select_where`, then the sheet scrolls to the first match |

### Actions that apply to a selection (Inspector and context menu)

| Action | Core call (new, one `store.update`, atomic) | Skips (named in one banner, like `_skipped_text`) |
|--------|---------------------------------------------|----------------------------------------------------|
| Accept / Unaccept | `review.set_status_many(store, keys, status)` | blank slides; trays not REVIEW/EXPORTED |
| Rotate +-90, Mirror | `review.apply_settings_many(store, {key: settings})` | `raw_removed` trays (cannot re-render) |
| Brightness / Colour / Grain / Sharpen (absolute value; the slider shows "mixed" until moved) | same | same |
| Reset to auto | same, with `SlideSettings()` | same |
| Re-export | `export.clear_exported(store, keys)` then a normal export of those keys | `raw_removed` trays, not accepted slides |
| Re-scan tray | the existing `rescan` | only when the selection lies in exactly one tray |

- All bulk calls return `skipped: tuple[SlideKey, ...]`. The controller turns them into
  `Banner("3 slides in tray 4 were left as they were: its raw scans were removed.")`.
- **Re-render queue.** A bulk edit must not starve the next tray's enhance. `runner.edit_many`
  only sets `preview_stale` (the existing field) and submits one `_drain_stale_job` if none is
  queued. That job re-renders **one** stale slide, then resubmits itself, so a tray-enhance job
  queued meanwhile runs between two slides.
- **Across trays.** Selection survives tray changes. `prune()` runs after each rebuild. A tray
  that starts a re-scan drops out of the selection.
- **Undo.** The controller keeps `_undo: tuple[UndoEntry, ...]`, at most 20 entries, cleared on
  batch close and not persisted. `UndoEntry(label, before: Mapping[SlideKey, tuple[SlideSettings,
  SlideStatus, bool]])` records settings, status and `owner_decided` before every bulk action.
  `undo()` restores them through the same `apply_settings_many`/`set_status_many` path, skipping
  freed trays.
  - Not undoable, so they ask for confirmation instead: Re-export (it writes new numbered files,
    and old files stay), free raw, re-scan.

---

## (d) States and failure handling

### Tray states shown (`TrayStage` plus the hands-free sub-state in `TrayRow.auto`)

`scanning -> scanned -> enhancing -> review -> [auto: accepting -> exporting -> verifying ->
freeing] -> done`. The terminal labels are: `done (raw freed)`, `done (raw kept: first tray)`,
`done (raw kept: setting)`, `export failed (raw kept)` and `failed`. The exact auto states
belong to the hands-free design.

| Situation | What the owner sees | What the app does | Raw data |
|-----------|---------------------|-------------------|----------|
| Scan fails (`TrayFailed`) | Tray row red, with the banner text from `failure_text`. With a power-cycle hint, the primary button becomes **Restart scanner...** | No auto-eject. Auto-pipeline skipped for that tray | Partial payload already removed by `_scan_job` |
| Power cycle needed / `INTERRUPTED` | **Restart scanner card**: "1 Switch off until the lights go out. 2 Switch on. [I did it]" -> `confirm_scanner_reset` -> First load card | Holder moves and scans stay refused until confirmed (unchanged) | Untouched |
| Cold scanner after power-on (`NOT_READY`, latch UNKNOWN) | First load card | `load` via the interactive wrapper (PR5) | Untouched |
| next-strip load fails | Banner `holder_failed_text("load")` + Restart scanner card | Latch -> UNKNOWN (latch branch) | Untouched |
| First-load Cancel or app closed at the prompt | "Cancelled. The scanner needs a power cycle before the next load." | Driver exits 130 on EOF; the interrupted mark stays (existing `interrupted` logic) | Untouched |
| Export fails for a slide | Tray `export failed (raw kept)`, with that slide listed under Failed and [Retry export] | Other slides of the tray still export (hands-free design) | That tray's raw kept |
| Verification fails (missing, short, undecodable, wrong size) | Same as above, reason in Details | Never calls `cleanup_raw` | Kept |
| Export writes nothing | "Nothing to write: all 12 accepted slides were already exported. Select slides and use Re-export to write them again." Separate counts for already exported, not accepted and raw freed | `ExportView` gains `already_exported`, `not_accepted`, `raw_freed` | n/a |
| Flagged slides | Sidebar "Flagged (n)": slides with non-empty `flags` (dust, too dark, ...) or BLANK. Built from `flags`, **not** status, because auto-accept turns NEEDS_LOOK into ACCEPTED | Exported like the rest | Per the hands-free design |
| Front-panel Eject pressed while the app cannot act | Banner "Eject ignored: a scan is running" (today it is silent) | `_act_on_eject_button` reports the refusal | n/a |
| App closed or crashed mid auto-pipeline | On reopen, the tray continues | `runner.resume()` + `AutoPipeline.resume()` re-queue trays in REVIEW/EXPORTED with `auto` not done and `raw_removed == False`. Verification runs again before any free | Kept until verified |
| Disk low | Footer turns warning below a threshold (constant, e.g. 2x one tray's raw at the batch dpi); Scan stays enabled but says why it is risky | n/a | n/a |

Raw-data safety rules (pinned by tests):
- free only after `verify_tray_export` passes
- free only inside the batch's own tray folders (`export._checked_sweep_folders`, unchanged)
- `keep_raw` and the first-tray hold always win
- bulk edits and re-export never touch freed trays
- undo never claims to restore raw data
- auto-free stays **off by default until fix/tone-black-point is merged**, because hazy JPEGs
  plus deleted raw cannot be undone

---

## (e) Sequenced PRs

Merge first (in flight, not part of this plan):
- **feat/holder-latch-state**. Touches controller, holder, messages, MainPage.qml, bridge, state,
  status, **and** batch/runner.py, batch/events.py, scanner/driver.py. Every PR below that touches
  these files rebases on it. It must merge first.
- **fix/tone-black-point**: pipeline only, no file overlap. It is a hard gate for turning
  auto-free on by default (PR4), not for writing PR4.

| PR | Branch | Content | Files (approx.) | Depends on | Size |
|----|--------|---------|-----------------|------------|------|
| PR1 | `feat/export-nothing-and-reexport` | `ExportView` counts + clear "nothing to write" text; `export.clear_exported(store, keys)`; `export_batch(..., keys: frozenset[SlideKey] \| None = None)` filter; ExportDialog text + a plain "Re-export all accepted" button (no selection yet) | batch/export.py, app/jobs.py, app/state.py, app/controller.py, app/qt/bridge.py, qml/ExportDialog.qml, tests | none (rebase on latch for state/controller/bridge: additive) | S, 6-8 |
| PR2 | `feat/batch-export-settings-7200` | `BatchDefaults.export_dir: str = ""`, `export_format: Format = "jpeg"`, `jpeg_size: Literal["full","3600"] = "full"`, `auto_export: bool = False`, `keep_raw: bool = False`. Manifest reads old files (dataclass defaults) and writes new keys only when non-default (like `slots`). `OutputSpec.scale` + INTER_AREA downscale for `jpeg_size="3600"` at 7200. StartPage segmented dpi + estimate line + JPEG size + album folder; ExportDialog prefilled from the batch. Default export format pref -> `jpeg` | batch/model.py, batch/manifest.py, output.py, batch/export.py, app/messages.py (estimates), app/prefs.py, app/qt/bridge.py, qml/StartPage.qml, qml/ExportDialog.qml, tests | PR1 | M, 10 |
| PR3 | `feat/holder-moves-during-export` | Drop the `WAIT_EXPORT` refusal from `_holder_refusal` for eject/load; `can_eject`/`can_load_next` no longer exclude `jobs_running` (`can_confirm_scanner` keeps it). Keep: scan vs holder move exclusive, close/open refused during a move, `_stop_jobs` on detach. `_act_on_eject_button` reports a dropped press. Jobs and enhance worker threads lower their own priority (`os.setpriority(PRIO_PROCESS, threading.get_native_id(), 10)` at thread start) | app/holder.py, app/state.py, app/controller.py, batch/runner.py (thread init), tests/test_app_holder_export_lock.py (inverted), tests/test_app_controller_exclusive.py | latch | S, 5-6. **Hardware gate before hands-free default:** one 7200 tray scanned while a 7200 export runs, `poll_timeouts=0` on all frames |
| PR4 | `feat/hands-free-pipeline` | Per the hands-free design: `app/auto.py` (Qt-free `AutoPipeline`, runs on `_jobs`); `review.accept_tray(store, n)` (sets ACCEPTED + `owner_decided=True` so re-enhance keeps it; flags stay); `export.verify_tray_export(...)`; free via `cleanup_raw`; first-tray hold + `confirm_free_held(n)`; resume; `TrayRow.auto`; Flagged/Failed counts. See the PR4 constraints below | app/auto.py (new), batch/model.py + batch/manifest.py (persisted `TrayRecord.auto`), batch/review.py, batch/export.py, app/controller.py, app/state.py, app/messages.py, app/qt/bridge.py, qml/TrayQueue.qml, qml/MainPage.qml (footer), tests (incl. old-manifest compatibility) | PR2, PR3; auto-free default needs tone fix | L, 12-14 |
| PR5 | `feat/in-app-first-load` | Wrapper mode in `scanner/driver.py`: `load_interactive(on_prompt) -> None` runs the **same** `of135i load` with `stdin=PIPE`, tails its log for the stable marker `"Press Enter when it is at the stop"` (from `loadflow.REINSERT_PROMPT`), calls `on_prompt`, waits (separate `HUMAN_WAIT_S`, e.g. 600 s) for `Done`, writes `"\n"`. Cancel closes stdin (exit 130, interrupted mark). Same runner as scans (`_config.python`). Calls the latch branch's `after_load`. Controller `first_load()`, `first_load_done()`, `first_load_cancel()`; `AppState.load_phase: Literal["idle","jogging","await_reinsert","loading","done","failed"]`. **Load & scan**: `load_and_scan()` = next-strip load, then wait (bounded 30 s) for a fresh READY poll, then `_reserved_scan`. Batch setting `auto_eject_after_scan` (default per G1). FirstLoadCard.qml replaces LoadHelpDialog steps (the dialog stays as "I loaded it in a terminal" help) | scanner/driver.py, app/holder.py, app/controller.py, app/state.py, app/messages.py, app/qt/bridge.py, qml/FirstLoadCard.qml (new), qml/MainPage.qml, qml/LoadHelpDialog.qml, tests (fake driver gains a prompt mode) | latch; PR3 for Load & scan during an export | M-L, 10. Hardware: owner present, 3 cold first loads + 5 swaps |
| P0 | `spike/theme` (throwaway, never merged) | See (h) | scratch only | none | XS |
| PR6 | `feat/theme-and-components` | `qml/Theme.qml` (singleton: palette for dark/light, radii, spacing), `PillButton.qml`, `SegmentedControl.qml`, `Card.qml`; `prefs` key `theme` (`"dark"` default, `"light"`), `bridge.theme` + `setTheme`; `app.py` applies the approach P0 proved; `Main.qml` toggle | qml/Theme.qml, PillButton.qml, SegmentedControl.qml, Card.qml (new), qml/Main.qml, qml/qmldir, app/qt/app.py, app/prefs.py, app/qt/bridge.py, tests | P0, latch (MainPage untouched here) | M, 9 |
| PR7 | `feat/selection-core` | `app/selection.py`; `review.set_status_many`, `apply_settings_many` (return skipped); `runner.edit_many` + `_drain_stale_job`; controller `select_slide(tray,pos,mods)`, `select_all`, `select_where`, `clear_selection`, bulk `accept_selection`, `adjust_selection`, `rotate_selection`, `mirror_selection`, `reset_selection`, `reexport_selection`, `undo`; `AppState.selection/focus/selection_summary/undo_label` | app/selection.py (new), batch/review.py, batch/runner.py, batch/export.py, app/controller.py (or a new mixin `app/bulk.py` to keep controller < 800 lines), app/state.py, tests | PR1 (clear_exported), PR4 (runner churn) | M-L, 7 |
| PR8 | `feat/contact-sheet` | MainPage rewrite: `SheetView.qml` (ListView of tray rows, newest first), `SlideThumb.qml`, `InspectorPanel.qml` (from AdjustPanel, bulk-aware, "mixed" sliders), keyboard map. Bridge: `sheet` model, **append-only** (a new tray inserts a row with `beginInsertRows`; never `beginResetModel` on growth), per-slide `previewToken` updated from `SlideUpdated` events, not `stat` on every tick. Sidebar Flagged/Failed filters. Pill/segmented components from PR6 | qml/MainPage.qml, SheetView.qml, SlideThumb.qml, InspectorPanel.qml (new), TrayQueue.qml, SlideCard.qml/AdjustPanel.qml (removed), app/qt/models.py, app/qt/bridge.py, app/controller.py (`_preview_tokens`), tests/qt | PR6, PR7 | L, 10-12 |
| PR9 | `feat/restyle-dialogs` | Apply Theme/components to StartPage, ExportDialog, FreeSpaceDialog, OrientationDialog, ConfirmDialog, ErrorBanner, LoadHelpDialog | qml/*.qml (7) | PR6, PR8 | S-M, 7 |

### Constraints Codex must follow (from the code)

- **PR4: no submit under the lock.** `_drain_events` calls `_apply_event` inside
  `with self._lock` (a non-reentrant `threading.Lock`). `_run(pool=self._jobs)` and
  `_after_pending_commands` take `self._lock` again, so submitting from `_apply_event` would
  deadlock.
  - `_apply_event` only records tray numbers in `_auto_due`.
  - `_drain_events` submits the auto jobs **after** releasing the lock.
  - Auto-eject at scan end (PR5) works the same way.
- **PR4: idempotent trigger.** `TrayStageChanged(REVIEW)` is also emitted after `set_tray_mode`,
  orientation changes and every `resume()`, so it cannot be the key. The pipeline keys off a
  persisted `TrayRecord.auto: Literal["", "pending", "exported", "verified", "freed", "held",
  "failed", "off"]`.
  - It is set to `"pending"` only when a tray first reaches REVIEW with `auto_export` on.
  - Only a tray in `"pending"` is processed.
  - A later owner edit never re-arms it. Re-export is explicit.
- **PR4: own plumbing for auto export.** It does not use the manual `_export` view or
  `_cancel_export`: `run_export` publishes into the slot `ExportDialog` shows, and `export()`
  clears the shared cancel event at submit. Reusing them would mean a manual Cancel kills the
  auto export, and a queued auto export wipes a manual Cancel. Auto export uses
  `_cancel_auto: threading.Event` (set only by close/shutdown) and publishes progress into
  `TrayRow.auto` / `_auto_progress`.
- **PR4: verification dimensions.** Rotation swaps width and height, and the aperture crop varies
  per slide, so the check is not against fixed sizes. `write_slide` (or `_export_tray`) records
  the `(w, h)` of the encoded array. The check decodes the JPEG and compares with that record.
- **PR3: which tests flip.** Invert only the cases that involve a running job in
  `test_app_holder_export_lock.py` and `test_app_controller_exclusive.py`.
  `test_batch_change_is_refused_while_a_holder_move_runs` and every scan-vs-move test stay as
  they are.
- **PR3: niceness is a best effort.** A per-thread nice does not reach OpenCV or BLAS pool
  threads created elsewhere.
  - Never nice the scan thread or the process: the driver child inherits the nice value of the
    thread that calls `Popen`.
  - The `poll_timeouts=0` hardware gate decides.
  - The fallback is to cap pipeline thread counts while a scan is pending (e.g. `cv2.setNumThreads`).
- **PR5: same locks as other holder moves.** The interactive load holds `_hw_lock`, the file
  lock and the `interrupted` mark for the whole human wait, exactly as `_holder_command` does.
- **PR5: when Load & scan is offered.** Only when the latch reads `RELEASED` from an eject in the
  current USB session (latch branch). A cold `--next-strip` exits 1 before any write, but it goes
  through `_holder_command` and would show the power-cycle text. A cold or UNKNOWN latch offers
  First load instead.

Merge order: latch -> (tone, in parallel) -> PR1 -> PR2 -> PR3 -> PR4 -> PR5 -> P0 -> PR6 -> PR7
-> PR8 -> PR9. PR1, PR2 and P0 can be written while latch is in review. PR4 delivers the
biggest win (no attended waits). PR5 delivers the 1-click swap.

Every PR follows the owner rules: its own branch, a PR, merge on green CI with
`merge_when_green.sh`, and never a direct push to main.

---

## (f) Test strategy

- **Qt-free first.** Coverage omits `app/qt/*` (pyproject), so all logic goes in `selection.py`,
  `auto.py`, `review.py`, `export.py`, `controller`, `state`. QML only binds.
- Unit tests:
  - `test_app_selection.py`: table-driven click, toggle, extend across trays, select_all
    skipping in-progress trays, prune after re-scan, focus moves.
  - `test_review_bulk.py`: atomic `set_status_many`/`apply_settings_many`; skipped freed trays;
    blank skipped on Accept.
  - `test_export_reexport.py`: `clear_exported`, the `keys` filter, the nothing-to-write counts,
    `jpeg_size="3600"` halves the encoded array's sides; verification compares with the recorded
    `(w, h)`, and a rotated 90/270 slide passes.
- Controller tests (fake scanner, `controller_helpers.open_new`, `wait_until`):
  - **Refusals that must stay** (keep or extend `test_app_controller_exclusive.py`): eject
    refused while a scan is queued or running; scan refused while the holder moves;
    close/open/new refused during a move; confirm-reset refused during jobs.
  - **Inverted by PR3**: eject during an export runs; a front-panel press during an export ejects;
    a press during a scan shows a banner.
  - Auto pipeline:
    - happy path frees raw
    - first tray held until `confirm_free_held`
    - `keep_raw`
    - verify fails (missing, truncated, undecodable, wrong size) -> raw kept + Failed
    - one slide's export fails -> the others written, that tray's raw kept
    - crash or resume at each auto sub-state never frees before verify
    - `cleanup_raw` never called outside the tray folders
  - Re-render drain: a tray enhance submitted during a 40-slide bulk edit finishes before the
    drain completes.
  - Undo restores settings, status and `owner_decided`; skips freed trays.
  - First load (fake driver prompt mode): prompt -> `await_reinsert`; Done -> newline -> loaded
    -> latch LATCHED; Cancel -> exit 130 -> INTERRUPTED; human-wait timeout -> treated as
    cancel; Load & scan waits for READY then scans exactly once.
- `tests/qt/test_qt_smoke.py` (offscreen, `-m qt`), by `objectName`:
  - `primaryAction` label per state
  - `firstLoadCard` phases
  - `themeToggle` persists through `MemoryPrefs` across a fresh `Bridge`
  - `sheetView` Ctrl-A / Shift-click across two trays selects 8
  - `inspectorAccept` applies to all
  - the export "nothing to write" text
  - the existing tests updated (`loadHelpButton`, `ejectButton`, ...)
- Pin these: the reinsert-prompt marker string against the driver fork version (a test reads
  `loadflow.REINSERT_PROMPT` when the fork is available, otherwise a recorded copy); manifest
  backward compatibility (an old manifest with no new keys loads; a new manifest with default
  values writes no new keys); export file naming unchanged.
- Gates per PR: full suite `PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider
  --cov=opticfilm_linux` (~9 min), `ruff check`, `ruff format --check`, `-m qt` smoke for QML
  PRs, then a separate review (Codex read-only).

---

## (g) Top risks and the 3 owner decisions

Risks:
1. **CPU contention breaks driver USB timing.** Export or enhance at 7200 during a scan could
   add poll timeouts. The niceness change in PR3 and the hardware gate (poll_timeouts=0) come
   before hands-free is the default. Memory is fine: enhance 4.6 GB + export 5.4 GB + stitch is
   well under 62 GB.
2. **next-strip is hardware-proven once (Test 89).** A failure costs a power cycle. The 1-click
   target depends on it.
3. **Irreversible raw deletion.** Guards: verify, first-tray hold, tone-fix gate, tests above.
4. **Re-export writes new numbered files and leaves the old ones** ("never overwritten"). The
   album can hold duplicates. The confirm text says so. Overwriting is a follow-up only if the
   owner asks.
5. **Theme override may not recolour `org.kde.desktop` controls** (they follow the system colour
   scheme). That is why P0 comes first.
6. **Contact sheet over thousands of slides.** Needs append-only model updates and
   event-driven preview tokens. Today's `set_rows` resets on growth and stats every 500 ms.
7. **Prompt detection is coupled to driver text.** Pinned by a test. On mismatch, fall back to
   the help dialog.
8. **Merge conflicts with the latch branch.** Nothing touching holder, controller or state
   starts its final rebase before latch is merged.

Decisions only the owner can make:
- **G1. The swap path.** Make `load --next-strip` (plus auto-eject at scan end) the default way
  to load the next holder, after a supervised series (e.g. 5 consecutive swaps at 7200)? Yes
  gives 1 click per tray. No gives a front-panel Eject plus a terminal load per tray.
- **G2. Raw and batch-folder lifetime.** Auto-free raw after verification, with the first-tray
  hold (as the hands-free design proposes)? And at session end, keep or remove the batch folder
  (manifest, previews, logs)? Both are irreversible.
- **G3. Default resolution and JPEG size.** 7200 + full-size JPEG (about 25 MB/slide, ~14
  min/tray), 7200 + 3600-size JPEG (better than native 3600 at the same file size), or 3600.

Smaller, lower-stakes: the accent colour, whether the front-panel button should also mean
"Load & scan" when the holder is out (follow-up, not planned), and the re-export overwrite
policy.

---

### Pre-mortem (three ways this fails)

1. **"Hands-free deleted good raw for bad JPEGs."** Auto-free shipped before the tone fix, or the
   verification checked fixed sizes and was loosened to pass.
   Prevented by: the tone-fix gate on the default, recorded-dimension verification, the
   first-tray hold, and tests that mutate each verify guard.
2. **"Scans started failing once exports overlapped."** CPU contention caused poll timeouts.
   Prevented by: the PR3 hardware gate before hands-free is the default, the thread-count cap
   fallback, and the per-frame `poll_timeouts` reported in the tray log.
3. **"The app froze on the first auto tray."** The lock re-entry deadlock above, or the auto
   export clashed with the manual dialog's cancel.
   Prevented by: the PR4 constraints, and a controller test running a manual export and an auto
   export together with a Cancel in between.

---

## (h) Throwaway prototype first

**P0 theme spike** (scratch branch `spike/theme`, about 1 file + 1 script, never merged). It
answers one question: can the app force dark/light under the style `configure_style()` selects?

- Build one QML window: Card, PillButton, SegmentedControl, Slider, Switch, ComboBox, an
  InlineMessage, in both themes.
- Try in order:
  - (a) `Kirigami.Theme.inherit: false` + explicit colours on the root;
  - (b) a dark `QPalette` set on `QGuiApplication` before the engine loads;
  - (c) when the theme is forced, the `org.kde.breeze` or `Fusion` style instead of
    `org.kde.desktop`.
- Screenshot both themes offscreen (`QQuickWindow.grabWindow()`) and show the owner. Pick the
  first approach that recolours native controls.
- Output: one paragraph in PR6's description, plus the two PNGs.

Optional second spike, only if PR8 looks slow: a sheet with 1,000 fake tray rows (generated
thumbnails) to check scroll and update cost before the bridge model is written.

No prototype is needed for the selection model (pure functions, test-first) or the first load
(fake driver prompt mode).
