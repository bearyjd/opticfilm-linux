"""Qt-free palette colors, shared by the application and its palette tests."""

_PALETTES = {
    "dark": {
        "Window": "#171b24",
        "WindowText": "#f1f5fc",
        "Base": "#232a38",
        "AlternateBase": "#2d3749",
        "Text": "#f1f5fc",
        "Button": "#39465b",
        "ButtonText": "#f1f5fc",
        "BrightText": "#102039",
        "Highlight": "#83b4ff",
        "HighlightedText": "#102039",
        "Light": "#526784",
        "Midlight": "#39465b",
        "Mid": "#4b5d78",
        "Dark": "#83b4ff",
        "Shadow": "#0a0e17",
        "Link": "#83b4ff",
        "LinkVisited": "#c6a8ff",
        "ToolTipBase": "#232a38",
        "ToolTipText": "#f1f5fc",
        "PlaceholderText": "#abb8ce",
        "Accent": "#83b4ff",
    },
    "light": {
        "Window": "#f1f4f9",
        "WindowText": "#172133",
        "Base": "#ffffff",
        "AlternateBase": "#e7edf5",
        "Text": "#172133",
        "Button": "#dce4ef",
        "ButtonText": "#172133",
        "BrightText": "#ffffff",
        "Highlight": "#255caa",
        "HighlightedText": "#ffffff",
        "Light": "#ffffff",
        "Midlight": "#dce4ef",
        "Mid": "#acbbcf",
        "Dark": "#255caa",
        "Shadow": "#8698b1",
        "Link": "#255caa",
        "LinkVisited": "#7650a4",
        "ToolTipBase": "#ffffff",
        "ToolTipText": "#172133",
        "PlaceholderText": "#53637b",
        "Accent": "#255caa",
    },
}


def palette_colors(name: str, *, disabled: bool = False) -> dict[str, str]:
    """Return fresh role colors so consumers cannot mutate the shared palette."""
    colors = _PALETTES[name].copy()
    if disabled:
        for role in ("WindowText", "Text", "ButtonText"):
            colors[role] = colors["PlaceholderText"]
    return colors
