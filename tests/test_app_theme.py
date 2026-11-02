"""Palette construction stays usable without importing Qt."""

import pytest


@pytest.mark.parametrize(
    ("name", "window", "surface", "text", "accent"),
    [
        ("dark", "#171b24", "#232a38", "#f1f5fc", "#83b4ff"),
        ("light", "#f1f4f9", "#ffffff", "#172133", "#255caa"),
    ],
)
def test_palette_supplies_native_control_and_surface_colors(name, window, surface, text, accent):
    from opticfilm_linux.app.theme import palette_colors

    colors = palette_colors(name)
    assert colors["Window"] == window
    assert colors["Base"] == surface
    assert colors["Text"] == colors["WindowText"] == colors["ButtonText"] == text
    assert colors["Highlight"] == colors["Accent"] == accent
    assert colors["HighlightedText"] != text
    assert colors["Button"] != surface
    assert colors["PlaceholderText"] != text
    assert len(colors) == 21


@pytest.mark.parametrize(("name", "muted"), [("dark", "#abb8ce"), ("light", "#53637b")])
def test_disabled_text_is_muted_without_changing_surfaces(name, muted):
    from opticfilm_linux.app.theme import palette_colors

    active = palette_colors(name)
    disabled = palette_colors(name, disabled=True)
    for role in ("Text", "WindowText", "ButtonText"):
        assert disabled[role] == muted
        assert disabled[role] != active[role]
    assert disabled["Window"] == active["Window"]
    disabled["Window"] = "#000000"
    assert palette_colors(name, disabled=True)["Window"] == active["Window"]
