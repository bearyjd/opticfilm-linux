# Responsive layout design for the OpticFilm Linux Qt/QML GUI

Status: approved design (Opus architect audit, 2026-11-03). Owner request: the GUI must reflow by screen size and must not need manual resizing to be useful.
Paths are relative to `src/opticfilm_linux/app/` unless they start with `tests/`. gu = `Kirigami.Units.gridUnit`; px values are at gu=18.
Evidence (333 offscreen captures + automatic overflow detection): 800x600 to 3840x2160, dpr 1/1.5/2, dark+light. Harness lives in the session scratchpad (`live-run/matrix/run_matrix.py`, `tree.js`).

## 0. Summary

The GUI is a 1220x780 desktop layout with no screen awareness. It breaks below about 1280 px wide or 720 px tall. Causes:
- Fixed window and pane sizes: `qml/Main.qml:12-13`; `qml/MainPage.qml:323-325,364-366`.
- A banner column with no height limit: `qml/MainPage.qml:190-315`.
- A toolbar that never overflows: `qml/MainPage.qml:119-188`.

Design: size the window from the screen in Python before showing; two size axes (width class, height class) with breakpoints in gridUnits; Kirigami `ActionToolBar` for secondary toolbar actions; tray list and Inspector become drawers on narrow windows (single instances, reparented); one prioritized status area with a height cap and an "n more" control; dialogs sized `min(content, 90% of window)` with footer actions always visible; all sizes derived from `Kirigami.Units.gridUnit`. Ships as 5 PRs each guarded by a probe that measures the real item tree.

## 1. Root causes (with file:line)

A. Fixed startup size, no minimum: `qml/Main.qml:12-14` (`width: 1220; height: 780; visible: true`); `qt/app.py:74-86` `create_engine` never sizes/clamps/restores/maximizes; min size 0/0. On small screens the initial window exceeds the screen.

B. Pixel literals instead of font-relative units: `qml/Theme.qml:19-26` (radius 14, spacing 8/16/24, fonts 12/14/20, controlHeight 44); every `font.pixelSize: Theme.font*` ignores system font size (e.g. `qml/ErrorBanner.qml:37`, `qml/StartPage.qml:42`, `qml/SheetView.qml:121`). Fixed minimums: `qml/PillButton.qml:9` (`Math.max(100,..)`), `:11` (padding 24), `:28-29`; `qml/SegmentedControl.qml:11` (96/segment); `qml/SlideThumb.qml:10` (170); `qml/SheetView.qml:107`; `qml/InspectorPanel.qml:112` (150), `:174` (100), `:200-201`. The detector found zero controls < 24 px and zero text < 11 px: the defect is that sizes ignore the font (at 4K dpr1 the UI is 12-14 px text and no font setting can fix it).

C. Status stack with no height limit starves the review area: `qml/MainPage.qml:190-315` shows, above the review row (`:317`): auto-status/held row (`:194-222`), FirstLoadCard (`:224`), latch label (`:228`, unconditional), latch InlineMessage (`:236`), confirm-latched button (`:244`), scanner row (`:252-309`), ErrorBanner (`:313`). Below: key-hint label (`:371-378`) and theme footer (`qml/Main.qml:29-43`, ~60 px). Worst stack at 1024x600 leaves ~50 px of review area.

D. Toolbar never overflows: `qml/MainPage.qml:119-188` RowLayout with 10 controls; "Close batch" (`:183`) is outside the window at 800 px.

E. Fixed-width side panes: tray sidebar min 180/pref 230 (`qml/MainPage.qml:323-325`), Inspector min 270/max 360 (`:364-366`): 451 px always taken; at 800 px the Inspector overflows (40+ controls outside the window in `qml/InspectorPanel.qml:85-252`); sidebar's two 44 px buttons (`:339-352`) leave ~150 px for the tray list at 600 px tall.

