import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Description and estimated date of what is selected: slides, one tray (only trays selected), or
// the batch default for trays scanned next (nothing selected). Edits apply on Enter or leaving
// a field; the review shortcuts never see keys typed here (MainPage skips them).
Rectangle {
    id: strip
    objectName: "metadataStrip"
    readonly property var summary: bridge.selectionSummary
    readonly property var meta: summary.meta || ({})
    readonly property var levels: summary.metaLevels || ({})
    readonly property string scope: summary.metaScope || "batch"
    readonly property var trays: summary.trays || []
    readonly property int singleTray: trays.length === 1 ? trays[0] : -1
    readonly property bool editable: !bridge.readonly && (scope !== "tray" || singleTray > 0)
    readonly property string identity: JSON.stringify([bridge.selection, scope, trays])
    readonly property bool canReset: editable && scope !== "batch" && (ownLevel("description") || ownLevel("date"))
    readonly property bool canClear: editable && (!isBlankHere("description") || !isBlankHere("date"))
    // Short windows keep the review space: one row, with the date and actions on request.
    property bool shortWindow: false
    property bool expanded: false
    readonly property bool collapsed: shortWindow && !expanded
    property string error: ""
    property bool descriptionDirty: false
    property bool pendingBatchFocus: false
    signal reviewFocusRequested()

    implicitHeight: content.implicitHeight + 2 * Theme.spacingSmall
    color: Theme.card
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("Description and date")

    onSummaryChanged: sync()
    onIdentityChanged: {
        descriptionDirty = false;
        error = "";
        sync();
    }
    Component.onCompleted: sync()

    component LevelChip: QQC2.Label {
        color: Theme.muted
        font.pixelSize: Theme.fontSmall
        leftPadding: Theme.spacingSmall
        rightPadding: Theme.spacingSmall
        background: Rectangle {
            radius: height / 2
            color: Theme.track
        }
    }

    // The fields show the selection's values unless the owner is in the middle of changing them.
    function sync() {
        if (!descriptionDirty)
            descriptionField.text = meta.description || "";
        if (!dateField.editing)
            dateField.setDate(meta.date || "");
        if (pendingBatchFocus && scope === "batch") {
            pendingBatchFocus = false;
            Qt.callLater(() => descriptionField.forceActiveFocus(Qt.OtherFocusReason));
        }
    }

    // Hands-free scanning: edit the batch default ("Next tray") without stopping.
    function focusBatch() {
        pendingBatchFocus = true;
        if (scope === "batch")
            sync();
        else
            bridge.clearSelection();
    }

    function leave() {
        descriptionDirty = false;
        descriptionField.text = meta.description || "";
        dateField.setDate(meta.date || "");
        reviewFocusRequested();
    }

    function levelText(level) {
        if (level == null)
            return qsTr("(mixed)");
        if (scope === "batch")
            return level === "batch" ? qsTr("batch default") : qsTr("not set");
        if (level === "slide" || (level === "tray" && scope === "tray"))
            return qsTr("edited");
        if (level === "tray")
            return singleTray > 0 ? qsTr("from tray %1").arg(singleTray) : qsTr("from their trays");
        return level === "batch" ? qsTr("from batch") : qsTr("not set");
    }

    // the field is set (or mixed) at the level this strip edits
    function ownLevel(field) {
        const level = levels[field];
        return level == null || level === "slide" || (scope !== "slides" && level === scope);
    }

    function isBlankHere(field) {
        return meta[field] === "" && ownLevel(field) && levels[field] != null;
    }

    // kind: "set" (value), "clear" ("" at this level) or "reset" (inherit again)
    function apply(field, kind, value) {
        if (!editable)
            return;
        const batchScope = scopeBox.currentValue;
        if (scope === "batch")
            error = kind === "set" ? bridge.setBatchMeta(field, value, batchScope) : bridge.clearBatchMeta(field, batchScope);
        else if (scope === "tray")
            error = kind === "set" ? bridge.setTrayMeta(singleTray, field, value) : kind === "clear" ? bridge.clearTrayMeta(singleTray, field) : bridge.resetTrayMeta(singleTray, field);
        else
            error = kind === "set" ? bridge.setSelectionMeta(field, value) : kind === "clear" ? bridge.clearSelectionMeta(field) : bridge.resetSelectionMeta(field);
    }

    function applyDescription() {
        if (!descriptionDirty)
            return;
        descriptionDirty = false;
        if (descriptionField.text !== meta.description)
            apply("description", "set", descriptionField.text);
    }

    function applyDate() {
        if (dateField.dateProblem !== "")
            return;  // the inline date message says why; nothing is sent
        const shown = meta.date == null ? "" : meta.date;
        if (dateField.dateText !== shown || meta.date == null && dateField.dateText !== "")
            apply("date", "set", dateField.dateText);
    }

    GridLayout {
        id: content
        anchors.fill: parent
        anchors.leftMargin: Theme.spacing
        anchors.rightMargin: Theme.spacing
        anchors.topMargin: Theme.spacingSmall
        anchors.bottomMargin: Theme.spacingSmall
        // one row when wide; description, date and actions stack as the window narrows
        readonly property real room: strip.width - 2 * Theme.spacing
        readonly property real descriptionNeed: scopeLabel.implicitWidth + descriptionField.Layout.minimumWidth + descriptionChip.implicitWidth + (dateToggle.visible ? dateToggle.implicitWidth + Theme.spacingSmall : 0) + 2 * Theme.spacingSmall
        columns: room >= descriptionNeed + dateRow.implicitWidth + actionRow.implicitWidth + 2 * columnSpacing ? 3 : room >= descriptionNeed + dateRow.implicitWidth + columnSpacing ? 2 : 1
        columnSpacing: Theme.spacing
        rowSpacing: Theme.spacingSmall

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSmall
            QQC2.Label {
                id: scopeLabel
                objectName: "metaScopeLabel"
                color: Theme.muted
                font.pixelSize: Theme.fontSmall
                text: strip.scope === "batch" ? qsTr("Next trays:") : strip.scope === "tray" ? (strip.singleTray > 0 ? qsTr("Tray %1:").arg(strip.singleTray) : qsTr("Select one tray")) : qsTr("%1 selected:").arg(strip.summary.count || 0)
            }
            QQC2.TextField {
                id: descriptionField
                objectName: "metaDescription"
                Layout.fillWidth: true
                Layout.minimumWidth: Theme.gu * 6
                enabled: strip.editable
                maximumLength: 500
                placeholderText: strip.meta.description == null ? qsTr("(mixed)") : qsTr("Description")
                Accessible.name: qsTr("Description")
                Accessible.description: strip.scope === "batch" ? qsTr("Press Enter to apply to trays scanned next") : qsTr("Press Enter to apply to the selection")
                onTextEdited: strip.descriptionDirty = true
                onAccepted: {
                    strip.applyDescription();
                    strip.reviewFocusRequested();
                }
                onEditingFinished: strip.applyDescription()
                Keys.onEscapePressed: strip.leave()
            }
            LevelChip {
                id: descriptionChip
                objectName: "metaLevelDescription"
                text: strip.levelText(strip.levels.description)
                Accessible.name: qsTr("Description: %1").arg(text)
            }
            QQC2.Button {
                id: dateToggle
                objectName: "metaDateToggle"
                visible: strip.shortWindow
                flat: true
                text: strip.collapsed ? (strip.meta.date ? "~" + strip.meta.date : qsTr("Date…")) : qsTr("Less")
                Accessible.name: strip.collapsed ? qsTr("Show the date and the Clear and Reset buttons") : qsTr("Hide the date and the Clear and Reset buttons")
                onClicked: strip.expanded = !strip.expanded
            }
        }
        RowLayout {
            id: dateRow
            visible: !strip.collapsed
            Layout.fillWidth: content.columns === 1
            Layout.minimumWidth: implicitWidth
            spacing: Theme.spacingSmall
            EstimatedDateField {
                id: dateField
                namePrefix: "meta"
                enabled: strip.editable
                yearPlaceholder: strip.meta.date == null ? qsTr("(mixed)") : qsTr("Year")
                onEdited: strip.applyDate()
                Keys.onEscapePressed: strip.leave()
            }
            LevelChip {
                objectName: "metaLevelDate"
                text: strip.levelText(strip.levels.date)
                Accessible.name: qsTr("Date: %1").arg(text)
            }
        }
        RowLayout {
            id: actionRow
            visible: !strip.collapsed
            Layout.fillWidth: content.columns !== 3
            Layout.minimumWidth: Math.min(implicitWidth, content.room)
            Layout.columnSpan: content.columns === 2 ? 2 : 1
            spacing: Theme.spacingSmall
            QQC2.ComboBox {
                id: scopeBox
                objectName: "metaBatchScope"
                visible: strip.scope === "batch"
                enabled: strip.editable
                Layout.fillWidth: true
                Layout.minimumWidth: Theme.gu * 6
                Layout.maximumWidth: implicitWidth
                model: [
                    {
                        key: "from_now_on",
                        label: qsTr("Apply to: only trays scanned from now on")
                    },
                    {
                        key: "inheriting",
                        label: qsTr("Apply to: all slides still inheriting")
                    }
                ]
                textRole: "label"
                valueRole: "key"
                Accessible.name: qsTr("Apply batch changes to")
                Keys.onEscapePressed: strip.leave()
            }
            Item {
                Layout.fillWidth: true
            }
            PillButton {
                objectName: "metaClear"
                text: qsTr("Clear")
                Accessible.name: qsTr("Clear the description and date")
                Accessible.description: qsTr("Leaves them blank here instead of inheriting")
                enabled: strip.canClear
                onClicked: {
                    for (const field of ["description", "date"]) {
                        if (!strip.isBlankHere(field))
                            strip.apply(field, "clear");
                    }
                }
            }
            PillButton {
                objectName: "metaReset"
                text: qsTr("Reset to inherited")
                Accessible.name: qsTr("Reset the description and date to inherited")
                visible: strip.scope !== "batch"
                enabled: strip.canReset
                onClicked: {
                    for (const field of ["description", "date"]) {
                        if (strip.ownLevel(field))
                            strip.apply(field, "reset");
                    }
                }
            }
        }
        QQC2.Label {
            objectName: "metaError"
            Layout.fillWidth: true
            Layout.columnSpan: content.columns
            visible: text !== ""
            text: dateField.showDateProblem ? dateField.dateProblem : strip.error !== "" ? strip.error : strip.summary.metaNote || ""
            Accessible.role: Accessible.AlertMessage
            Accessible.name: text
            color: dateField.showDateProblem || strip.error !== "" ? Kirigami.Theme.negativeTextColor : Theme.muted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.WordWrap
        }
    }
}
