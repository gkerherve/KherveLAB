"""Draws analytics.Chart objects as SVG (app and web) or reportlab drawings (PDF).

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Each chart is laid out once as a list of primitives in a top-left-origin
frame; to_svg and to_drawing only translate them, so the PDF, the app and
the web pages show identical charts.
"""

from __future__ import annotations

import math
from html import escape

W, H = 640, 330
PALETTE = ["#3b7ddd", "#f2a541", "#4caf8e", "#d9534f", "#8e6cc9", "#45b5c4", "#e07bb0",
           "#8c9a3b", "#7f8c99", "#c9763b"]
INK, MUTED, GRID = "#24303d", "#6b7785", "#e3e8ee"


def _fmt(v: float, unit: str = "") -> str:
    if unit == "%":
        return f"{v:.0f}%"
    if unit in ("h", "days"):
        return f"{v:,.0f} {unit}" if abs(v) >= 10 or v == int(v) else f"{v:,.1f} {unit}"
    if abs(v) >= 10000:
        return f"{unit}{v / 1000:,.0f}k"
    if abs(v) >= 1000:
        return f"{unit}{v / 1000:,.1f}k"
    return f"{unit}{v:,.0f}" if abs(v) >= 10 or v == int(v) else f"{unit}{v:,.1f}"


def fmt(v: float, unit: str = "") -> str:
    return _fmt(v, unit)


def _nice_max(v: float) -> tuple[float, float]:
    if v <= 0:
        return 1.0, 0.25
    raw = v / 4
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    return step * math.ceil(v / step), step


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _tw(s: str, size: float) -> float:
    return len(s) * size * 0.55


class _Canvas:
    def __init__(self):
        self.ops: list[tuple] = []

    def rect(self, x, y, w, h, fill, rx=0):
        self.ops.append(("rect", x, y, w, h, fill, rx))

    def line(self, x1, y1, x2, y2, stroke=GRID, width=1.0):
        self.ops.append(("line", x1, y1, x2, y2, stroke, width))

    def poly(self, pts, stroke, width=2.0, fill=None):
        self.ops.append(("poly", pts, stroke, width, fill))

    def circle(self, cx, cy, r, fill):
        self.ops.append(("circle", cx, cy, r, fill))

    def wedge(self, cx, cy, r, a0, a1, fill):
        self.ops.append(("wedge", cx, cy, r, a0, a1, fill))

    def text(self, x, y, s, size=11, anchor="start", fill=INK, bold=False):
        self.ops.append(("text", x, y, s, size, anchor, fill, bold))


def _empty(chart) -> bool:
    return not chart.labels or not any(any(v) for _, v in chart.series)


def layout(chart) -> _Canvas:
    c = _Canvas()
    c.text(16, 26, chart.title, 15, bold=True)
    if _empty(chart):
        c.text(W / 2, H / 2, "No data for this period", 12, "middle", MUTED)
        return c
    {"bar": _bar, "line": _bar, "hbar": _hbar, "stacked": _hbar, "pie": _pie,
     "heatmap": _heat}[chart.kind](c, chart)
    return c


def _legend(c, names, x, y):
    for i, n in enumerate(names):
        c.rect(x, y - 9, 10, 10, PALETTE[i % len(PALETTE)], 2)
        c.text(x + 15, y, n, 10.5)
        x += 30 + _tw(n, 10.5)


def _bar(c, chart):
    vals = chart.series[0][1]
    top, (vmax, step) = 50, _nice_max(max(vals))
    left = 16 + max(_tw(_fmt(vmax, chart.unit), 10), 24)
    right, bottom = W - 16, H - 40
    for k in range(int(round(vmax / step)) + 1):
        y = bottom - (bottom - top) * k * step / vmax
        c.line(left, y, right, y)
        c.text(left - 6, y + 4, _fmt(k * step, chart.unit), 10, "end", MUTED)
    n = len(vals)
    slot = (right - left) / n
    every = max(1, math.ceil(n * 48 / (right - left)))
    pts = []
    for i, v in enumerate(vals):
        x = left + slot * (i + 0.5)
        y = bottom - (bottom - top) * v / vmax
        if chart.kind == "bar":
            bw = min(slot * 0.66, 56)
            c.rect(x - bw / 2, y, bw, bottom - y, PALETTE[0], 3)
            if v and n <= 14:
                c.text(x, y - 5, _fmt(v, chart.unit), 9.5, "middle", MUTED)
        pts.append((x, y))
        if i % every == 0:
            c.text(x, bottom + 18, _short(chart.labels[i], 10), 10, "middle", MUTED)
    if chart.kind == "line":
        area = [(pts[0][0], bottom)] + pts + [(pts[-1][0], bottom)]
        c.poly(area, "none", 0, "#3b7ddd22")
        c.poly(pts, PALETTE[0], 2.5)
        for x, y in pts:
            c.circle(x, y, 3.5, PALETTE[0])
    c.line(left, bottom, right, bottom, MUTED)