F. Dialog sizing: content widths hard-coded (`implicitWidth: gridUnit*N`): `qml/EmptyFramesDialog.qml:93` (44), `qml/LoadHelpDialog.qml:32` (32), `qml/FreeSpaceDialog.qml:32`, `qml/OrientationDialog.qml:34`, `qml/ExportDialog.qml:73` (28 each). Action rows sit inside the scrolling content, not in the footer: `qml/EmptyFramesDialog.qml:146-165` (the empty-frame safety confirmation), `qml/FreeSpaceDialog.qml:53-68`, `qml/ExportDialog.qml:124,150,158`. At 800x600 the empty-frame confirmation starts below the viewport. Repeated `y` binding loops at Kirigami `dialogs/Dialog.qml:303` for LoadHelp and EmptyFrames (hypothesis: fixed content width vs wrapped labels).

G. Banners stack instead of being prioritized (same lines as C): six independent `visible:` bindings, no ordering/collapse.

H. Text elision: `qml/SlideThumb.qml:59-65` cuts the status caption at 800 px (consequence of E); `qml/PillButton.qml:39` elides labels when squeezed. The `ElideMiddle` path labels (`qml/StartPage.qml:61,137,312`) are intentional: whitelist them.

I. Dialog hosts that a drawer design would break: `reexportDialog` is declared inside `qml/InspectorPanel.qml:18-24`; the raw-recovery `recoverDialog` is declared inside the TrayQueue delegate (`qml/TrayQueue.qml:56-61`). If their hosts move into drawers or get recycled, these confirmations close or die.

## 2. Responsive policy

### Window (Python, before the window is shown)
- New pure module `qt/window_geometry.py`: `initial_geometry(avail_w, avail_h, gu, saved) -> (w, h, maximized, min_w, min_h)`. No Qt dependency: unit-testable in the default suite.
- Use the target screen (`window.screen()` or `QGuiApplication.primaryScreen()`).`availableGeometry()`, not `Screen.desktopAvailableWidth` (spans all monitors).
- Minimum: `min_w = min(40*gu, avail_w)`, `min_h = min(30*gu, avail_h)` (~720x540 at gu=18). The compact layout must work at it.
- First run: `w = clamp(0.85*avail_w, min_w, 100*gu)`, `h = clamp(0.85*avail_h, min_h, 60*gu)`. If `avail_w < 70*gu` or `avail_h < 45*gu` (netbook, Steam Deck 1280x800) start maximized.
- Restore: save `window/width`, `window/height`, `window/maximized` via the existing `qt/settings.py` QSettingsPrefs on `closing`; clamp saved size to `[min, avail]`; never save/restore position (Wayland ignores it; wrong after monitor change).
- Wiring: `qml/Main.qml`: `visible: false`, default `width: 68*gu`, `height: 43*gu`. `create_engine(bridge, provider, size=None)`: when `size` is set (tests, or env `OPTICFILM_WINDOW_SIZE=WxH`) resize to it; otherwise apply `initial_geometry`; then `show()` or `showMaximized()`. Test fixtures pass `size=(1220, 780)` so existing tests keep their baseline.

### Size classes (decided in MainPage)
| Axis | Class | Breakpoint | What changes |
|---|---|---|---|
| width | compact | page width < 50 gu (< 900 px) | Tray pane in a left `Kirigami.OverlayDrawer`; Inspector in a right `OverlayDrawer`; toolbar shows "Trays" and "Inspector (n)" toggles (always visible); sheet uses the full width |
| width | medium | 50-68 gu (900-1224 px) | Tray pane docked at `clamp(11gu, 0.16W, 15gu)`; Inspector is a right drawer with its toggle |
| width | expanded | >= 68 gu (>= 1224 px) | Three panes; tray pane `clamp(11gu, 0.16W, 15gu)`; Inspector `clamp(16gu, 0.25W, 22gu)` |
| height | short | page height < 45 gu (< 810 px) | Theme footer hidden (theme becomes an overflow action); key-hint label hidden (overflow item "Keyboard shortcuts..." opens a popup with the same text); status area shows 1 item plus "n more"; FirstLoadCard lays out as a `Flow`; sidebar Select flagged/failed become one row of two compact buttons |
| height | regular | >= 45 gu | Footer and key hint visible; status area shows up to 3 items |

