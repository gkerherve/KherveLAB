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
class Colours:
    background: str
    closed: str
    grid: str
    grid_hour: str
    header: str
    header_text: str
    today: str
    now_line: str
    text: str
    muted: str

    @staticmethod
    def light() -> "Colours":
        return Colours("#ffffff", "#f1f3f5", "#eceef1", "#d0d7de", "#f6f8fa", "#24292f",
                       "#ddf4ff", "#cf222e", "#24292f", "#57606a")

    @staticmethod
    def dark() -> "Colours":
        return Colours("#1e1f22", "#26282c", "#2b2d31", "#3d4048", "#25272b", "#e6e6e6",
                       "#1c3247", "#ff6b6b", "#e6e6e6", "#9aa4af")


def apply(app: QApplication, dark: bool) -> Colours:
    app.setStyle("Fusion")
    p = QPalette()
    if dark:
        base, alt, text, window, button, hl = ("#1e1f22", "#26282c", "#e6e6e6", "#2b2d31",
                                               "#33363b", "#2f81f7")
    else:
        base, alt, text, window, button, hl = ("#ffffff", "#f6f8fa", "#24292f", "#f3f4f6",
                                               "#f6f8fa", "#0969da")
    R = QPalette.ColorRole
    for role, colour in ((R.Window, window), (R.WindowText, text), (R.Base, base),
                         (R.AlternateBase, alt), (R.Text, text), (R.Button, button),
                         (R.ButtonText, text), (R.ToolTipBase, base), (R.ToolTipText, text),
                         (R.Highlight, hl), (R.HighlightedText, "#ffffff"),
                         (R.PlaceholderText, "#8c959f")):
        p.setColor(role, QColor(colour))
    for role in (R.Text, R.ButtonText, R.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#8c959f"))
    app.setPalette(p)
    return Colours.dark() if dark else Colours.light()


def readable_text(hex_colour: str) -> str:
    c = QColor(hex_colour)
    lum = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
    return "#000000" if lum > 0.55 else "#ffffff"
