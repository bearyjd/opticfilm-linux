import QtQuick
import QtQml.Models

// Binding the whole palette (or only QApplication's) misses Basic's initial colors.
Instantiator {
    id: paletteBindings

    required property var window
    required property var colors

    model: Object.keys(colors)
    delegate: Instantiator {
        id: groupBindings

        required property string modelData

        model: Object.keys(paletteBindings.colors[modelData])
        delegate: Binding {
            required property string modelData
            target: paletteBindings.window.palette[groupBindings.modelData]
            property: modelData
            value: paletteBindings.colors[groupBindings.modelData][modelData]
        }
    }
}
