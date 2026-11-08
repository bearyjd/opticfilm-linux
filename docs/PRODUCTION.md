# Production runbook: scanning many slides

For the owner scanning boxes of slides at 3600 dpi (the default for new batches; never lower) on one OpticFilm 135i. Short steps, expected
times, and what to do when something goes wrong. Facts come from the owner's and maintainer's
sessions of 2026-11-04/05, the README, the docs and the team learnings. Anything not checked is
marked **unverified**.

This is not the developer or host-ops guide. For that see [RUNBOOK.md](RUNBOOK.md) and
[ENV.md](ENV.md).

## Safety rules (read once)

- Stay at the scanner while it moves.
- If the motor grinds or stalls: switch the scanner **OFF at once**. Send nothing else.
  The read-only `of135i doctor` is only useful **before** a power cycle, and only when it is
  safe and the scanner still responds. After power-off it can only say "scanner not found".
  Do not switch the scanner on again until you have inspected the holder, then restart from
  the checklist ([hardware-checklist.md](hardware-checklist.md), [RUNBOOK.md](RUNBOOK.md)).
- Never force a holder that will not come out.
- Never use `pkill` or `pgrep -f` to stop a scan or the watcher.
- "Ejected" in a message does not prove the holder is free. Look at it.
- Keep the calibration stop gate on. Do not turn it off to "fix" a scan.
- The production driver (`.local-research/publish/driver-latest`, the owner's fork at commit `eaa6f88`)
  has the stop gate on by default: `scan --cal-wait-scanstop` defaults to `enforce`
  (`of135i/cli.py`). It is behind the upstream master by many commits, but upstream ported the gate
  from this fork, and upstream's own default is `off`. To check a driver, run
  `grep -n 'cal-wait-scanstop' -A2 <driver>/of135i/cli.py` and look for `default="enforce"`.

## 1. Before you start

Tick each item.

- [ ] Disk: the batch folder is **outside the repo**, on disk, not in RAM-backed `/tmp`. Allow
      about 3.5 GB of raw data per 7200 dpi tray (3600 dpi is about a quarter of that, an estimate
      from the pixel count, not measured) and about 9 GB free for a first tray at 7200 dpi
      (README, RUNBOOK.md, performance.md). Raw scans are **kept by default**, so plan disk for every
      tray of the box until you turn that off (see the settings table below).
- [ ] Nothing heavy is running. The app runs the driver with `--no-clean` by default, so there
      is little post-processing per frame. With `OPTICFILM_DRIVER_CLEAN=1` the driver's own IR
      clean adds about 2 min of single-core work per frame.
- [ ] The Eject watcher is not holding the scanner. The app stops
      `opticfilm-eject-watch.service` itself and restores it on exit (only if it was running).
      For terminal driver commands you must stop it yourself (section 6).
- [ ] Scanner has power and USB. After a power cycle, follow the cold start in step 2.1.
- [ ] Holder is clean and loaded with up to four slides.
- [ ] Check the batch settings. The defaults for a new batch are in the table below; the Start
      page shows them and you can change them per batch.

| Setting | Default for a new batch | Where to change it |
|---|---|---|
| dpi | **3600** (choices 600 to 7200; 7200 is too slow for a big job, owner decision 2026-11-07) | Start page. Existing batches keep the dpi stored in their `batch.json`. |
| Export JPEGs after each tray | **on** (needs an export folder; the Start page asks for one) | Start page, and the starting choice in **Workflow settings…** |
| JPEG size | **3600 dpi size**: the same as full size at 3600 dpi; it caps a 7200 dpi scan at 3600 dpi | Start page |
| Skip empty openings | **on** | **Workflow settings…**, or `OPTICFILM_SKIP_EMPTY` (the variable wins and locks the switch) |
| Keep raw scans after export | **on**, for now. This is the one switch for the raw scans: untick it on the Start page (or set `DEFAULT_KEEP_RAW` in `app/prefs.py` to change the default of new batches) and the app frees each tray's raw scans after its export is verified. The owner turns it off after two supervised trays. That freeing path has **never run on hardware**. | Start page, per batch |
| Automatic eject | **off**, never run on hardware | **Workflow settings…**, or `OPTICFILM_AUTO_EJECT` |
| Load & scan | **off**, never run on hardware | **Workflow settings…**, or `OPTICFILM_LOAD_AND_SCAN` |

A switch you already saved in **Workflow settings…** keeps its saved value; the new defaults
only apply to a profile that never saved it and to new batches. An environment variable still
overrides the saved value.

- [ ] Start the app with the production command:

```sh
distrobox-host-exec env PYTHONPATH=<repo>/src \
  OPTICFILM_DRIVER_DIR=<repo>/.local-research/publish/driver-latest \
  OPTICFILM_DRIVER_RUNNER=host \
  python3 -c "from opticfilm_linux.app.qt.app import main; raise SystemExit(main())"
```

Replace `<repo>` with the repo path. Create or open the batch **outside** the repo.

### 1.1 Batch description and date: the guess assist

On the New batch card, type whatever you know about the box in **What do you know about this
batch?** (box label, handwriting, memory), for example `Maine trip, summer 1974, Kodachrome,
Grandma's camera`, `Christmas 1983 - Dec` or `wedding, 6/1979`. A suggestion appears under it.
**Use suggestion** (or Ctrl+Enter in the box) fills the Description with your words and the
Date with the suggested year or year and month. It only fills fields you left empty; to
overwrite something you typed, use the separate **Replace my description** or **Replace my
date** button. Nothing is saved until you press Create batch, and you can edit every field.

What it guesses (plain rules, English only, no network, no AI):

- One year from 1900 to two years ago, as `1974` or `'74` (only when `'74` cannot also be 2074).
  A month from a month name next to the year (`Dec 1983`, `1983 - Dec`), `6/1979`, or `1979-06`.
  A day (`Dec 25, 1983`, `1983-12-25`) is dropped, with a note; the date stays year or year and
  month, and always an estimate.
- Seasons (`summer 1974`) and month ranges (`June-August 1974`) default to the year and offer
  the months as picks. Winter offers Dec, Jan and Feb of that year, with a note that the
  December may belong to the year before.
- Year ranges (`1974-1976`, `1974 to 1976`, `74-76`, `between 1974 and 1976`) default to the
  middle year and offer every year; `1974 or 1975` defaults to the earlier. Ranges wider than 15
  years give no default.
- A decade (`1970s`, `the 70s`, `seventies`) gives **no default**: pick one of its ten years,
  or nothing is applied to the date.
- Unrelated years (`1974 and 1983`) give no default and offer up to five of them. Implausible
  years (before 1900, in the last two years or the future) are ignored with a note.

The picks are small buttons under the suggestion (Tab to reach them, Enter or Space to pick);
the picked one is what Use suggestion applies. It does not guess from events such as Christmas
and a name that is also a month right next to the year (`Aunt Jan 1974`) is read as the month,
so check the suggestion. Your words stay
in the description unchanged apart from tidied spaces and a 500 character limit. Only the batch
description and estimated date are set; no other metadata is written.

## 2. Scan one tray

Times are for one 4-slide tray. The measured figures are at 7200 dpi; the 3600 dpi figures are estimates.

### 2.1 Cold start (after any power-on or power cycle)

The scanner is "cold" after a power cycle. The app says "Scanner is not loaded" until you load.

1. Switch the scanner **OFF**.
2. Wait until the lights go out **and** `07b3:1436` is gone from the USB bus
   (for example `lsusb | grep 07b3`).
3. Switch it **ON**.
4. Wait for the power light to stop blinking. A steady light means ready. (Steady blue is an
   owner observation, 2026-11-05; the repo does not name the colour. **Unverified.**)
5. Slide the holder into the slot loosely. Do not push it in hard yet.
6. In the app, press **First load…** (the primary button). The card says "Slide the holder in
   loosely, then press Load." Press **Load**.
7. When the card says "Pull the holder fully OUT, then push it back in to the stop. Press
   Done.", do that, then press **Done**. Wait while it says "Moving the holder…" and
   "Loading…". It ends with "Loaded. Press Scan when ready."
8. Check by hand that the holder is latched. The latch has no sensor.

**Cancel** needs a power cycle afterwards (the app asks "Cancel first load?"). Press it only
if you must. If the card says "First load did not finish", read the error details and power
cycle before trying again.

Status: this in-app cold first load passed twice in a prototype (2026-11-02). It has **not**
been run cold through the finished app. See section 7.

### 2.2 Scan

1. Load the holder (cold start above, or **Load next holder** between trays).
2. Press **Scan** when the scanner says "Scanner ready".
3. Wait. Do not run other heavy jobs.

| Setting | Time per 4-slide tray |
|---|---|
| 7200 dpi, driver run with `--no-clean` | about 14 min |
| 7200 dpi, driver's own IR clean | about 22 min |
| 3600 dpi (the new default) | about 6 min, an **estimate**: the 2026-10-31 run was a 3600 dpi tray scan at about 5 min; the app's `--slides` path at 3600 dpi was measured once on 2026-11-07 for 3 slides (openings 1 to 3, the app's exact command): scan pass 43.8 s per opening, position and park 5 to 18 s growing with the opening, calibration about 5.2 s on **every** opening (dual/IR scans recalibrate each time, so there is no once-per-tray saving), and 218 s wall for the command, about 73 s per slide. That is about 5 min for a full tray of 4 before the app's enhance and export, which a profile put at roughly 1 to 2 min per tray and overlaps the next scan. One unit, one tray |

