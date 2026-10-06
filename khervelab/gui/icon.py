"""The KherveLAB mark, drawn with QPainter (no image file ships with the app).

A blue rounded tile with "KLAB" over a small week calendar whose booked
slots are filled in. ``app_icon`` is the window icon; the packaging renders
the .ico / .icns from ``app_icon_pixmap`` so they always match.

Copyright (C) 2026 Gwilherm Kerherve
Licensed under the GNU General Public License v3.0 or later (see LICENSE).
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap

TOP, BOTTOM = QColor("#3b82d6"), QColor("#1d4f91")

# (column, first row, rows) of the booked cells in the 5 x 4 grid
_BOOKED = [(0, 0, 2), (1, 1, 2), (2, 0, 1), (3, 2, 2), (4, 0, 3)]


def app_icon_pixmap(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.scale(size / 256.0, size / 256.0)
    grad = QLinearGradient(0, 0, 0, 256)
    grad.setColorAt(0, TOP)
    grad.setColorAt(1, BOTTOM)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(4, 4, 248, 248), 44, 44)

    font = QFont("Arial")
    font.setPixelSize(70)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(QRectF(0, 18, 256, 84), Qt.AlignmentFlag.AlignCenter, "KLAB")

    # calendar: white card, header band, 5 x 4 cells
    card = QRectF(40, 112, 176, 116)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("white"))
    p.drawRoundedRect(card, 14, 14)
    p.setBrush(QColor("#9cc3f0"))
    p.drawRoundedRect(QRectF(40, 112, 176, 22), 14, 14)
    p.drawRect(QRectF(40, 124, 176, 10))
    x0, y0, cw, ch = 50.0, 142.0, 31.2, 19.0
    p.setPen(QPen(QColor("#d5e3f3"), 2))
    for c in range(6):
        p.drawLine(int(x0 + c * cw), int(y0), int(x0 + c * cw), int(y0 + 4 * ch))
    p.setPen(Qt.PenStyle.NoPen)
    for i, (c, r, n) in enumerate(_BOOKED):
        p.setBrush(QColor("#2563b8") if i % 2 == 0 else QColor("#f59e0b"))
        p.drawRoundedRect(QRectF(x0 + c * cw + 3, y0 + r * ch + 2, cw - 6, n * ch - 4), 5, 5)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(s))
    return icon
