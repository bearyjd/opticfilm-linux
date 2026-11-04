import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Page {
    id: page

    objectName: "mainPage"
    padding: 0
    bottomPadding: shortWindow ? 0 : page.Window.window.themeFooterHeight
    title: bridge.collection !== "" ? qsTr("%1 — Box %2, section %3").arg(bridge.batchName).arg(bridge.box).arg(bridge.section) : bridge.batchName
    focus: true
    readonly property bool shortWindow: height < 45 * Theme.gu
    readonly property bool compact: width < 50 * Theme.gu
    readonly property bool expanded: width >= 68 * Theme.gu
    readonly property string widthClass: compact ? "compact" : expanded ? "expanded" : "medium"
    readonly property real trayWidth: Math.max(Theme.paneMin, Math.min(width * 0.16, Theme.paneMax))
    readonly property real inspectorWidth: Math.max(Theme.inspectorMin, Math.min(width * 0.25, Theme.inspectorMax))
    onCompactChanged: if (!compact) trayDrawer.close()
    onExpandedChanged: if (expanded) inspectorDrawer.close()
    readonly property string shortcutText: qsTr("Ctrl+A select all · Shift+click range · Space accept toggle · A accept · R reject · N needs look · [ ] rotate · M mirror · Ctrl+Z undo")

    function focusReview() {
        const active = page.Window.window ? page.Window.window.activeFocusItem : null;
        if (active && active !== page)
            active.focus = false;
        page.forceActiveFocus();
    }

    function primaryAction() {
        if (bridge.canScan)
            bridge.scan();
        else if (bridge.canLoadNext)
            bridge.loadNextHolder();
        else if (bridge.canFirstLoad)
            bridge.firstLoad();
        else if (bridge.canConfirmScanner)
            confirmScannerDialog.open();
    }

    function handleKey(event) {
        const active = page.Window.window ? page.Window.window.activeFocusItem : null;
        if (active && typeof active.cursorPosition === "number") return;
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

    QQC2.Popup {
        id: shortcutsPopup
        objectName: "shortcutsPopup"
        parent: QQC2.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(32 * Theme.gu, parent.width * 0.9)
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
            spacing: Theme.spacingSmall

            PillButton {
                objectName: "primaryActionButton"
                text: bridge.scanPending ? qsTr("Scanning…") : bridge.canScan ? qsTr("Scan") : bridge.canLoadNext ? qsTr("Load next holder") : bridge.canFirstLoad ? qsTr("First load…") : bridge.canConfirmScanner ? qsTr("Restart scanner…") : qsTr("Scan")
                icon.name: "scanner"
                highlighted: true
                enabled: bridge.canScan || bridge.canLoadNext || bridge.canFirstLoad || bridge.canConfirmScanner
                onClicked: page.primaryAction()
            }
            QQC2.Button {
                objectName: "nextSectionButton"
                text: qsTr("Next section")
                visible: bridge.collection !== ""
                enabled: bridge.canFinishBox
                onClicked: bridge.finishBox(false)
            }
            QQC2.Button {
                objectName: "finishBoxButton"
                text: qsTr("Finish box")
                visible: bridge.collection !== ""
                enabled: bridge.canFinishBox
                onClicked: bridge.finishBox(true)
            }
            QQC2.Button {
                objectName: "trayDrawerToggle"
                text: qsTr("Trays")
                icon.name: "view-list-details"
                visible: page.compact
                onClicked: trayDrawer.open()
            }
            QQC2.Button {
                objectName: "inspectorDrawerToggle"
                text: qsTr("Inspector (%1)").arg(inspector.count)
                icon.name: "document-properties"
                visible: !page.expanded
                onClicked: inspectorDrawer.open()
            }
            Kirigami.ActionToolBar {
                objectName: "secondaryActionToolBar"
                Layout.fillWidth: true
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
                    }
                ]
            }
        }
    }

    Kirigami.OverlayDrawer {
        id: trayDrawer
        objectName: "trayDrawer"
        edge: Qt.LeftEdge
        modal: true
        focus: true
        handleVisible: false
        handle.parent: page
        handle.visible: false
        width: Math.min(22 * Theme.gu, page.width * 0.9)
        contentItem: Item {}
        onClosed: page.focusReview()
    }
    Kirigami.OverlayDrawer {
        id: inspectorDrawer
        objectName: "inspectorDrawer"
        edge: Qt.RightEdge
        modal: true
        focus: true
        handleVisible: false
        handle.parent: page
        handle.visible: false
        width: Math.min(22 * Theme.gu, page.width * 0.9)
        contentItem: Item {}
        onClosed: page.focusReview()
    }
    TrayPane {
        id: trayPane
        parent: page.compact ? trayDrawer.contentItem : trayDockSlot
        anchors.fill: parent
        shortWindow: page.shortWindow
        onRecoverRequested: number => { recoverDialog.tray = number; recoverDialog.open(); }
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
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        StatusArea {
            id: statusArea
            Layout.fillWidth: true
            // One budget for both: the column's real height (what enforces the review minimum),
            // floored so the always-visible scanner row can never be squashed away.
            readonly property real budget: Math.max(2 * Theme.controlHeight, parent.height - Theme.reviewMin(page.height)
                - (shortcuts.visible ? shortcuts.implicitHeight + 2 * Theme.spacingSmall : 0))
            Layout.maximumHeight: budget
            Layout.preferredHeight: Math.max(2 * Theme.controlHeight, Math.min(implicitHeight, budget))
            shortWindow: page.shortWindow
            compact: page.width < 50 * Theme.gu
            onLoadHelp: loadHelpDialog.open()
            onConfirmScanner: confirmScannerDialog.open()
            onReviewEmptyFrames: {
                emptyFramesDialog.tray = bridge.emptyConfirmationTray;
                emptyFramesDialog.sweepVersion = bridge.emptyConfirmationSweepVersion;
                emptyFramesDialog.previews = bridge.emptyConfirmationPreviews.slice();
                emptyFramesDialog.open();
            }
        }

        RowLayout {
            objectName: "reviewArea"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: Theme.reviewMin(page.height)
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