- The app passes `--no-clean` to the driver by default (PR #95), so a 4-slide 7200 dpi tray
  should take about 14 min. Set `OPTICFILM_DRIVER_CLEAN=1` to restore the driver's own IR
  clean: about 22 min, and on 3600 dpi scans it caused blocky blotches. The 14 min figure
  after this change is expected from the earlier `--no-clean` runs and has not been
  re-measured through the app.
- The scan pass is 175 s per frame at 7200 dpi. That is hardware time. With the driver clean
  on, post-processing adds about 2 min of single-core work per frame.
- The 22 min and 2 min figures were measured on one unit, on the owner's 2026-11-04 trays
  (diag.json started/finished against file times).
- The earlier 827 s and 854 s trays (learnings) were driver runs with `--no-clean`, which is
  why they are close to the 14 min figure.

### 2.3 After the scan

With the defaults, the steady-state loop per tray is: eject, swap the holder, **Load next holder**,
**Scan**. Enhancement and export then run by themselves while the next tray scans (export is
automatic; raw scans are kept). You only open a tray for slides that were flagged. The first
hands-free tray with these defaults has not been run on the scanner.

- The holder **stays latched** after a scan. Automatic eject exists but is **off by default**: switch it on in
  **Workflow settings…** (saved between runs) or set `OPTICFILM_AUTO_EJECT=1` (also `true`, `yes`,
  `on`) before starting the app; the variable overrides the saved setting and locks the switch.
  It then runs its own eject once a good scan has fully ended. It skips with a banner ("Automatic eject skipped") and
  does not retry if the scan failed, the driver exited with an error, the batch is closing or the
  scanner state is unknown. It has **not** been run on the scanner, supervised or not
  (`docs/ROADMAP.md`: stays off until hardware rungs H5, H7 and H8 have passed twice).
- **Load & scan** (one press, or Enter, after an eject the app saw finish) is off by default and has not been run on the scanner; its supervised
  hardware check has not been done; the project keeps it off by default until that has passed
  twice, and nothing in the app prevents switching it on, so do not enable it before then. See the README
  section "Keyboard and workflow settings".
- Eject with the app's **Eject holder** button, or the scanner's front-panel Eject button.
  The panel button works while the app window is idle, not during a scan or holder move.
- Look at the holder. Confirm it is released before you pull it.

**Keys** (plain letters, same gates as the buttons; none is run by a held key, while you type in a
text field or in a read-only batch; a key the button would refuse shows the reason as a banner;
nobody has used them on the scanner yet):

| Key | Action |
|---|---|
| `S` | Scan |
| `E` | Eject holder |
| `L` | Load next holder |
| `X` | Open Export (it never exports by itself) |
| `F` | Ask to finish the box (confirm dialog, **Cancel** focused) |

They never skip the latch confirmation, the raw-free approval, the empty-frame confirmation or any
confirm dialog, and do not switch on automatic eject or Load & scan.

### 2.4 Headless tray: `ofscan batch run`

After the first load and latch are done by hand, one command scans, accepts, exports and frees:

```
ofscan batch run BATCH --out ALBUM --auto-export [--jpeg-size 3600] [--keep-raw]
ofscan batch free-held BATCH 1 --yes     # after you checked tray 1's JPEGs
```

- The flags are saved into the batch before the scan, and each changed default is printed.
  Without `--auto-export` the tray waits in review and export stays manual.
- It never loads, ejects or confirms the latch. With the real scanner it refuses `--count` above 1,
  a scanner that is not ready, a released latch, and a pending "scanner cut off" mark (exit 3).
- Tray 1 keeps its raw scans until `free-held`. Exit codes: 0 ok, 2 tray or export failed
  (raw kept), 3 refused before anything is written or scanned (`free-held`: 3 for any refusal,
  including a missing `--yes`), 130 interrupted.
- `--box` and `--section` only apply while the current section has no trays.

**Trays with fewer than four slides.** Add `--skip 4` (or `--skip 3,4`, `--skip 2`) to name the
bare holder openings. They are not scanned (`--skip 4` commands `--slides 1-3`), for every tray
of that command, and their slide numbers stay reserved: with `--skip 4`, tray 1 is 001-003 and
tray 2 starts at 005. Only single ASCII digits 1-4 are accepted (spaces around them are fine). A bad list (empty,
repeated, outside 1-4, a range like `3-4`, `04`, non-ASCII or huge numbers, or all four) is
refused (exit 3).

## 3. Review, export and swap trays

Enhancement runs in the background while the next tray scans (README).

1. Open the finished tray. Slides with no flag are accepted automatically. Dusty slides
   are accepted and exported too: the infrared dust mask covers 3 to 4 percent of real 3600 dpi
   slides, so dust is only listed ("N flagged", the Flagged filter) for a later spot check. Very
   dark, very bright, clipped and blank slides still wait for you.
2. Check each flagged slide. Use rotate and mirror if needed.
   - Rotating one slide in review used to take about 19 s. The fix merged as PR #99: rotate and mirror
     now turn the existing preview instead of re-enhancing the slide.
   - Preview lag does not change the output. **Export recomputes from the raw data.**
3. Press **Accept all** when satisfied.
4. **Export.** JPEG is the output format for the heirloom workflow. Files go to
   `box-<N>/<section>/<NNN>.jpg` when the heirloom layout is chosen (spec; the layout code is
   merged).
5. Swap trays:
   1. **Eject holder**; look and confirm it is released.
   2. Swap the holder and push it in to the stop.
   3. Press **Load next holder**. This runs `load --next-strip`. It took 5 to 6 s in a
      five-swap series on one unit, with no jog and no prompt.
   4. Press **Scan**.

Automatic export now runs while the next tray scans, which the driver's post-processing also needs a core for. One
7200 tray was clean while a 7200 export ran (learnings, 2026-11-01), so it is allowed, but
that is one observation.

Free disk space only with the app's **Free disk space** dialog. It says what it removes before
it does. Raw scans are removed only by that dialog, or by the batch setting below: with
**Export JPEGs after each tray** on and **Keep raw scans after export** turned off (it is on by default), the app says
"Raw data is kept until you check and free the first tray. Later trays are freed after
verification." Turn **Keep raw scans after export** on if you want to keep every raw scan.

### 3.1 Description and estimated date

Add details as they become apparent, at any time, also after a tray is exported. The strip
above the contact sheet edits what is selected; with nothing selected it edits the **Entire
batch**. A slide's own value beats its tray's, which beats the batch's; an edit never touches
rotation, brightness, accepted state or the rendered picture, and never re-renders anything. A
date is an estimate: a year, or a year and month.

The **Apply to** box picks what an edit reaches. Type the text (Enter applies it); the line under
the fields says first how many slides or trays it will change. Each edit is one **Undo** step.

| Apply to | Effect |
|----------|--------|
| Entire batch (default with nothing selected) | Sets the batch value: every slide that still inherits it changes, in every tray, scanned or not, exported or not. Slides and trays with their own value keep it. |
| Only trays scanned from now on | Trays that inherit the old value keep it (it is saved on the tray); only trays scanned later get the new one. |
| Selected slides / This tray | As before: the selected slides, or the one selected tray. |
| Tray range | Sets the tray's own value on each existing tray from the first to the last number. |
| Only slides with no value | Fills slides that have no description (or date) at any level. Never overwrites a value. A blank you set on purpose counts as a value. |
| Add to description | Adds `; text` to what each slide's description is now (selected slides, the selected tray, or every slide if nothing is selected), saved on the slide. Refused whole if any slide would pass 500 characters, and it skips slides whose description already is, or ends with `; text`, the text. Description only. |

Add to description saves the combined text on each slide, so a later Entire batch or tray edit
no longer reaches those slides (they now have their own value); use it for details that belong to
those photos, and Entire batch for what applies to all. Only slides with no value and Tray range
ignore the selection and look at the whole batch; Add to description follows the selection.

Clear and **Reset to inherited** work as before on the selection's own level (the selected
slides, the selected tray, or the batch), whatever **Apply to** says. Edits are refused
on a batch opened read-only.

