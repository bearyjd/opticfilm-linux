import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Page {
    id: page

    objectName: "mainPage"
    padding: 0
    title: bridge.collection !== "" ? qsTr("%1 — Box %2, section %3").arg(bridge.batchName).arg(bridge.box).arg(bridge.section) : bridge.batchName
    focus: true

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

    header: QQC2.ToolBar {
        RowLayout {
            anchors.fill: parent
            spacing: Kirigami.Units.largeSpacing

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
            Item {
                Layout.fillWidth: true
            }
            QQC2.Button {
                objectName: "loadHelpButton"
                text: qsTr("How to load the scanner")
                icon.name: "help-contents"
                onClicked: loadHelpDialog.open()
            }
            QQC2.Button {
                text: qsTr("Accept all")
                icon.name: "dialog-ok-apply"
                enabled: !bridge.readonly && bridge.trays.rowCount() > 0
                onClicked: bridge.acceptAll()
            }
            QQC2.Button {
                text: qsTr("Batch orientation…")
                icon.name: "object-rotate-right"
                enabled: !bridge.readonly
                onClicked: orientationDialog.open()
            }
            QQC2.Button {
                text: qsTr("Export…")
                icon.name: "document-export"
                enabled: !bridge.readonly && bridge.trays.rowCount() > 0
                onClicked: exportDialog.open()
            }
            QQC2.Button {
                text: qsTr("Free disk space…")
                icon.name: "edit-clear-all"
                enabled: !bridge.readonly
                onClicked: {
                    bridge.requestFreeSpace();
                    freeSpaceDialog.open();
                }
            }
            QQC2.Button {
                text: qsTr("Close batch")
                icon.name: "document-close"
                onClicked: bridge.closeBatch()
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            visible: bridge.batchAutoExport || bridge.autoStatus !== "" || bridge.emptyConfirmationCount > 0

            QQC2.Label {
                objectName: "autoPipelineStatus"
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: bridge.emptyConfirmationCount > 0 ? qsTr("%1 frame(s) look empty - confirm they are empty to free raw").arg(bridge.emptyConfirmationCount) : bridge.autoStatus
            }
            QQC2.Button {
                objectName: "confirmEmptyFrames"
                visible: bridge.emptyConfirmationTray > 0 && !bridge.readonly
                text: qsTr("Review %1 empty frames in tray %2").arg(bridge.emptyConfirmationCount).arg(bridge.emptyConfirmationTray)
                onClicked: {
                    emptyFramesDialog.tray = bridge.emptyConfirmationTray;
                    emptyFramesDialog.sweepVersion = bridge.emptyConfirmationSweepVersion;
                    emptyFramesDialog.previews = bridge.emptyConfirmationPreviews.slice();
                    emptyFramesDialog.open();
                }
            }
            QQC2.Button {
                objectName: "freeHeldRaw"
                visible: bridge.heldTray > 0 && !bridge.readonly
                text: qsTr("Approve settings and release raw cleanup")
                onClicked: bridge.requestHeldRelease(bridge.heldTray)
            }
        }

        FirstLoadCard {
            onTerminalHelp: loadHelpDialog.open()
        }

        QQC2.Label {
            objectName: "holderLatchText"
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            wrapMode: Text.Wrap
            text: bridge.holderLatchText
        }

        Kirigami.InlineMessage {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            visible: bridge.holderLatchWarning
            type: Kirigami.MessageType.Warning
            text: qsTr("Holder latch is unknown. Reseat and latch the holder before scanning.")
        }

        QQC2.Button {
            objectName: "confirmHolderLatched"
            Layout.margins: Kirigami.Units.smallSpacing
            visible: bridge.canConfirmHolderLatched
            text: qsTr("Confirm holder is seated and latched")
            onClicked: bridge.confirmHolderLatched()
        }

        RowLayout {
            // the scanner's status and the holder controls: own row, so the text is never cut off
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.largeSpacing

            QQC2.Label {
                objectName: "scannerStatusText"
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: bridge.readonly ? qsTr("Read-only — another program has this batch open") : bridge.scannerText
            }
            QQC2.Button {
                objectName: "loadHelpLink"
                visible: !bridge.readonly && bridge.scannerStatus === "not_ready"
                flat: true
                text: qsTr("How to load the scanner")
                icon.name: "help-contextual"
                onClicked: loadHelpDialog.open()
            }
            QQC2.Button {
                objectName: "confirmScannerButton"
                visible: !bridge.readonly && bridge.scannerStatus === "interrupted"
                enabled: bridge.canConfirmScanner
                text: qsTr("Confirm scanner reset…")
                icon.name: "dialog-ok"
                onClicked: confirmScannerDialog.open()
            }
            QQC2.Button {
                objectName: "ejectButton"
                text: qsTr("Eject holder")
                icon.name: "media-eject"
                enabled: bridge.canEject
                onClicked: bridge.ejectHolder()
            }
            Item {
                // a disabled button gets no hover, so the explanation hangs on this wrapper
                implicitWidth: loadNextButton.implicitWidth
                implicitHeight: loadNextButton.implicitHeight
                QQC2.ToolTip.visible: loadNextHover.hovered
                QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
                QQC2.ToolTip.text: bridge.nextStripEnabled ? qsTr("Load the next holder after the first terminal load.") : qsTr("Switched off by OPTICFILM_NEXT_STRIP. Unset it and restart the app to enable loading the next holder.") + " " + qsTr("If the scanner was just switched on, use the terminal steps instead; the driver refuses this and nothing is sent.")

                HoverHandler {
                    id: loadNextHover
                }
                QQC2.Button {
                    id: loadNextButton

                    objectName: "loadNextButton"
                    anchors.fill: parent
                    text: qsTr("Load next holder")
                    icon.name: "go-down"
                    enabled: bridge.canLoadNext
                    onClicked: bridge.loadNextHolder()
                }
            }
        }
        Kirigami.Separator {
            Layout.fillWidth: true
        }
        ErrorBanner {
            Layout.fillWidth: true
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            ColumnLayout {
                Layout.preferredWidth: 230
                Layout.maximumWidth: 230
                Layout.minimumWidth: 180
                Layout.fillHeight: true
                spacing: Theme.spacingSmall
                QQC2.Label {
                    text: qsTr("TRAYS")
                    color: Theme.muted
                    font.pixelSize: Theme.fontSmall
                    Layout.margins: Theme.spacing
                }
                TrayQueue {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    onRecoverRequested: number => { recoverDialog.tray = number; recoverDialog.open(); }
                    onTrayChosen: { sheet.scrollToFocus(); Qt.callLater(() => page.focusReview()); }
                }
                PillButton {
                    objectName: "selectFlagged"
                    Layout.fillWidth: true
                    Layout.margins: Theme.spacingSmall
                    text: qsTr("Select flagged (%1)").arg(bridge.flaggedCount)
                    onClicked: { bridge.selectFlagged(); sheet.scrollToFocus(); Qt.callLater(() => page.focusReview()); }
                }
                PillButton {
                    objectName: "selectFailed"
                    Layout.fillWidth: true
                    Layout.margins: Theme.spacingSmall
                    text: qsTr("Select failed (%1)").arg(bridge.failedCount)
                    onClicked: { bridge.selectFailed(); sheet.scrollToFocus(); Qt.callLater(() => page.focusReview()); }
                }
            }
            Kirigami.Separator { Layout.fillHeight: true }
            SheetView {
                id: sheet
                Layout.fillWidth: true
                Layout.fillHeight: true
                onReviewFocusRequested: page.focusReview()
            }
            Kirigami.Separator { Layout.fillHeight: true }
            InspectorPanel {
                id: inspector
                Layout.preferredWidth: Math.min(360, page.width * 0.3)
                Layout.maximumWidth: 360
                Layout.minimumWidth: 270
                Layout.fillHeight: true
                onReexportRequested: reexportDialog.open()
                onRescanRequested: rescanDialog.open()
            }
        }
        QQC2.Label {
            Layout.fillWidth: true
            Layout.margins: Theme.spacingSmall
            text: qsTr("Ctrl+A select all · Shift+click range · Space accept toggle · A accept · R reject · N needs look · [ ] rotate · M mirror · Ctrl+Z undo")
            color: Theme.muted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.Wrap
        }
    }
}
