"""Adapt the Qt-free color roles to the palette consumed by native controls."""

from PySide6.QtGui import QColor, QPalette

from opticfilm_linux.app.theme import palette_colors


def make_palette(name: str) -> QPalette:
    palette = QPalette()
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        for role, color in palette_colors(name, disabled=group == QPalette.Disabled).items():
            palette.setColor(group, getattr(QPalette.ColorRole, role), QColor(color))
    return palette


def qml_palette_colors(palette: QPalette) -> dict[str, dict[str, str]]:
    return {
        group_name: {
            role[0].lower() + role[1:]: palette.color(
                group, getattr(QPalette.ColorRole, role)
            ).name()
            for role in palette_colors("dark")
        }
        for group_name, group in (
            ("active", QPalette.Active),
            ("inactive", QPalette.Inactive),
            ("disabled", QPalette.Disabled),
        )
    }