Exported JPEGs carry the description and date only in the `ofscan` XMP fields. An edit that
makes exported JPEGs out of date updates them by itself (setting **Update exported JPEGs after
a description or date edit**, on by default;
`OPTICFILM_UPDATE_EXPORTED_METADATA_AUTOMATICALLY` overrides it).

- It rewrites only that metadata, without encoding the picture again, as a background job that
  never makes you wait.
- It starts only when no scan, eject, load, export or disk-space job is running. A scan, Eject,
  Load next holder, Load & scan or an export stops it after the file it is on, and an export
  also waits until it has stopped. It then continues by itself, doing only the files not yet
  rewritten.
- The banner reads "Updating metadata in exported JPEGs: 120 of 3000" and has a **Stop
  updating** button. After Stop it stays stopped ("press Update JPEGs to continue") until your
  next description or date edit.
- Several edits in a row cost at most one more pass. It never touches a read-only batch or raw
  scans.
- A file that no longer matches what was exported (changed by another program) is left alone
  and listed in the banner.
- Before each file is replaced, the app writes the new file's hash into the batch. If the app
  or computer dies at that moment, the next pass recognises the file as its own work and
  finishes the record. Temporary files such as `.001.jpg.0123456789ab.tmp` that a kill left in
  the output folder are removed at the start of a pass when they are older than a minute.
  Do not go back to an older version of the app while such an update was interrupted: the
  older version does not know this record and drops it the next time it saves.

