import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Description and estimated date of what is selected: slides, one tray (only trays selected), or
// the whole batch (nothing selected). The "Apply to" box picks what an edit reaches (the batch
// scopes are in batch/meta_bulk.py) and a line under the fields counts what it will change.
// Edits apply on Enter or leaving a field (Add to description: on Enter only); the review
// shortcuts never see keys typed here (MainPage skips them).
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
    property bool dateDirty: false
    // What an edit applies to, recorded when the owner starts it: a click elsewhere changes the
    // selection before the field loses focus, so the live selection is the wrong target.
    property var target: null
    property string shownDescription: ""
    property string shownDate: ""
    property bool pendingBatchFocus: false
    // What an edit reaches, from the "Apply to" box; the first entry is the selection's own scope.
    readonly property var scopeOptions: {
        const bulk = [
            {
                key: "batch",
                label: qsTr("Entire batch")
            },
            {
                key: "from_now_on",
                label: qsTr("Only trays scanned from now on")
            },
            {
                key: "tray_range",
                label: qsTr("Tray range")
            },
            {
                key: "empty_only",
                label: qsTr("Only slides with no value")
            },
            {
                key: "append",
                label: qsTr("Add to description")
            }
        ];
        if (scope === "slides")
            return [{
                    key: "slides",
                    label: qsTr("Selected slides")
                }].concat(bulk);
        if (scope === "tray" && singleTray > 0)
            return [{
                    key: "tray",
                    label: qsTr("This tray")
                }].concat(bulk);
        return bulk;
    }
    readonly property string chosenScope: scopeBox.currentValue || (scope === "batch" ? "batch" : scope)
    readonly property bool appendMode: chosenScope === "append"
    property int trayTotal: 1
    property string previewText: ""
    property bool previewRefused: false
    // shown on request even where a short window would leave it out (see MainPage)
    property bool requested: false
    property string requestedScope: ""
    signal reviewFocusRequested()

    // short windows: the thinnest band that still holds a full-size control
    readonly property real verticalInset: shortWindow ? Theme.spacingSmall / 4 : Theme.spacingSmall
    implicitHeight: content.implicitHeight + 2 * verticalInset
    // one row: what a short window must find room for before it shows the strip
    readonly property real rowHeight: descriptionRow.implicitHeight + 2 * verticalInset
    color: Theme.card
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("Description and date")

    onSummaryChanged: sync()
    // The selection moved (a click, a scan tick): finish what was typed for the old target.
    onIdentityChanged: {
        error = "";
        commitPending();
        scopeBox.currentIndex = 0;  // each new selection starts at its own scope
        if (requested && scope !== requestedScope)
            requested = false;
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
        trayTotal = Math.max(1, bridge.trays.rowCount());
        if (!descriptionDirty && !appendMode) {
            shownDescription = meta.description || "";
            descriptionField.text = shownDescription;
        }
        if (!dateDirty) {
            shownDate = meta.date || "";
            dateField.setDate(shownDate);
        }
        if (pendingBatchFocus && scope === "batch") {
            pendingBatchFocus = false;
            Qt.callLater(() => descriptionField.forceActiveFocus(Qt.OtherFocusReason));
        }
    }

    // Hands-free scanning: edit the entire batch ("Batch: ...") without stopping.
    function focusBatch() {
        requested = true;
        requestedScope = "batch";
        pendingBatchFocus = true;
        if (scope === "batch")
            sync();
        else
            bridge.clearSelection();
    }

    // Show the strip for the current selection even where a short window leaves it out.
    function focusSelection() {
        requested = true;
        requestedScope = scope;
        Qt.callLater(() => descriptionField.forceActiveFocus(Qt.OtherFocusReason));
    }

    function currentTarget() {
        return {
            scope: scope,
            mode: chosenScope,
            keys: JSON.parse(JSON.stringify(bridge.selection)),
            tray: singleTray
        };
    }

    // The slides, tray or tray range a bulk edit is aimed at (the backend ignores what its scope
    // does not use).
    function bulkTarget(t) {
        const target = {
            keys: t.keys
        };
        if (t.tray > 0)
            target.tray = t.tray;
        target.first = rangeFrom.value;
        target.last = rangeTo.value;
        return target;
    }

    // "Changes 148 slides" for what is typed and not yet applied, or why it would be refused.
    function refreshPreview() {
        const t = target || currentTarget();
        const parts = [];
        let refused = false;
        const add = (field, value) => {
            const r = bridge.previewMetaEdit(t.mode, field, value, bulkTarget(t));
            parts.push(r.text);
            refused = refused || !r.ok;
        };
        if (descriptionDirty)
            add("description", descriptionField.text);
        if (dateDirty && dateField.dateProblem === "")
            add("date", dateField.dateText);
        previewText = parts.join(" · ");
        previewRefused = refused;
    }

    function beginEdit() {
        if (target === null)
            target = currentTarget();
    }

    function endEditIfDone() {
        if (!descriptionDirty && !dateDirty)
            target = null;
    }

    // Escape: drop what was typed
    function leave() {
        requested = false;
        descriptionDirty = false;
        dateDirty = false;
        target = null;
        descriptionField.text = appendMode ? "" : shownDescription;
        dateField.setDate(shownDate);
        refreshPreview();
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
    function apply(field, kind, value, to) {
        if (!editable)
            return;
        const t = to || currentTarget();
        const batchScope = (t.mode || chosenScope) === "from_now_on" ? "from_now_on" : "inheriting";
        let refusal;
        if (kind === "set")
            refusal = bridge.applyMetaEdit(t.mode || t.scope, field, value, bulkTarget(t));
        else if (t.scope === "slides")
            refusal = kind === "clear" ? bridge.clearSlidesMeta(t.keys, field) : bridge.resetSlidesMeta(t.keys, field);
        else if (t.scope === "batch")
            refusal = bridge.clearBatchMeta(field, batchScope);
        else
            refusal = kind === "clear" ? bridge.clearTrayMeta(t.tray, field) : bridge.resetTrayMeta(t.tray, field);
        // Clear and Reset touch both fields: keep the first refusal of the pair
        if (refusal !== "" || kind === "set")
            error = refusal;
    }

    // Unchanged text is no change: a mixed field typed into and emptied again stays mixed.
    function applyDescription(explicit) {
        if (!descriptionDirty)
            return;
        descriptionDirty = false;
        // Add to description is never applied by leaving the field: only Enter adds the text.
        if (appendMode) {
            if (explicit && descriptionField.text.trim() !== "")
                apply("description", "set", descriptionField.text, target);
            descriptionField.text = "";
        } else if (descriptionField.text !== shownDescription) {
            apply("description", "set", descriptionField.text, target);
        }
        endEditIfDone();
        refreshPreview();
    }

    function applyDate() {
        if (!dateDirty || dateField.dateProblem !== "")
            return;  // the inline date message says why; nothing is sent
        dateDirty = false;
        if (dateField.dateText !== shownDate)
            apply("date", "set", dateField.dateText, target);
        endEditIfDone();
        refreshPreview();
    }

    // before the selection changes (a click on the sheet) or after it changed
    function commitPending() {
        applyDescription();
        applyDate();
        if (dateDirty) {  // an invalid date cannot follow the old target: drop it
            dateDirty = false;
            target = null;
        }
    }

    GridLayout {
        id: content
        anchors.fill: parent
        anchors.leftMargin: Theme.spacing
        anchors.rightMargin: Theme.spacing
        anchors.topMargin: strip.verticalInset
        anchors.bottomMargin: strip.verticalInset
        // one row when wide; description, date and actions stack as the window narrows
        readonly property real room: strip.width - 2 * Theme.spacing
        readonly property real descriptionNeed: scopeLabel.implicitWidth + descriptionField.Layout.minimumWidth + descriptionChip.implicitWidth + (dateToggle.visible ? dateToggle.implicitWidth + Theme.spacingSmall : 0) + 2 * Theme.spacingSmall
        columns: room >= descriptionNeed + dateRow.implicitWidth + actionRow.implicitWidth + 2 * columnSpacing ? 3 : room >= descriptionNeed + dateRow.implicitWidth + columnSpacing ? 2 : 1
        columnSpacing: Theme.spacing
        rowSpacing: Theme.spacingSmall

        RowLayout {
            id: descriptionRow
            Layout.fillWidth: true
            spacing: Theme.spacingSmall
            QQC2.Label {
                id: scopeLabel
                objectName: "metaScopeLabel"
                color: Theme.muted
                font.pixelSize: Theme.fontSmall
                text: strip.scope === "batch" ? qsTr("Entire batch:") : strip.scope === "tray" ? (strip.singleTray > 0 ? qsTr("Tray %1:").arg(strip.singleTray) : qsTr("Select one tray")) : qsTr("%1 selected:").arg(strip.summary.count || 0)
            }
            QQC2.TextField {
                id: descriptionField
                objectName: "metaDescription"
                Layout.fillWidth: true
                Layout.minimumWidth: Theme.gu * 6
                enabled: strip.editable
                maximumLength: 500
                placeholderText: strip.appendMode ? qsTr("Text to add to each description") : strip.meta.description == null ? qsTr("(mixed)") : qsTr("Description")
                Accessible.name: qsTr("Description")
                Accessible.description: qsTr("Press Enter to apply; the line below says what it will change")
                onTextEdited: {
                    strip.beginEdit();
                    strip.descriptionDirty = true;
                    strip.refreshPreview();
                }
                onAccepted: {
                    strip.applyDescription(true);
                    strip.reviewFocusRequested();
                }
                // Qt emits editingFinished before accepted: Add to description waits for Enter
                onEditingFinished: {
                    if (!strip.appendMode)
                        strip.applyDescription(false);
                }
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
                onChanging: {
                    strip.beginEdit();
                    strip.dateDirty = true;
                    Qt.callLater(strip.refreshPreview);
                }
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
                enabled: strip.editable
                Layout.fillWidth: true
                Layout.minimumWidth: Theme.gu * 6
                Layout.maximumWidth: implicitWidth
                model: strip.scopeOptions
                textRole: "label"
                valueRole: "key"
                displayText: qsTr("Apply to: %1").arg(currentText)
                Accessible.name: qsTr("Apply the edit to")
                onActivated: {
                    strip.error = "";
                    strip.refreshPreview();
                }
                onCurrentValueChanged: {
                    if (strip.appendMode)
                        descriptionField.text = "";
                    else
                        strip.sync();
                    strip.refreshPreview();
                }
                Keys.onEscapePressed: strip.leave()
            }
            QQC2.SpinBox {
                id: rangeFrom
                objectName: "metaRangeFrom"
                visible: strip.chosenScope === "tray_range"
                from: 1
                to: strip.trayTotal
                value: 1
                Accessible.name: qsTr("First tray")
                onValueModified: strip.refreshPreview()
                Keys.onEscapePressed: strip.leave()
            }
            QQC2.SpinBox {
                id: rangeTo
                objectName: "metaRangeTo"
                visible: strip.chosenScope === "tray_range"
                from: 1
                to: strip.trayTotal
                value: strip.trayTotal
                Accessible.name: qsTr("Last tray")
                onValueModified: strip.refreshPreview()
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
                    strip.error = "";
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
                    strip.error = "";
                    for (const field of ["description", "date"]) {
                        if (strip.ownLevel(field))
                            strip.apply(field, "reset");
                    }
                }
            }
        }
        PillButton {
            // Shown while some exported JPEGs are out of date: with the automatic update setting on they
            // are rewritten by themselves; this button does it on request (setting off, or a retry).
            objectName: "metaUpdateJpegs"
            Layout.columnSpan: content.columns
            visible: bridge.metadataPending > 0
            enabled: !bridge.readonly
            text: bridge.metadataPending === 1 ? qsTr("Update 1 JPEG") : qsTr("Update %1 JPEGs").arg(bridge.metadataPending)
            Accessible.name: qsTr("Put the current description and date into %1 exported JPEGs").arg(bridge.metadataPending)
            Accessible.description: qsTr("Only the description and date change; the image is not encoded again")
            onClicked: bridge.updateExportedMetadata()
        }
        QQC2.Label {
            objectName: "metaCount"
            Layout.fillWidth: true
            Layout.columnSpan: content.columns
            visible: text !== ""
            text: strip.previewText
            Accessible.role: Accessible.StaticText
            Accessible.name: text
            color: strip.previewRefused ? Kirigami.Theme.negativeTextColor : Theme.muted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.WordWrap
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
