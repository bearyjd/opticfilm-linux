import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

// An estimated date: a year, optionally a month, never a day. Shared by the start page and the
// metadata strip. Children are named namePrefix + "Year", "Month" and "DateClear".
RowLayout {
    id: field
    property string namePrefix: ""
    property string help: qsTr("A guess is fine. Saved as approximate.")
    readonly property alias yearText: yearField.text
    readonly property alias yearFocused: yearField.activeFocus
    // "1978", "1978-07" or "" (no date)
    readonly property string dateText: yearField.text === "" ? "" : yearField.text + (monthBox.currentIndex > 0 ? "-" + String(monthBox.currentIndex).padStart(2, "0") : "")
    readonly property string dateProblem: bridge.validateDate(dateText)
    // the message waits until the year looks finished
    readonly property bool showDateProblem: dateProblem !== "" && (yearField.text.length >= 4 || !yearField.activeFocus)
    // the owner finished a change: Enter or leaving the year, a month chosen, or Clear
    signal edited()

    function setDate(text) {
        const parts = (text || "").split("-");
        yearField.text = parts[0] || "";  // the year first: an empty year resets the month
        monthBox.currentIndex = parts.length > 1 ? Math.max(0, Math.min(12, parseInt(parts[1], 10) || 0)) : 0;
    }

    QQC2.TextField {
        id: yearField
        objectName: field.namePrefix + "Year"
        placeholderText: qsTr("Year")
        Accessible.name: qsTr("Year")
        Accessible.description: field.help
        inputMethodHints: Qt.ImhDigitsOnly
        Layout.preferredWidth: Theme.gu * 8
        validator: RegularExpressionValidator {
            regularExpression: /[0-9]{0,4}/
        }
        onTextChanged: if (text === "") monthBox.currentIndex = 0
        onEditingFinished: field.edited()
    }
    QQC2.ComboBox {
        id: monthBox
        objectName: field.namePrefix + "Month"
        Accessible.name: qsTr("Month")
        Accessible.description: field.help
        model: ["—", qsTr("Jan"), qsTr("Feb"), qsTr("Mar"), qsTr("Apr"), qsTr("May"), qsTr("Jun"), qsTr("Jul"), qsTr("Aug"), qsTr("Sep"), qsTr("Oct"), qsTr("Nov"), qsTr("Dec")]
        enabled: yearField.text !== ""
        onActivated: field.edited()
    }
    PillButton {
        objectName: field.namePrefix + "DateClear"
        text: qsTr("Clear")
        Accessible.name: qsTr("Clear the date")
        enabled: yearField.text !== ""
        onClicked: {
            yearField.text = "";
            field.edited();
        }
    }
}