With the setting off, or to retry, press **Update JPEGs**: that one runs like an export
(scanning waits for it) and also has the Stop button.

## 4. End of box or next section

- The toolbar has **Next section** and **Finish box**. Finish box advances the box number and
  starts a fresh holder (hands-free spec; **unverified** on real hardware).
- Do not mix boxes in one section. Check the box number and section name before the first
  scan of a new box.
- Before you leave a finished box: confirm every tray shows **Exported**, then check the output
  folder for the expected number of JPEGs.
- Do not delete raw scans until the box is exported and checked. Cleanup waits for your say-so.
- When you finish for the day: close the app (it restores the Eject watcher), then eject and
  take out the holder.

## Before a session

Run the pre-flight check with the app closed and the Eject watcher stopped:

```sh
python3 scripts/hardware/preflight.py --driver-dir <driver-dir> --dest <batch-folder> \
  [--min-free-gb 50] [--min-tmp-free-gb 8] [--min-scratch-free-gb 9] [--skip-scanner]
```

It prints one PASS/WARN/FAIL line per check and ends with GO (exit 0) or NO-GO (exit 1, any
FAIL). Checks:

- Free space plus a real small write and fsync on the batch folder, the temp dir (a full `/tmp`
  quota breaks every command) and the app's scratch dir (`$XDG_CACHE_HOME/opticfilm-linux/work`,
  default `~/.cache/opticfilm-linux/work`).
