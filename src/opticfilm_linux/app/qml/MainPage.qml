import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Page {
    id: page

    objectName: "mainPage"
    padding: 0
    bottomPadding: shortWindow || !page.Window.window ? 0 : page.Window.window.themeFooterHeight
    title: bridge.collection !== "" ? qsTr("%1 — Box %2, section %3").arg(bridge.batchName).arg(bridge.box).arg(bridge.section) : bridge.batchName
    focus: true
    readonly property bool shortWindow: height < Theme.shortMax
    readonly property real reviewMinimum: shortWindow ? 0.4 * (page.Window.window ? page.Window.window.height : page.height) : Theme.reviewMin(height)
    // A short window gives the status area (safety rows) and one tray row priority over the
    // metadata strip; asking for it (Next tray: Edit) still shows it.
    readonly property bool metaStripFits: !shortWindow || statusArea.implicitHeight + metaStrip.rowHeight + Math.max(reviewMinimum, Theme.shortReviewFloor) <= reviewColumn.height
    readonly property real metaStripHeight: metaStrip.visible ? metaStrip.implicitHeight : 0
    readonly property bool compact: width < Theme.compactMax
    readonly property bool expanded: width >= Theme.expandedMin
    readonly property string widthClass: compact ? "compact" : expanded ? "expanded" : "medium"
    readonly property real trayWidth: Math.max(Theme.paneMin, Math.min(width * Theme.paneFraction, Theme.paneMax))
    readonly property real inspectorWidth: Math.max(Theme.inspectorMin, Math.min(width * Theme.inspectorFraction, Theme.inspectorMax))
    onCompactChanged: if (!compact) trayDrawer.close()
    onExpandedChanged: if (expanded) inspectorDrawer.close()
    // Re-parenting a pane across width classes drops the focus it held; put it back on the review.
    onWidthClassChanged: {
        const active = page.Window.window ? page.Window.window.activeFocusItem : null;
        if (!active || holdsFocus(trayPane, active) || holdsFocus(inspector, active))
            Qt.callLater(() => page.focusReview());
    }

    function holdsFocus(pane, active) {
        for (let item = active; item; item = item.parent) {
            if (item === pane)
                return true;
        }
        return false;
    }

    // A modal drawer hides the page, so a blocking safety entry must pull the user back to it.
    function closeDrawers() {
        const wasOpen = trayDrawer.opened || inspectorDrawer.opened;
        trayDrawer.close();
        inspectorDrawer.close();
        if (wasOpen)
            page.focusReview();
    }
    // One control's width: the least the toolbar must keep for its overflow button,
    // and the least any fixed toolbar item may shrink to (they fill up to their natural width, so
    // they give way before the toolbar is starved).
    readonly property real toolbarFloor: Theme.controlHeight
    readonly property real headerInset: width < 7 * toolbarFloor ? Theme.spacingSmall / 2 : Theme.spacing
    readonly property string shortcutText: qsTr("Ctrl+A select all · Shift+click range · Space accept toggle · A accept · R reject · N needs look · [ ] rotate · M mirror · Ctrl+Z undo (outside text fields) · S scan · E eject · L load next holder · X export · F finish box · Enter load & scan (when switched on)")

    function focusReview() {
        const active = page.Window.window ? page.Window.window.activeFocusItem : null;
        if (active && active !== page)
            active.focus = false;
        page.forceActiveFocus();
    }

    function primaryAction() {
        if (bridge.canScan)
            bridge.scan();
        else if (bridge.canLoadAndScan)
            bridge.loadAndScan();
        else if (bridge.canLoadNext)
            bridge.loadNextHolder();
        else if (bridge.canFirstLoad)
            bridge.firstLoad();
        else if (bridge.canConfirmScanner)
            confirmScannerDialog.open();
    }

    // S, E, L, X, F, Enter: the same gates as the buttons; a held key must not repeat a motor move.
    // Enter is only Load & scan (never a plain scan or load); a dialog that is open takes it first.
    function handleTrayKey(event) {
        const keys = [Qt.Key_S, Qt.Key_E, Qt.Key_L, Qt.Key_X, Qt.Key_F, Qt.Key_Return, Qt.Key_Enter];
        const modified = event.modifiers & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier);
        if (modified || keys.indexOf(event.key) < 0) return false;
        const letter = String.fromCharCode(event.key);
        const repeat = event.isAutoRepeat;
        const typing = page.textHasFocus();
        if (repeat && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) return true;
        if (event.key === Qt.Key_S) { if (bridge.keyAllowed(letter, repeat, typing)) bridge.scan(); }
        else if (event.key === Qt.Key_E) { if (bridge.keyAllowed(letter, repeat, typing)) bridge.ejectHolder(); }
        else if (event.key === Qt.Key_L) { if (bridge.keyAllowed(letter, repeat, typing)) bridge.loadNextHolder(); }
        else if (event.key === Qt.Key_X) { if (bridge.keyAllowed(letter, repeat, typing)) exportDialog.open(); }
        else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            // Enter on a focused button (Eject reached by Tab) is about that button
            const active = page.Window.window ? page.Window.window.activeFocusItem : null;
            if (active && active.autoExclusive !== undefined && typeof active.toggle === "function")
                return true;  // an AbstractButton (autoExclusive and toggle() are its own)
            if (bridge.loadAndScanEnabled && bridge.canLoadAndScan) bridge.loadAndScan();
        }
        else if (bridge.keyAllowed(letter, repeat, typing)) finishBoxDialog.open();
        return true;
    }

    function textHasFocus() {
        const active = page.Window.window ? page.Window.window.activeFocusItem : null;
        return !!active && (typeof active.cursorPosition === "number" || holdsFocus(metaStrip, active));
    }

    function handleKey(event) {
        if (page.textHasFocus()) return;  // keys typed into a text field or the strip
        const ctrl = (event.modifiers & Qt.ControlModifier) !== 0;
        const shift = (event.modifiers & Qt.ShiftModifier) !== 0;
        if (ctrl && event.key === Qt.Key_A) bridge.selectAll();
        else if (ctrl && event.key === Qt.Key_Z) bridge.undo();
        else if (ctrl || (event.modifiers & Qt.AltModifier)) return;
        else if (event.key === Qt.Key_Left) bridge.moveSelectionFocus(-1, shift);
        else if (event.key === Qt.Key_Right) bridge.moveSelectionFocus(1, shift);
        else if (event.key === Qt.Key_Up) bridge.moveSelectionFocus(-4, shift);
        else if (event.key === Qt.Key_Down) bridge.moveSelectionFocus(4, shift);
        else if (event.key === Qt.Key_Escape) bridge.clearSelection();
        else if (bridge.readonly) return;
        else if (handleTrayKey(event)) { event.accepted = true; return; }
        else if (event.key === Qt.Key_Space) bridge.toggleSelectionAccepted();
        else if (event.key === Qt.Key_A) bridge.setSelectionStatus("accepted");
        else if (event.key === Qt.Key_R) bridge.setSelectionStatus("rejected");
        else if (event.key === Qt.Key_N) bridge.setSelectionStatus("needs_look");
        else if (event.key === Qt.Key_BracketLeft) bridge.rotateSelection(-1);
        else if (event.key === Qt.Key_BracketRight) bridge.rotateSelection(1);
        else if (event.key === Qt.Key_M) bridge.mirrorSelection();
        else return;
        event.accepted = true;
        sheet.scrollToFocus();
    }

    Keys.priority: Keys.AfterItem
    Keys.onPressed: event => handleKey(event)

    ConfirmDialog {
        id: reexportDialog
        objectName: "reexportSelectionDialog"
        title: qsTr("Re-export selected slides?")
        subtitle: qsTr("This writes new numbered files for the accepted slides in the selection. Existing files are kept. Writing files cannot be undone.")
        onConfirmed: bridge.reexportSelection()
    }
    ConfirmDialog {
        id: recoverDialog
        objectName: "recoverDialog"
        property int tray: 0
        title: qsTr("Recover raw scans of tray %1?").arg(recoverDialog.tray)
        subtitle: qsTr("Recovery verifies the current raw scans, restores this tray to review and resets its export marks. Incomplete older sweeps are kept and do not need verification for recovery. Review the frames, export them again, and approve raw removal again. Missing or damaged current raw scans require restoring the original folders or rescanning.")
        onConfirmed: bridge.recoverInterruptedCleanup(recoverDialog.tray)
    }

    ConfirmDialog {
        id: rescanDialog
        objectName: "rescanSelectionDialog"
        title: qsTr("Re-scan tray %1?").arg(inspector.singleTray)
        subtitle: qsTr("The old scan is kept on disk, but this tray's slide settings and choices are reset and it is enhanced again.")
        onConfirmed: bridge.rescan(inspector.singleTray)
    }
    ConfirmDialog {
        id: heldReleaseDialog
        objectName: "heldReleaseDialog"
        destructive: true
        property int tray: 0
        property string rawBytes: "0"
        title: qsTr("Release raw cleanup for tray %1?").arg(tray)
        subtitle: qsTr("This will remove approximately %1 bytes of raw scans from tray %2. Later trays will be freed automatically after verification. Raw scans can no longer be adjusted or exported again. Choosing Yes confirms that you have opened the delivered JPEGs for this tray and checked the results and settings.").arg(rawBytes).arg(tray)
        onConfirmed: {
            if (tray === bridge.heldTray && !bridge.readonly)
                bridge.confirmFreeHeld(tray);
        }
    }
    Connections {
        target: bridge
        function onHeldReleaseReady(number, size) {
            heldReleaseDialog.tray = number;
            heldReleaseDialog.rawBytes = size;
            heldReleaseDialog.open();
        }
        function onBatchReplaced() {
            heldReleaseDialog.close();
            reexportDialog.close();
            recoverDialog.close();
        }
        function onChanged() {
            if (heldReleaseDialog.tray !== bridge.heldTray)
                heldReleaseDialog.close();
        }
    }
    ConfirmDialog {
        id: confirmScannerDialog

        objectName: "confirmScannerDialog"
        title: qsTr("Confirm the scanner was reset?")
        subtitle: qsTr("A scan or holder move was cut off, so the scanner's state is unknown. Only confirm after you have switched the scanner off until its lights went out, switched it on, and loaded the holder again. Then rescan the interrupted tray.")
        onConfirmed: bridge.confirmScannerReset()
    }
    ConfirmDialog {
        id: finishBoxDialog
        objectName: "finishBoxDialog"
        defaultReject: true
        title: qsTr("Finish this box?")
        subtitle: qsTr("The batch moves on to the next box. Check that every tray of this box is scanned and reviewed.")
        onConfirmed: if (bridge.canFinishBox) bridge.finishBox(true)
    }
    ExportDialog {
        id: exportDialog
    }
    EmptyFramesDialog {
        id: emptyFramesDialog
    }
    FreeSpaceDialog {
        id: freeSpaceDialog
    }
    OrientationDialog {
        id: orientationDialog
    }
    LoadHelpDialog {
        id: loadHelpDialog
    }
    WorkflowSettingsDialog {
        id: workflowSettingsDialog
    }
    TrayMetaDialog {
        id: trayMetaDialog
        onClosed: page.focusReview()
    }
    property bool nextTrayEditPending: false
    property bool metaEditPending: false
    // Inspector: Edit description / date shows the strip for the selection, even in a crowded
    // short window, without changing the selection
    function editSelectionMeta() {
        if (inspectorDrawer.opened) {
            metaEditPending = true;
            inspectorDrawer.close();
        } else {
            metaStrip.focusSelection();
        }
    }
    function editNextTray() {
        if (trayDrawer.opened) {
            nextTrayEditPending = true;  // after the drawer hands focus back to the review
            trayDrawer.close();
        } else {
            metaStrip.focusBatch();
        }
    }

    QQC2.Popup {
        id: shortcutsPopup
        objectName: "shortcutsPopup"
        parent: QQC2.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(Theme.popupMax, parent.width * Theme.overlayFraction)
        modal: true
        focus: true
        closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutside
        onClosed: page.focusReview()
        contentItem: QQC2.Label {
            text: page.shortcutText
            wrapMode: Text.Wrap
        }
    }

    header: QQC2.ToolBar {
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: page.headerInset
            anchors.rightMargin: page.headerInset
            spacing: Theme.spacingSmall

            PillButton {
                objectName: "primaryActionButton"
                text: bridge.scanPending ? (bridge.loadScanPhase === "loading" ? qsTr("Loading the holder…") : bridge.loadScanPhase === "waiting_ready" ? qsTr("Waiting for the scanner…") : qsTr("Scanning…")) : bridge.canScan ? qsTr("Scan") : bridge.canLoadAndScan ? qsTr("Holder latched – Load & scan") : bridge.canLoadNext ? qsTr("Load next holder") : bridge.canFirstLoad ? qsTr("First load…") : bridge.canConfirmScanner ? qsTr("Restart scanner…") : qsTr("Scan")
                icon.name: "scanner"
                highlighted: true
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
                Layout.minimumWidth: page.toolbarFloor
                enabled: bridge.canScan || bridge.canLoadAndScan || bridge.canLoadNext || bridge.canFirstLoad || bridge.canConfirmScanner
                QQC2.ToolTip.visible: hovered && QQC2.ToolTip.text !== ""
                QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
                QQC2.ToolTip.text: bridge.canScan ? qsTr("Scan (S)") : bridge.canLoadAndScan ? qsTr("Only when the holder is seated and latched: load it, then scan it (Enter)") : bridge.canLoadNext ? qsTr("Load next holder (L)") : ""
                onClicked: page.primaryAction()
            }
            QQC2.Button {
                objectName: "nextSectionButton"
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
                Layout.minimumWidth: page.toolbarFloor
                clip: true
                text: qsTr("Next section")
                visible: bridge.collection !== ""
                enabled: bridge.canFinishBox
                onClicked: bridge.finishBox(false)
            }
            QQC2.Button {
                objectName: "finishBoxButton"
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
                Layout.minimumWidth: page.toolbarFloor
                clip: true
                text: qsTr("Finish box")
                visible: bridge.collection !== ""
                enabled: bridge.canFinishBox
                QQC2.ToolTip.visible: hovered
                QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
                QQC2.ToolTip.text: qsTr("Finish box (F)")
                onClicked: bridge.finishBox(true)
            }
            QQC2.Button {
                id: trayToggle
                objectName: "trayDrawerToggle"
                Layout.fillWidth: true
                // Below its full width it shrinks to the icon alone so it cannot starve the overflow button.
                readonly property real fullWidth: traysProbe.implicitWidth
                Layout.maximumWidth: fullWidth
                Layout.minimumWidth: Math.max(page.toolbarFloor, Math.ceil(leftPadding + rightPadding + Math.max(icon.width, Theme.iconMedium)))
                display: width < fullWidth ? QQC2.AbstractButton.IconOnly : QQC2.AbstractButton.TextBesideIcon
                clip: true
                text: qsTr("Trays")
                // Same button in text mode, hidden: its implicit width is the width the label needs.
                QQC2.Button { id: traysProbe; visible: false; font: trayToggle.font; text: trayToggle.text; icon.name: trayToggle.icon.name }
                icon.name: "view-list-details"
                visible: page.compact
                onClicked: trayDrawer.open()
            }
            QQC2.Button {
                id: inspectorToggle
                objectName: "inspectorDrawerToggle"
                text: qsTr("Inspector (%1)").arg(inspector.count)
                // Sized for a two-digit count so the toolbar does not jitter as the selection changes.
                readonly property real stableWidth: inspectorProbe.implicitWidth
                Layout.fillWidth: true
                Layout.maximumWidth: stableWidth
                // Below its stable width it shrinks to the icon alone (the count is still in its accessible name).
                Layout.minimumWidth: Math.max(page.toolbarFloor, Math.ceil(leftPadding + rightPadding + Math.max(icon.width, Theme.iconMedium)))
                display: width < stableWidth ? QQC2.AbstractButton.IconOnly : QQC2.AbstractButton.TextBesideIcon
                clip: true
                QQC2.Button { id: inspectorProbe; visible: false; font: inspectorToggle.font; text: qsTr("Inspector (%1)").arg(99); icon.name: inspectorToggle.icon.name }
                icon.name: "document-properties"
                visible: !page.expanded
                onClicked: inspectorDrawer.open()
            }
            Kirigami.ActionToolBar {
                objectName: "secondaryActionToolBar"
                Layout.fillWidth: true
                // Leftover space only; never less than the overflow button, so "..." stays reachable.
                Layout.preferredWidth: 0
                Layout.minimumWidth: page.toolbarFloor
                alignment: Qt.AlignRight
                actions: [
                    Kirigami.Action {
                        objectName: "loadHelpButton"
                        text: qsTr("How to load the scanner")
                        icon.name: "help-contents"
                        onTriggered: loadHelpDialog.open()
                    },
                    Kirigami.Action {
                        objectName: "acceptAllAction"
                        text: qsTr("Accept all")
                        icon.name: "dialog-ok-apply"
                        enabled: !bridge.readonly && bridge.trays.rowCount() > 0
                        onTriggered: bridge.acceptAll()
                    },
                    Kirigami.Action {
                        objectName: "batchOrientationAction"
                        text: qsTr("Batch orientation…")
                        icon.name: "object-rotate-right"
                        enabled: !bridge.readonly
                        onTriggered: orientationDialog.open()
                    },
                    Kirigami.Action {
                        objectName: "exportAction"
                        text: qsTr("Export…")
                        tooltip: qsTr("Export (X)")
                        icon.name: "document-export"
                        enabled: !bridge.readonly && bridge.trays.rowCount() > 0
                        onTriggered: exportDialog.open()
                    },
                    Kirigami.Action {
                        objectName: "freeSpaceAction"
                        text: qsTr("Free disk space…")
                        icon.name: "edit-clear-all"
                        enabled: !bridge.readonly
                        onTriggered: {
                            bridge.requestFreeSpace();
                            freeSpaceDialog.open();
                        }
                    },
                    Kirigami.Action {
                        objectName: "closeBatchAction"
                        text: qsTr("Close batch")
                        icon.name: "document-close"
                        onTriggered: bridge.closeBatch()
                    },
                    Kirigami.Action {
                        objectName: "themeAction"
                        text: bridge.theme === "dark" ? qsTr("Theme: switch to Light") : qsTr("Theme: switch to Dark")
                        icon.name: "preferences-desktop-theme"
                        displayHint: Kirigami.DisplayHint.AlwaysHide
                        onTriggered: bridge.setTheme(bridge.theme === "dark" ? "light" : "dark")
                    },
                    Kirigami.Action {
                        objectName: "shortcutHelp"
                        text: qsTr("Keyboard shortcuts…")
                        icon.name: "input-keyboard"
                        displayHint: Kirigami.DisplayHint.AlwaysHide
                        onTriggered: shortcutsPopup.open()
                    },
                    Kirigami.Action {
                        objectName: "workflowSettingsAction"
                        text: qsTr("Workflow settings…")
                        icon.name: "configure"
                        displayHint: Kirigami.DisplayHint.AlwaysHide
                        onTriggered: workflowSettingsDialog.open()
                    }
                ]
            }
        }
    }

    ReviewDrawer {
        id: trayDrawer
        objectName: "trayDrawer"
        edge: Qt.LeftEdge
        host: page
        onClosed: if (page.nextTrayEditPending) {
            page.nextTrayEditPending = false;
            metaStrip.focusBatch();
        }
    }
    ReviewDrawer {
        id: inspectorDrawer
        objectName: "inspectorDrawer"
        edge: Qt.RightEdge
        host: page
        onClosed: if (page.metaEditPending) {
            page.metaEditPending = false;
            metaStrip.focusSelection();
        }
    }
    TrayPane {
        id: trayPane
        parent: page.compact ? trayDrawer.contentItem : trayDockSlot
        anchors.fill: parent
        shortWindow: page.shortWindow
        onRecoverRequested: number => { recoverDialog.tray = number; recoverDialog.open(); }
        onNextTrayEditRequested: page.editNextTray()
        onReviewFocusRequested: {
            if (page.compact)
                trayDrawer.close();
            sheet.scrollToFocus();
            Qt.callLater(() => page.focusReview());
        }
    }
    InspectorPanel {
        id: inspector
        parent: page.expanded ? inspectorDockSlot : inspectorDrawer.contentItem
        anchors.fill: parent
        onReexportRequested: reexportDialog.open()
        onRescanRequested: rescanDialog.open()
        onMetaEditRequested: page.editSelectionMeta()
    }

    ColumnLayout {
        id: reviewColumn
        anchors.fill: parent
        spacing: 0

        StatusArea {
            id: statusArea
            Layout.fillWidth: true
            // One budget for both: the column's real height (what enforces the review minimum),
            // floored so the always-visible scanner row can never be squashed away.
            readonly property real budget: Math.min(parent.height, Math.max(minimumStatusHeight, parent.height - page.reviewMinimum
                - page.metaStripHeight - (shortcuts.visible ? shortcuts.implicitHeight + 2 * Theme.spacingSmall : 0)))
            Layout.maximumHeight: budget
            Layout.preferredHeight: Math.max(minimumStatusHeight, heightForBudget(budget))
            shortWindow: page.shortWindow
            compact: page.compact
            onBlockingGained: page.closeDrawers()
            onLoadHelp: loadHelpDialog.open()
            onConfirmScanner: confirmScannerDialog.open()
            onReviewEmptyFrames: {
                emptyFramesDialog.tray = bridge.emptyConfirmationTray;
                emptyFramesDialog.sweepVersion = bridge.emptyConfirmationSweepVersion;
                emptyFramesDialog.previews = bridge.emptyConfirmationPreviews.slice();
                emptyFramesDialog.open();
            }
        }

        MetadataStrip {
            id: metaStrip
            shortWindow: page.shortWindow
            visible: page.metaStripFits || requested
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            onReviewFocusRequested: Qt.callLater(() => page.focusReview())
        }

        RowLayout {
            objectName: "reviewArea"
            Layout.fillWidth: true
            Layout.fillHeight: true
            // At large font scales the review area can drop below 240 px by design: the status area keeps its minimum so safety rows stay reachable.
            Layout.minimumHeight: Math.min(page.reviewMinimum, Math.max(0, parent.height - statusArea.minimumStatusHeight - page.metaStripHeight))
            spacing: 0

            Item {
                id: trayDockSlot
                objectName: "trayDockSlot"
                visible: !page.compact
                Layout.preferredWidth: page.trayWidth
                Layout.minimumWidth: page.trayWidth
                Layout.maximumWidth: page.trayWidth
                Layout.fillHeight: true
            }
            Kirigami.Separator { visible: !page.compact; Layout.fillHeight: true }
            SheetView {
                id: sheet
                Layout.fillWidth: true
                Layout.fillHeight: true
                onReviewFocusRequested: page.focusReview()
                onAboutToSelect: metaStrip.commitPending()
                onTrayMetaRequested: number => {
                    trayMetaDialog.tray = number;
                    trayMetaDialog.meta = bridge.sheet.get(bridge.sheet.indexOfTray(number)).meta || {};
                    trayMetaDialog.open();
                }
            }
            Kirigami.Separator { visible: page.expanded; Layout.fillHeight: true }
            Item {
                id: inspectorDockSlot
                objectName: "inspectorDockSlot"
                visible: page.expanded
                Layout.preferredWidth: page.inspectorWidth
                Layout.minimumWidth: page.inspectorWidth
                Layout.maximumWidth: page.inspectorWidth
                Layout.fillHeight: true
            }
        }
        QQC2.Label {
            id: shortcuts
            visible: !page.shortWindow
            Layout.fillWidth: true
            Layout.margins: Theme.spacingSmall
            text: page.shortcutText
            color: Theme.muted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.Wrap
        }
    }
}