### Panes and drawers
- `TrayPane` (TrayQueue + select buttons) and `InspectorPanel` are SINGLE instances; their `parent` is bound to the docked slot or the drawer's `contentItem` depending on size class. Keeps objectNames unique, preserves ListView state, avoids double model bindings. Set `Layout.*` on the slot wrappers, not on the panel.
- Drawers are modal overlays with `onClosed: page.focusReview()`. Esc closes the drawer and the keyboard map is live again. Selection keys (A/R/N/Space/[ ]/M/Ctrl+Z) act on the selection without the Inspector.
- Tapping a tray in the compact tray drawer selects the tray, closes the drawer, then calls `scrollToFocus` (same path as `qml/MainPage.qml:337`).

### Toolbar
- `PillButton` Scan (`primaryActionButton`) and `nextSectionButton`/`finishBoxButton` stay real items on the left, always visible.
- Everything else moves into a `Kirigami.ActionToolBar` aligned right with `Kirigami.Action`s: How to load, Accept all, Batch orientation..., Export..., Free disk space..., Close batch. Theme and Keyboard-shortcuts actions use `displayHint: Kirigami.DisplayHint.AlwaysHide`. Each Action keeps the old objectName.
- Safety controls are NEVER in the overflow menu: release, empty-frame confirmation, latch, recovery, eject, load next, confirm scanner reset and first-load buttons all live in the status area.

### Status area (new `qml/StatusArea.qml`; replaces `qml/MainPage.qml:194-315`)
- The scanner row is always shown and not collapsible: `scannerStatusText`, `loadHelpLink`, `confirmScannerButton`, `ejectButton`, `loadNextButton`; `GridLayout` whose `columns` switch from 5 to 1 + `Flow` in compact.
- Prioritized entries, top first: (1) FirstLoadCard while active (jogging/await_reinsert/loading; the "Done" prompt is time-critical); (2) ErrorBanner; (3) held release: auto-status text + `freeHeldRaw`; (4) empty frames: text + `confirmEmptyFrames`; (5) holder latch: ONE entry merging `holderLatchText` and the InlineMessage, plus `confirmHolderLatched` (keep both objectNames; label `visible: text !== ""`); (6) FirstLoadCard when idle/failed/done; (7) plain auto-status.
- The top N entries render in full with their buttons (N = 1 short, 3 regular). Below them a flat button `statusMore` reads "N more: Holder latch, Raw cleanup held" and expands the rest inline, so every safety action is one click away.
- The area sits in a `ScrollView` with `Layout.maximumHeight = pageH - reviewMin`, `reviewMin = max(17*gu, 0.5*pageH)` (~306 px at 800x600). Expanded banner Details scroll inside this cap.
- Texts, bridge calls and objectNames of every entry stay unchanged.

### Sheet (`qml/SheetView.qml`; still an append-only ListView over `bridge.sheet` with `reuseItems`)
- Cell geometry computed once: `columns = (width - 2*spacing) >= 4*minCell + 3*gap ? 4 : 2`, `minCell = 8*gu`; `cellWidth = min(maxCell, (avail - (columns-1)*gap)/columns)`, `maxCell = 22*gu`; `cellHeight = cellWidth/1.5 + captionH` (3:2 film frames).
- The delegate's `RowLayout` (`:98-115`) becomes a `GridLayout { columns: sheet.columns }` with fixed `Layout.preferredWidth/Height`, left-aligned.
- Up/Down still call `moveSelectionFocus(+-4)` (`qml/MainPage.qml:42-43`): "by tray" per the UX spec, so a 2x2 tray stays correct. Keyboard map unchanged.

### Dialogs (all)
- Kirigami dialogs: `preferredWidth: Math.min(N*gu, parent ? parent.width*0.9 : N*gu)` (keep current N); `maximumHeight: parent ? parent.height*0.9 : Infinity`; remove `implicitWidth` from content.
- Actions move to a `footer:` row of PillButtons (custom footer items keep their objectNames), so the footer is always visible and only the body scrolls.
- Keep exactly: the `loaded === previews.length` gate (`qml/EmptyFramesDialog.qml:153`); `closePolicy` (`qml/FreeSpaceDialog.qml:27`); focus-on-accept (`qml/ConfirmDialog.qml:42-50`).
- EmptyFrames grid: `columns: availableWidth >= 2*14*gu ? 2 : 1`; preview height `min(8*gu, ...)`. `ConfirmDialog` (`Kirigami.PromptDialog`): `preferredWidth: min(30*gu, 0.9*parent.width)`.
- Hoist `reexportDialog` and `recoverDialog` to `MainPage` scope (the latter as `recoverDialog.tray = n; open()`).