- The batch folder is a writable directory, not a symlink leaving its parent.
- The Eject watcher is inactive. If `systemctl --user` is unreachable (no user bus, for example
  inside distrobox) this is a WARN with the host command to run by hand; the script never runs it.
- The driver imports from `--driver-dir`.
- Scanner state from the driver's read-only `doctor`: idle-homed with the holder loaded is PASS.
  Holder not loaded, or a ready-looking report with a failing exit code, is WARN. Cold, not
  connected, failed, timed out or unparsable is FAIL (never guessed).
- No other process holds the scanner: the lock files `$XDG_STATE_HOME/opticfilm-linux/scanner.lock`
  (the app's, default `~/.local/state/...`) and `$OF135I_LOCK_FILE` (the driver's, default
  `/tmp/of135i-07b3-1436.lock`) are tested with a non-blocking flock without creating them; and
  no `of135i`, `ofscan` or `opticfilm` (app) process is running.
- No held `batch.lock` in the batch folder.

It runs only `systemctl --user is-active opticfilm-eject-watch.service` and, with the same
Python that runs the script (`sys.executable`), `-c "import of135i"` and `-m of135i doctor`,
never scan, load or eject. With `--skip-scanner` the `doctor` call is omitted and a GO ends with
the line `GO (scanner state NOT verified)`.