def _hbar(c, chart):
    names = [n for n, _ in chart.series]
    stacked = chart.kind == "stacked"
    totals = [sum(v[i] for _, v in chart.series) for i in range(len(chart.labels))]
    vmax, step = _nice_max(max(totals))
    top = 64 if stacked else 46
    if stacked:
        _legend(c, names, 16, 50)
    lw = min(max(_tw(_short(l, 22), 10.5) for l in chart.labels), 150) + 24
    left, right, bottom = lw, W - 60, H - 26
    n = len(chart.labels)
    row = min((bottom - top) / n, 34)
    for k in range(int(round(vmax / step)) + 1):
        x = left + (right - left) * k * step / vmax
        c.line(x, top, x, top + row * n)
        c.text(x, top + row * n + 14, _fmt(k * step, chart.unit), 9.5, "middle", MUTED)
    for i, lab in enumerate(chart.labels):
        y = top + row * i
        bh = row * 0.64
        c.text(left - 8, y + row / 2 + 4, _short(lab, 22), 10.5, "end")
        x = left
        for j, (_, vals) in enumerate(chart.series):
            w = (right - left) * vals[i] / vmax
            if w > 0:
                colour = PALETTE[(j if stacked else i) % len(PALETTE)]
                c.rect(x, y + (row - bh) / 2, w, bh, colour, 3)
            x += w
        c.text(x + 6, y + row / 2 + 4, _fmt(totals[i], chart.unit), 10, fill=MUTED)


def _pie(c, chart):
    vals = chart.series[0][1]
    total = sum(vals)
    cx, cy, r = 170, H / 2 + 14, 112
    a = -90.0
    for i, v in enumerate(vals):
        if v <= 0:
            continue
        sweep = 360 * v / total
        c.wedge(cx, cy, r, a, a + min(sweep, 359.99), PALETTE[i % len(PALETTE)])
        a += sweep
    c.circle(cx, cy, r * 0.56, "#ffffff")
    c.text(cx, cy + 2, _fmt(total, chart.unit), 15, "middle", bold=True)
    c.text(cx, cy + 20, "total", 10, "middle", MUTED)
    x, y = 320, max(60, cy - 11 * len(vals))
    for i, (lab, v) in enumerate(zip(chart.labels, vals)):
        c.rect(x, y - 10, 12, 12, PALETTE[i % len(PALETTE)], 3)
        c.text(x + 18, y, _short(lab, 26), 11)
        c.text(W - 16, y, f"{_fmt(v, chart.unit)}  {100 * v / total:.0f}%", 11, "end", MUTED)
        y += 22


def _heat(c, chart):
    vals = [v for _, v in chart.series]
    vmax = max(max(r) for r in vals) or 1
    left, top = 52, 52
    cw, ch = (W - left - 16) / 24, (H - top - 40) / 7
    for d, row in enumerate(vals):
        c.text(left - 8, top + ch * d + ch / 2 + 4, chart.rows[d], 10.5, "end")
        for h, v in enumerate(row):
            t = v / vmax
            col = "#%02x%02x%02x" % (int(247 - t * (247 - 59)), int(249 - t * (249 - 125)),
                                     int(252 - t * (252 - 221)))
            c.rect(left + cw * h + 1, top + ch * d + 1, cw - 2, ch - 2, col, 3)
    for h in range(0, 24, 2):
        c.text(left + cw * h + cw / 2, top + ch * 7 + 16, chart.labels[h], 9.5, "middle", MUTED)


# -- output ------------------------------------------------------------------------------