## 3. Sizing system (`qml/Theme.qml`; add `import org.kde.kirigami as Kirigami`)
All tokens derive from `gu = Kirigami.Units.gridUnit`.
- `spacingSmall`/`spacing`/`spacingLarge` = `round(gu*0.45)`/`round(gu*0.9)`/`round(gu*1.33)` (8/16/24)
- `cardRadius` = `round(gu*0.8)` (14)
- `fontSmall`/`fontBody`/`fontHeading` = `round(gu*0.67)`/`round(gu*0.78)`/`round(gu*1.11)` (12/14/20)
- `controlHeight` = `max(44, round(gu*2.45))` (44, minimum touch target)
- `iconSmall`/`iconMedium` = `Kirigami.Units.iconSizes.small` / `.smallMedium`
- `minCell` 8, `maxCell` 22, `paneMin` 11, `paneMax` 15, `inspectorMin` 16, `inspectorMax` 22 gu; `reviewMin` = `max(17gu, 0.5H)`.
Replacements: `qml/PillButton.qml:9` 100 -> 5.5*gu; `qml/SegmentedControl.qml:11` 96 -> 5*gu; `qml/SlideThumb.qml:10` remove fixed height (sheet sets it); `qml/InspectorPanel.qml:112` `max(150,..)` -> `max(8*gu,..)`; `:174` -> 5.5*gu; `qml/MainPage.qml:323-325,364-366` use the tokens. At the default font the values are identical to today (PR 2 has no visible change at defaults; it only fixes scaling).

## 4. PR order (each shippable and green)
1. `fix/window-fits-screen`: new `qt/window_geometry.py`; `qt/app.py` (`create_engine(size=)`, restore/save), `qt/settings.py` (3 keys), `qml/Main.qml` (`visible: false`, gu defaults, save on closing); hoist `reexportDialog` (`qml/InspectorPanel.qml:18-28`) and `recoverDialog` (`qml/TrayQueue.qml:56-61`) to `qml/MainPage.qml`; tests `tests/test_window_geometry.py` + the probe infrastructure (section 5) with today's failures marked strict `xfail`.
2. `refactor/theme-units`: `qml/Theme.qml` tokens; literals replaced in PillButton, SegmentedControl, SlideThumb, SheetView, InspectorPanel, ErrorBanner, LoadHelpDialog, MainPage; update `tests/qt/test_dialog_theme.py:79` and `tests/qt/test_contact_sheet_ui.py:198-199` to compare against Theme tokens read from QML instead of literals; add the font-scale probe case.
3. `fix/dialogs-fit-window`: Export, EmptyFrames, FreeSpace, Orientation, LoadHelp, ConfirmDialog width/height policy, footer actions, no binding loops; flips the dialog `xfail`s.
4. `feat/status-area`: new `qml/StatusArea.qml`; `qml/MainPage.qml:190-315,371-378` replaced; `qml/FirstLoadCard.qml` gets a `compact` property (Flow); `qml/Main.qml:29-43` footer `visible: !page.short`; flips the worst-stack review-height `xfail`.
5. `feat/adaptive-panes`: `qml/MainPage.qml` header = Scan + section buttons + `ActionToolBar`; size classes; drawers with single-instance reparenting; new `qml/TrayPane.qml` (from `qml/MainPage.qml:322-353`); `qml/SheetView.qml` columns/cellWidth/GridLayout delegate; `qml/InspectorPanel.qml` width-agnostic; update `tests/qt/test_qt_smoke.py:289,331` (`loadHelpButton` becomes an Action: assert visible/enabled and trigger). Flips all remaining `xfail`s.

