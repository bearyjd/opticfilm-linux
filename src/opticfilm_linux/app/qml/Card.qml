import QtQuick
import org.kde.kirigami as Kirigami

Kirigami.Card {
    padding: Theme.spacing
    // severity cards (warning / error) override these with a Theme.warn / Theme.error accent
    property color borderColor: Theme.line
    property int borderWidth: 1

    // AbstractCard creates an independent View color context; set its roles here.
    Kirigami.Theme.inherit: false
    Kirigami.Theme.backgroundColor: Theme.card
    Kirigami.Theme.alternateBackgroundColor: Theme.track
    Kirigami.Theme.textColor: Theme.text
    Kirigami.Theme.disabledTextColor: Theme.muted
    Kirigami.Theme.highlightColor: Theme.accent
    Kirigami.Theme.highlightedTextColor: Theme.accentText
    Kirigami.Theme.linkColor: Theme.accent

    background: Rectangle {
        radius: Theme.cardRadius
        color: Theme.card
        border.color: borderColor
        border.width: borderWidth
    }
}
