"""Light and dark Fusion palettes, shared with the rest of the suite.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


@dataclass(frozen=True)
class CalendarColours:
    background: str
    closed: str
    grid: str
    grid_hour: str
    header: str
    header_text: str
    today: str
    now_line: str
    text: str


LIGHT = CalendarColours("#ffffff", "#f3f4f6", "#eceef1", "#d0d7de", "#f6f8fa", "#24292f",
                        "#ddf4ff", "#cf222e", "#24292f")
DARK = CalendarColours("#1e1f22", "#26282c", "#2b2d31", "#3d4048", "#25272b", "#e6e6e6",
                       "#1c3247", "#ff6b6b", "#e6e6e6")


def apply(app: QApplication, dark: bool) -> CalendarColours:
    app.setStyle("Fusion")
    p = QPalette()
    if dark:
        base, alt, text, window, button = "#1e1f22", "#26282c", "#e6e6e6", "#2b2d31", "#33363b"
        highlight = "#2f81f7"
    else:
        base, alt, text, window, button = "#ffffff", "#f6f8fa", "#24292f", "#f3f4f6", "#f6f8fa"
        highlight = "#0969da"
    for role, colour in (
        (QPalette.ColorRole.Window, window), (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, base), (QPalette.ColorRole.AlternateBase, alt),
        (QPalette.ColorRole.Text, text), (QPalette.ColorRole.Button, button),
        (QPalette.ColorRole.ButtonText, text), (QPalette.ColorRole.ToolTipBase, base),
        (QPalette.ColorRole.ToolTipText, text), (QPalette.ColorRole.Highlight, highlight),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.PlaceholderText, "#8c959f"),
    ):
        p.setColor(role, QColor(colour))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#8c959f"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#8c959f"))
    app.setPalette(p)
    return DARK if dark else LIGHT


def readable_text(hex_colour: str) -> str:
    c = QColor(hex_colour)
    lum = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
    return "#000000" if lum > 0.55 else "#ffffff"