Invariants for every PR: all objectNames preserved (tests use `ejectButton`, `loadNextButton`, `loadHelpLink`, `scannerStatusText`, `confirmScannerButton`, `freeHeldRaw`, `confirmEmptyFrames`, `firstLoad*`, `selectFlagged`, `selectFailed`, `inspectorPanel`, `reexportSelection*`, `themeToggle`, `errorBanner`, `primaryActionButton`, `nextSectionButton`, `finishBoxButton`); all bridge calls and safety texts byte-identical; no change to `bridge.sheet` or `models.py`.

## 5. Automatic regression tests
Probe: move `detect()`/`tree()` from the matrix harness into `tests/qt/layout_probe.py`; move the 4-tray fake-scanner fixture and the injected worst-stack state into `tests/qt/layout_fixture.py`. With a dialog open, check only the popup subtree plus the dialog bounds.

Tiers: `tests/qt/test_responsive.py` (marker `qt`, runs in the Qt CI job): 800x600, 1024x600, 1280x800, 1920x1080 at dpr 1 dark, plus 800x600 light, plus the minimum size; contact sheet and worst stack at all sizes; every dialog (Export, FreeSpace, held-release Confirm, LoadHelp, EmptyFrames, Orientation, firstLoadQuit) at 800x600; ~20 captures, target < 90 s. `tests/qt/test_responsive_matrix.py` with new marker `qt_matrix` (add `and not qt_matrix` to `addopts` in `pyproject.toml`): full 9 sizes x dpr 1/1.5/2 x themes, plus app font x1.5 at 1024x600 and 1366x768; dpr and font need a fresh process (subprocess per dpr with `QT_SCALE_FACTOR`).

Pass criteria (by objectName, not counts): (1) no `control_outside_window` / `outside_clipping_parent` hit outside a scrollable ancestor (whitelist the Kirigami-internal `Main.qml:1` node by type and the ElideMiddle path labels); (2) reachability: each required objectName is fully inside the window directly or after scrolling its scroll ancestor or one click (drawer toggle or `statusMore`): `primaryActionButton`, `ejectButton`, `loadNextButton`, `freeHeldRaw`, `confirmEmptyFrames`, `confirmHolderLatched`, `firstLoadButton`, `firstLoadDoneButton`, `firstLoadCancelButton`, `bannerDetails`, `bannerDismiss`, `confirmScannerButton`, `acceptSelection`, `reexportSelection`, `undoSelection`, `selectFlagged`, `recoverRaw<n>`, `themeToggle` (or its overflow action); (3) dialog rect within the window and footer buttons fully visible without scrolling; (4) `sheetView.height >= 300` at 800x600 worst stack and >= 0.5 x page height at every size with the status area collapsed; one full tray row fits; (5) no `SlideThumb` caption `truncated` at width >= 800, toolbar visible items do not exceed toolbar width; (6) engine `warnings` list empty (catches the `Dialog.qml:303` loops); (7) keyboard: with each drawer opened then closed, Left/Right/Up/Down/Space/A/R still reach `bridge`; (8) performance: 300 appended trays grow the row count of `bridge.sheet` without a model reset (extend `tests/qt/test_sheet_identity.py`), instantiated delegates <= visible rows + cacheBuffer at 800x600 and 3840x2160.

## 6. Risks and unknowns
Risks: ActionToolBar look differs from PillButton (accept for secondary actions); reparenting loses `Layout.*` attached values; modal drawers take keyboard focus (mitigated by `focusReview()` + criterion 7); breakpoints shift with font size (intended; long translations rely on ActionToolBar measuring); a GridLayout per delegate should cost about the same as the RowLayout (criterion 8 guards it).
Not determinable offscreen: real window-manager behaviour (decorations, maximize, KDE/GNOME fractional scaling, gamescope on Steam Deck); the actual `gridUnit` on the owner's desktop (verify it is 18 at the start of PR 2); the empty square dialog close buttons (probably no icon theme offscreen); whether an empty `holderLatchText` Label keeps one line of height; whether the `Dialog.qml:303` loops come purely from the fixed `implicitWidth`; touch usability on real hardware; glyph raster clipping the probe does not measure.