## 5. Troubleshooting

| Symptom | Likely cause | Do | Do NOT |
|---|---|---|---|
| Power light blinking | Scanner is starting up after power-on | Wait. A steady light means ready. | Send load or scan commands while it blinks. |
| Power light blinks for a very long time | **Unverified** | Switch OFF until the lights go out, then ON. Check the USB cable. | Keep pressing buttons. |
| Holder locked after a scan | By design: the holder stays latched until you eject (automatic eject is off by default, `OPTICFILM_AUTO_EJECT`, section 2.3) | Press **Eject holder** in the app, or the front-panel Eject button. Look at the holder. | Pull the holder out by force. |
| Load fails or stalls | Holder not at the stop, or a bad load. The driver then refuses all commands until a power cycle. | If the motor grinds or stalls: OFF at once (see Motor grinds). Otherwise close the app, run `of135i doctor` (section 6) while the scanner still responds, then power-cycle (section 2.1), reseat the holder, load once. | Retry load in a loop. Force the holder. |
| "Scanner is not loaded" after a power cycle | The scanner is cold | Do the cold start (2.1) and the in-app load step. | Press Scan before loading. |
| Scan interrupted (window closed, killed, Ctrl-C) | Scanner state unknown. The app records a marker and refuses scan, eject and load. | Power-cycle (2.1), load the holder, press **Confirm scanner reset…** (the primary button reads **Restart scanner…** in this state), rescan the tray. The tray is not enhanced or exported. | Delete the marker file by hand. Confirm before you have power-cycled and loaded. |
| Eject watcher conflict: the app refuses to open the scanner and tells you to stop the unit | The watcher unit, or a leftover `of135i watch`, is still alive. Only one program may use the scanner. | `systemctl --user stop opticfilm-eject-watch.service`, then open the batch again. | Kill `of135i watch` with `kill -9`. Use `pkill -f`. |
| The driver says "another program is using the scanner; wait for it to finish or close it, then try again" | Another `ofscan` or app run holds the scanner lock | Wait for it to finish or close it, then retry. | Start a second program on the scanner. |
| Slow step after the scan finishes | The driver's post-processing is about 2 min single-core per frame (one unit). The dust clean adds time (about 22 min vs 14 min per tray). | Wait. Stop other heavy jobs. | Close the app or power off during this step. |
| Slide rotates slowly in review | Preview was recomputed in about 19 s per slide (fixed in PR #99: rotate turns the existing preview) | Wait for the preview. Set orientation per batch where you can. Output is not affected: export recomputes from raw. | Rotate the same slide repeatedly while it works. |
| Motor grinds or stalls | Mechanical problem or a bad state | Power **OFF** at once. Send nothing else. Inspect the holder before you power on. `doctor` only helps before power-off, if the scanner still responds and it is safe (section 6). | Send more commands. Force the holder. Power on before inspecting the holder. |
| Calibration error "dark_b is residual" | The scanner is left in an unsafe state (reg 0x01 = 0x02). This is what the stop gate prevents. | Power-cycle, load, rescan. Keep the stop gate on. | Turn the gate off. Keep scanning without a power cycle. |
| App will not start | Missing PySide6 or Kirigami, wrong driver dir, or a second window holds the batch | Check the command in section 1 (`PYTHONPATH`, `OPTICFILM_DRIVER_DIR`). Install `python3-pyside6 kf6-kirigami qt6-qtdeclarative`. A second window on the same batch opens read-only. | Install the PySide6 wheel from PyPI (it cannot load system Kirigami). |

Messages the app shows (from `app/messages.py`):

- "Scanner is not loaded. Between trays, press Load next holder. After power-on, open
  'How to load the scanner' for the first terminal load." This text is older than the in-app
  load step. Follow section 2.1.
- "The scanner stopped this scan. Power-cycle it (off until the lights go out, then on), eject,
  load the holder again and rescan this tray."
- "Could not eject the holder." or "Could not load the next holder." Switch off until the lights
  go out, switch on, then load.
- "Holder: unknown after power-on or a fault; check by hand before scanning."

## 6. Quick reference

### Commands

```sh
# Status of the Eject watcher
systemctl --user status opticfilm-eject-watch.service

# Before any driver command from a terminal: CLOSE THE APP FIRST
# (the app polls doctor itself; do not run doctor while it is open)
systemctl --user stop opticfilm-eject-watch.service
# ... run the driver command ...
# Start the unit again only after the app is closed. The app stops it itself
# and refuses to open the scanner while a watcher is alive.
systemctl --user start opticfilm-eject-watch.service

# Read-only scanner state (app closed, watcher stopped)
cd <driver-dir> && PYTHONPATH=. python3 -m of135i doctor

# Is the scanner on the USB bus?
lsusb | grep 07b3
```

`<driver-dir>` is `<repo>/.local-research/publish/driver-latest`. Run `doctor` on the host
(`distrobox-host-exec` if you are in the container). The exact `doctor` invocation for the host
runner is **unverified**; the form above matches the load command the app prints.

### Files and folders

| What | Where |
|---|---|
| Driver logs | `~/.local/state/opticfilm-linux/logs` (or `$XDG_STATE_HOME/opticfilm-linux/logs`) |
| Interrupted marker | `~/.local/state/opticfilm-linux/scanner-interrupted.json` |
| Holder latch record | `~/.local/state/opticfilm-linux/holder-latch.json` |
| Scan scratch | `~/.cache/opticfilm-linux/work` |
| Batch folder | You choose it, **outside the repo**. Holds `batch.json` (the manifest) and the trays' raw scans. |
| Exported JPEGs | The export folder you pick. Heirloom layout: `box-<N>/<section>/<NNN>.jpg` |

- **`scanner-interrupted.json`**: a scan or holder move may not have finished. While it exists
  the app and `ofscan` refuse scan, eject and load. Clear it only with the app's **Confirm scanner
  reset…** button (also reached from the primary button **Restart scanner…**) after a power
  cycle and a fresh load. The terminal form `ofscan batch confirm-scanner --yes` needs the
  package installed; the production launch runs from `PYTHONPATH`, so `ofscan` is probably not
  on your PATH (**unverified**). A
  mark that cannot be read counts as present.
- **`holder-latch.json`**: what the app believes about the holder (latched, released,
  unknown). A new session starts as unknown and never restores the file, because the holder may
  change while the app is closed. After a known eject, Scan is refused until you confirm the
  holder is seated and latched.
- The Eject watcher unit is stopped while the app has a scanning batch open. "Inactive" is
  normal then. If the app was killed outright, start the unit by hand once the scanner is safe.

## 7. What is not verified yet

- **Cold first load through the finished app.** A prototype passed twice on 2026-11-02. The
  finished in-app flow still needs a run after a real power cycle (STATE.md: needs the owner).
- **First hands-free tray with auto export.** Auto export is now on by default for new batches. A
  supervised tray with auto export on and keep raw on has not been run, and neither has the 3600 dpi
  `--slides` path timing.
- **Freeing raw scans after a verified export** (keep raw off) has never run on hardware. Keep raw
  is on by default until the owner has watched two trays.
- **Flatpak.** The manifest is a draft and not buildable. The sandbox integration merged as
  PR #113: sandbox detection, a host-terminal load command, watcher stop/restore via
  `flatpak-spawn --host`, and a refusal to open the scanner when the host cannot be asked. Nothing
  has run inside a real sandbox. The `--talk-name=org.freedesktop.Flatpak` permission is
  documented in `packaging/flatpak/README.md`.
- **Finish box / Next section** on real hardware, and the heirloom output layout on a real box.
- **Blinking power light that never stops** (section 5): cause unknown.
- **Light colour.** "Steady blue" is an owner observation, not in the repo.
- **Exact `doctor` invocation on the host runner** (section 6).
- **Speed figures** are from one unit: 14 min (`--no-clean`), 22 min (driver clean), 6 min
  (3600 dpi, the new default; not measured through the app). Treat them as estimates.
- **Reliability.** Passing series so far are one unit, one operator. They are not a
  reliability figure.
- **Bulk description/date scopes and the automatic JPEG update** (section 3.1) are tested only
  with offscreen Qt and the fake scanner; nobody has used the strip by hand yet.
- **Auto-eject** (PR #108) is merged, off by default, gated by `OPTICFILM_AUTO_EJECT` or the
  saved switch, and never run on hardware, so every tray still needs an eject action unless you opt in.
