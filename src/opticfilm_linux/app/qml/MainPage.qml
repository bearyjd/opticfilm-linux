import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.Page {
    id: page

    property int currentSlide: 1

    objectName: "mainPage"
    padding: 0
    title: bridge.collection !== "" ? qsTr("%1 — Box %2, section %3").arg(bridge.batchName).arg(bridge.box).arg(bridge.section) : bridge.batchName

    // Keyboard use for the review loop.
    Keys.onLeftPressed: currentSlide = Math.max(1, currentSlide - 1)
    Keys.onRightPressed: currentSlide = Math.min(4, currentSlide + 1)
    Keys.onSpacePressed: page.toggleAccepted()
    Keys.onPressed: event => {
        if (event.key === Qt.Key_BracketLeft)
            bridge.rotate(bridge.selectedTray, currentSlide, -1);
        else if (event.key === Qt.Key_BracketRight)
            bridge.rotate(bridge.selectedTray, currentSlide, 1);
        else if (event.key === Qt.Key_M && !bridge.readonly && bridge.selectedTray >= 0)
            bridge.mirror(bridge.selectedTray, currentSlide);
    }
    focus: true

    function toggleAccepted() {
        if (bridge.readonly || bridge.selectedTray < 0)
            return;
        for (let i = 0; i < slideRepeater.count; ++i) {
            const card = slideRepeater.itemAt(i);
            if (card && card.position === currentSlide && card.status !== "blank")
                bridge.setAccepted(bridge.selectedTray, currentSlide, card.status !== "accepted");
        }
    }

    ConfirmDialog {
        id: rescanDialog

        title: qsTr("Re-scan tray %1?").arg(bridge.selectedTray)
        subtitle: qsTr("The old scan is kept on disk, but this tray's slide settings and choices are reset and it is enhanced again.")
        onConfirmed: bridge.rescan(bridge.selectedTray)
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

            QQC2.Button {
                text: bridge.scanPending ? qsTr("Scanning…") : qsTr("Scan")
                icon.name: "scanner"
                highlighted: true
                enabled: bridge.canScan
                onClicked: bridge.scan()
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
                QQC2.ToolTip.text: bridge.nextStripEnabled ? qsTr("Experimental: loading without a terminal is not yet proven on this scanner.") : qsTr("Switched off: loading without a terminal is not yet proven on this scanner. Start the app with OPTICFILM_NEXT_STRIP=1 to try it.") + " " + qsTr("If the scanner was just switched on, use the terminal steps instead; the driver refuses this and nothing is sent.")

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

            TrayQueue {
                Layout.preferredWidth: Kirigami.Units.gridUnit * 16
                Layout.fillHeight: true
            }
            Kirigami.Separator {
                Layout.fillHeight: true
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: Kirigami.Units.largeSpacing
                spacing: Kirigami.Units.largeSpacing

                RowLayout {
                    visible: bridge.selectedTray >= 0
                    Kirigami.Heading {
                        level: 2
                        text: qsTr("Tray %1").arg(bridge.selectedTray)
                        Layout.fillWidth: true
                    }
                    QQC2.Button {
                        text: qsTr("Re-scan this tray…")
                        icon.name: "view-refresh"
                        enabled: bridge.canScan
                        onClicked: rescanDialog.open()
                    }
                }

                QQC2.Label {
                    visible: bridge.selectedTray < 0
                    Layout.alignment: Qt.AlignCenter
                    opacity: 0.7
                    text: qsTr("Select a tray to see its slides.")
                }

                GridLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    columns: 2
                    rowSpacing: Kirigami.Units.smallSpacing
                    columnSpacing: Kirigami.Units.smallSpacing

                    Repeater {
                        id: slideRepeater

                        model: bridge.slides

                        delegate: SlideCard {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.preferredHeight: 1
                            current: position === page.currentSlide
                            onChosen: page.currentSlide = position
                        }
                    }
                }

                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 11
                    visible: bridge.selectedTray >= 0

                    Repeater {
                        model: bridge.slides

                        delegate: AdjustPanel {
                            anchors.fill: parent
                            visible: position === page.currentSlide
                            tray: bridge.selectedTray
                        }
                    }
                }
            }
        }
    }
}
