"""Printable QR labels for samples: plain A4 and common Avery sheets.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

The QR code holds the bare sample id, so a handheld scanner (which types
what it reads) opens the sample from the "Open sample" box. reportlab's own
QR widget is used; the qrcode package would add nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from ..core.samples import Sample


@dataclass(frozen=True)
class Layout:
    name: str
    cols: int
    rows: int
    width: float      # label size, mm
    height: float
    left: float       # page margins, mm
    top: float
    hpitch: float     # distance between label origins, mm
    vpitch: float


LAYOUTS = {
    "a4-40": Layout("A4, 40 per sheet (52.5 × 29.7 mm)", 4, 10, 52.5, 29.7, 0, 0, 52.5, 29.7),
    "avery-l7160": Layout("Avery L7160, 21 per sheet (63.5 × 38.1 mm)", 3, 7, 63.5, 38.1,
                          7.21, 15.15, 66.04, 38.1),
    "avery-l7163": Layout("Avery L7163, 14 per sheet (99.1 × 38.1 mm)", 2, 7, 99.1, 38.1,
                          4.65, 15.15, 101.6, 38.1),
    "avery-l7651": Layout("Avery L7651, 65 per sheet (38.1 × 21.2 mm)", 5, 13, 38.1, 21.2,
                          4.75, 10.7, 40.64, 21.2),
}


def _fit(c: canvas.Canvas, text: str, font: str, size: float, width: float) -> str:
    while text and c.stringWidth(text, font, size) > width:
        text = text[:-2] + "…" if len(text) > 2 else ""
    return text


def make_labels(samples: list[Sample], target: Path, layout: str = "avery-l7160",
                skip: int = 0, copies: int = 1) -> Path:
    """`skip` leaves the first labels of a part-used sheet blank."""
    lay = LAYOUTS[layout]
    c = canvas.Canvas(str(target), pagesize=A4)
    c.setTitle("KherveLAB sample labels")
    page_w, page_h = A4
    per_page = lay.cols * lay.rows
    items = [s for s in samples for _ in range(copies)]
    slot = skip
    for s in items:
        if slot and slot % per_page == 0:
            c.showPage()
        pos = slot % per_page
        col, row = pos % lay.cols, pos // lay.cols
        x = (lay.left + col * lay.hpitch) * mm
        y = page_h - (lay.top + row * lay.vpitch + lay.height) * mm
        w, h = lay.width * mm, lay.height * mm
        pad = 1.5 * mm
        qr_size = h - 2 * pad
        widget = QrCodeWidget(s.id, barLevel="M")
        b = widget.getBounds()
        d = Drawing(qr_size, qr_size, transform=[qr_size / (b[2] - b[0]), 0, 0,
                                                 qr_size / (b[3] - b[1]), 0, 0])
        d.add(widget)
        renderPDF.draw(d, c, x + pad, y + pad)
        tx = x + pad + qr_size + 1.5 * mm
        tw = w - (tx - x) - pad
        big = 9 if lay.height > 25 else 7
        c.setFont("Helvetica-Bold", big)
        c.drawString(tx, y + h - pad - big, _fit(c, s.id, "Helvetica-Bold", big, tw))
        c.setFont("Helvetica", big - 1.5)
        lines = [s.name, s.composition, f"from {s.parent}" if s.parent else "",
                 s.created.isoformat() if s.created else ""]
        ly = y + h - pad - big - (big + 1)
        for line in lines:
            if not line or ly < y + pad:
                continue
            c.drawString(tx, ly, _fit(c, line, "Helvetica", big - 1.5, tw))
            ly -= big
        slot += 1
    c.save()
    return target