def _arc_pts(cx, cy, r, a0, a1):
    n = max(2, int(abs(a1 - a0) / 4))
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * k / n))) for k in range(n + 1)]


def _rgba(col: str) -> tuple[str, float]:
    if len(col) == 9:
        return col[:7], int(col[7:], 16) / 255
    return col, 1.0


def to_svg(chart, width: int | None = None) -> str:
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'width="{width or W}" height="{(width or W) * H // W}" '
           'font-family="Arial" role="img" '
           f'aria-label="{escape(chart.title)}">',
           f'<rect width="{W}" height="{H}" rx="10" fill="#ffffff"/>']
    for op in layout(chart).ops:
        k = op[0]
        if k == "rect":
            _, x, y, w, h, fill, rx = op
            col, a = _rgba(fill)
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                       f'rx="{rx}" fill="{col}" fill-opacity="{a:.2f}"/>')
        elif k == "line":
            _, x1, y1, x2, y2, s, w = op
            out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                       f'stroke="{s}" stroke-width="{w}"/>')
        elif k == "poly":
            _, pts, s, w, fill = op
            col, a = _rgba(fill) if fill else ("none", 1)
            p = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            out.append(f'<polyline points="{p}" fill="{col}" fill-opacity="{a:.2f}" '
                       f'stroke="{s}" stroke-width="{w}" stroke-linejoin="round"/>')
        elif k == "circle":
            _, x, y, r, fill = op
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{fill}"/>')
        elif k == "wedge":
            _, cx, cy, r, a0, a1, fill = op
            p = " ".join(f"{x:.1f},{y:.1f}" for x, y in [(cx, cy)] + _arc_pts(cx, cy, r, a0, a1))
            out.append(f'<polygon points="{p}" fill="{fill}" stroke="#ffffff" '
                       'stroke-width="2"/>')
        else:
            _, x, y, s, size, anchor, fill, bold = op
            weight = ' font-weight="bold"' if bold else ""
            out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
                       f'text-anchor="{anchor}" fill="{fill}"{weight}>{escape(str(s))}</text>')
    out.append("</svg>")
    return "".join(out)


def to_drawing(chart, width: float):
    """A reportlab Drawing of the chart, scaled to width points."""
    from reportlab.graphics.shapes import Circle, Drawing, Group, Line, PolyLine, Polygon, Rect, String
    from reportlab.lib.colors import HexColor

    g = Group()
    font = {True: "Helvetica-Bold", False: "Helvetica"}
    for op in layout(chart).ops:
        k = op[0]
        if k == "rect":
            _, x, y, w, h, fill, rx = op
            col, a = _rgba(fill)
            g.add(Rect(x, H - y - h, w, h, rx=rx, ry=rx, fillColor=HexColor(col),
                       fillOpacity=a, strokeColor=None))
        elif k == "line":
            _, x1, y1, x2, y2, s, w = op
            g.add(Line(x1, H - y1, x2, H - y2, strokeColor=HexColor(s), strokeWidth=w))
        elif k == "poly":
            _, pts, s, w, fill = op
            flat = [v for x, y in pts for v in (x, H - y)]
            if fill:
                col, a = _rgba(fill)
                g.add(Polygon(flat, fillColor=HexColor(col), fillOpacity=a, strokeColor=None))
            else:
                g.add(PolyLine(flat, strokeColor=HexColor(s), strokeWidth=w))
        elif k == "circle":
            _, x, y, r, fill = op
            g.add(Circle(x, H - y, r, fillColor=HexColor(fill), strokeColor=None))
        elif k == "wedge":
            _, cx, cy, r, a0, a1, fill = op
            flat = [v for x, y in [(cx, cy)] + _arc_pts(cx, cy, r, a0, a1) for v in (x, H - y)]
            g.add(Polygon(flat, fillColor=HexColor(fill), strokeColor=HexColor("#ffffff"),
                          strokeWidth=1.5))
        else:
            _, x, y, s, size, anchor, fill, bold = op
            g.add(String(x, H - y, str(s), fontName=font[bold], fontSize=size,
                         fillColor=HexColor(fill), textAnchor=anchor))
    scale = width / W
    g.scale(scale, scale)
    d = Drawing(width, H * scale)
    d.add(g)
    return d
