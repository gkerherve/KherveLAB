"""Export a facility Report to .xlsx (KherveSheet) and a .tex project (KherveTeX).

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Charts are not drawn by a new plotting engine: the workbook carries native
spreadsheet charts over its own cells, and the KherveTeX project draws with
pgfplots from numbers written into the .tex, so both stay editable and the
.tex compiles with no image files beside it. Figures use figure* (the same
as figure in one column) because KherveTeX's importer keeps figure* verbatim,
which carries the pgfplots code through an edit unchanged.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .core.reports import Report


# -- .xlsx ------------------------------------------------------------------------

def export_xlsx(rep: Report, target: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="EEF1F4")

    def sheet(title: str, header: list[str], rows: list[list], widths: list[int] | None = None):
        ws = wb.create_sheet(title)
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill = head_font, head_fill
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths or [], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        return ws

    wb.remove(wb.active)
    period = f"{rep.start:%d %b %Y} – {rep.end:%d %b %Y} (end exclusive)"
    ws = sheet("Summary", ["Instrument", "Facility", "Booked h", "Training h", "Bookable h",
                           "Utilisation", "Downtime h", "Faults", "Bookings", "Users"],
               [[i.name, i.facility, round(i.booked, 2), round(i.training, 2),
                 round(i.bookable, 1), i.utilisation, round(i.downtime, 2), i.faults, i.bookings,
                 i.users] for i in rep.instruments],
               [38, 42, 11, 11, 11, 11, 11, 8, 9, 7])
    for row in ws.iter_rows(min_row=2, min_col=6, max_col=6):
        for c in row:
            c.number_format = "0.0%"
    ws.insert_rows(1, 2)
    ws["A1"] = f"{rep.facility} — usage report"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = period
    ws.freeze_panes = "A4"
    n = len(rep.instruments)
    if n:
        ch = BarChart()
        ch.type = "bar"
        ch.title = "Booked hours per instrument"
        ch.y_axis.title = "hours"
        ch.add_data(Reference(ws, min_col=3, max_col=4, min_row=3, max_row=3 + n),
                    titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=4, max_row=3 + n))
        ch.grouping = "stacked"
        ch.overlap = 100
        ch.height, ch.width = max(8, n * 0.45), 18
        ws.add_chart(ch, "L4")

    groups = sheet("By group", ["Group", "Hours"],
                   [[g, round(h, 2)] for g, h in rep.by_group.items()], [40, 10])
    if rep.by_group:
        gc = BarChart()
        gc.title = "Hours by group"
        gc.add_data(Reference(groups, min_col=2, min_row=1, max_row=1 + len(rep.by_group)),
                    titles_from_data=True)
        gc.set_categories(Reference(groups, min_col=1, min_row=2, max_row=1 + len(rep.by_group)))
        groups.add_chart(gc, "D2")
    sheet("By user", ["User", "Hours"], [[u, round(h, 2)] for u, h in rep.by_user.items()],
          [40, 10])

    active = [i for i in rep.instruments if any(rep.monthly[m].get(i.id) for m in rep.monthly)]
    monthly = sheet("Monthly", ["Month", *[i.name for i in active], "Total"],
                    [[m, *[round(rep.monthly[m].get(i.id, 0.0), 2) for i in active],
                      round(sum(rep.monthly[m].values()), 2)] for m in rep.monthly],
                    [10] + [16] * (len(active) + 1))
    if rep.monthly:
        lc = LineChart()
        lc.title = "Booked hours per month"
        col = len(active) + 2
        lc.add_data(Reference(monthly, min_col=col, min_row=1, max_row=1 + len(rep.monthly)),
                    titles_from_data=True)
        lc.set_categories(Reference(monthly, min_col=1, min_row=2, max_row=1 + len(rep.monthly)))
        monthly.add_chart(lc, f"{get_column_letter(col + 2)}2")

    sheet("Downtime", ["Instrument", "Fault", "Opened", "Closed", "Blocking", "Downtime h",
                       "Resolution"],
          [[d.instrument, d.fault, d.opened.replace(tzinfo=None),
            d.closed.replace(tzinfo=None) if d.closed else "open", d.blocking, round(d.hours, 2),
            d.resolution] for d in rep.downtime], [16, 40, 18, 18, 9, 11, 40])
    sheet("Training", ["Person", "Group", "Instrument", "Trained", "Status"],
          [[t.who, t.group, t.instrument, t.trained, t.status] for t in rep.training],
          [30, 24, 18, 12, 12])
    raw = sheet("Raw bookings", ["Instrument", "User id", "User", "Group", "Kind", "Start", "End",
                                 "Hours in period"],
                [[u.instrument, u.user, u.who, u.group, u.kind, u.start.replace(tzinfo=None),
                  u.end.replace(tzinfo=None), round(u.hours, 3)] for u in rep.usage],
                [16, 9, 24, 24, 12, 18, 18, 14])
    for row in raw.iter_rows(min_row=2, min_col=6, max_col=7):
        for c in row:
            c.number_format = "yyyy-mm-dd hh:mm"
            c.alignment = Alignment(horizontal="left")
    wb.save(target)
    return target


# -- KherveTeX project ---------------------------------------------------------------

_TEX_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
            "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}"}


def tex(s: str) -> str:
    s = "".join(_TEX_ESC.get(ch, ch) for ch in str(s))
    return (s.replace("–", "--").replace("—", "---").replace("×", r"$\times$")
            .replace("²", r"\textsuperscript{2}").replace("°", r"\textdegree{}")
            .replace("’", "'").replace("…", r"\ldots{}"))


def _h(x: float) -> str:
    return f"{x:,.1f}".replace(",", r"\,")


def _hbar(title: str, labels: list[str], series: list[tuple[str, list[float]]], xlabel: str,
          stacked: bool = True, percent: bool = False) -> str:
    n = len(labels)
    if not n:
        return ""
    height = max(3.2, 0.55 * n + 1.2)
    coords = []
    plots = []
    colours = ["kblue", "korange", "kgreen", "kgrey"]
    for k, (name, values) in enumerate(series):
        pts = " ".join(f"({v:.3f},{i})" for i, v in enumerate(values))
        plots.append(f"\\addplot+[fill={colours[k % 4]}, draw={colours[k % 4]}!70!black] "
                     f"coordinates {{{pts}}};\n\\addlegendentry{{{tex(name)}}}")
    coords = ",".join(str(i) for i in range(n))
    ticklabels = ",".join("{" + tex(l) + "}" for l in labels)
    opts = ["scale only axis", "width=0.58\\linewidth", f"height={height - 1.0:.1f}cm", "xbar" + (" stacked" if stacked else ""),
            "bar width=8pt", f"ytick={{{coords}}}", f"yticklabels={{{ticklabels}}}",
            "y dir=reverse", "xmin=0", f"xlabel={{{tex(xlabel)}}}", "enlarge y limits=0.06",
            "tick label style={font=\\small}", "legend style={font=\\small, at={(0.98,0.02)}, "
            "anchor=south east}", "axis x line*=bottom", "axis y line*=left",
            "xmajorgrids", "grid style={gray!25}"]
    top = max((v for _, vals in series for v in vals), default=0.0)
    if stacked and len(series) > 1:
        top = max((sum(vals[i] for _, vals in series) for i in range(n)), default=0.0)
    opts += [f"xmax={max(1.0, top * 1.12):.3f}", "scaled x ticks=false",
             "xticklabel style={/pgf/number format/fixed, /pgf/number format/precision=1}"]
    if percent:
        opts.append("xticklabel={\\pgfmathprintnumber[fixed,precision=1]{\\tick}\\,\\%}")
    if len(series) == 1:
        opts.append("legend style={draw=none}")
        plots = [p.split("\n")[0] for p in plots]
    return ("\\begin{figure*}[htbp]\n\\centering\n\\begin{tikzpicture}\n\\begin{axis}[\n  "
            + ",\n  ".join(opts) + "]\n" + "\n".join(plots) + "\n\\end{axis}\n\\end{tikzpicture}\n"
            f"\\caption{{{tex(title)}}}\n\\end{{figure*}}\n")


def _monthly_chart(rep: Report) -> str:
    months = list(rep.monthly)
    if not months:
        return ""
    pts = " ".join(f"({i},{sum(rep.monthly[m].values()):.2f})" for i, m in enumerate(months))
    labels = ",".join(datetime.strptime(m, "%Y-%m").strftime("%b %y") for m in months)
    ticks = ",".join(str(i) for i in range(len(months)))
    return ("\\begin{figure*}[htbp]\n\\centering\n\\begin{tikzpicture}\n\\begin{axis}[\n"
            "  scale only axis, width=0.86\\linewidth, height=5cm, ybar, bar width=10pt, ymin=0,\n"
            f"  xtick={{{ticks}}}, xticklabels={{{labels}}},\n"
            "  x tick label style={rotate=45, anchor=east, font=\\small},\n"
            "  ylabel={booked hours}, axis x line*=bottom, axis y line*=left,\n"
            "  ymajorgrids, grid style={gray!25}, enlarge x limits=0.04]\n"
            f"\\addplot+[fill=kblue, draw=kblue!70!black] coordinates {{{pts}}};\n"
            "\\end{axis}\n\\end{tikzpicture}\n"
            "\\caption{Booked hours per month, all instruments}\n\\end{figure*}\n")


def report_tex(rep: Report) -> str:
    act = rep.active() or rep.instruments
    util = rep.total_booked / rep.total_bookable if rep.total_bookable else 0.0
    down_total = sum(d.hours for d in rep.downtime)
    lines = [
        r"\documentclass[11pt,a4paper]{article}",
        r"\usepackage[margin=2.2cm]{geometry}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{lmodern}",
        r"\usepackage{booktabs}",
        r"\usepackage{longtable}",
        r"\usepackage{xcolor}",
        r"\usepackage{pgfplots}",
        r"\pgfplotsset{compat=1.18}",
        r"\usepackage{textcomp}",
        r"\definecolor{kblue}{HTML}{1F6FEB}",
        r"\definecolor{korange}{HTML}{E16F24}",
        r"\definecolor{kgreen}{HTML}{2DA44E}",
        r"\definecolor{kgrey}{HTML}{8C959F}",
        r"\title{" + tex(rep.facility) + r"\\[2pt]\large Facility report}",
        r"\author{Generated by KherveLAB}",
        r"\date{" + tex(f"{rep.start:%d %B %Y} – {rep.end:%d %B %Y}") + "}",
        r"\begin{document}",
        r"\maketitle",
        "",
        r"\section{Summary}",
        f"Over the period the facility delivered {_h(rep.total_booked)} booked hours "
        f"against {_h(rep.total_bookable)} bookable hours, a utilisation of "
        f"{util * 100:.1f}\\,\\%. {len(rep.downtime)} fault(s) were logged, with "
        f"{_h(down_total)} hours of blocking downtime. "
        f"{sum(i.bookings for i in rep.instruments)} bookings were made by "
        f"{len(rep.by_user)} users from {len(rep.by_group)} groups.",
        "",
    ]
    lines.append(_hbar("Booked hours per instrument", [i.name for i in act],
                       [("Measurement", [i.booked for i in act]),
                        ("Training", [i.training for i in act])], "hours"))
    lines.append(_hbar("Utilisation against bookable hours", [i.name for i in act],
                       [("Utilisation", [100 * i.utilisation for i in act])], "utilisation",
                       stacked=False, percent=True))
    lines.append(_monthly_chart(rep))
    lines += [
        r"\section{Instruments}",
        r"\begin{longtable}{@{}p{0.36\linewidth}rrrrr@{}}",
        r"\toprule",
        r"Instrument & Booked h & Bookable h & Utilisation & Downtime h & Faults \\",
        r"\midrule\endhead",
    ]
    for i in act:
        lines.append(f"{tex(i.name)} & {_h(i.booked + i.training)} & {_h(i.bookable)} & "
                     f"{i.utilisation * 100:.1f}\\,\\% & {_h(i.downtime)} & {i.faults} \\\\")
    lines += [r"\bottomrule", r"\end{longtable}", ""]
    idle = len(rep.instruments) - len(act)
    if idle > 0 and act is not rep.instruments:
        lines += [f"{idle} other instrument(s) had no bookings or faults in the period; "
                  "the spreadsheet export lists every instrument.", ""]
    if rep.by_group:
        top = list(rep.by_group.items())[:15]
        lines.append(_hbar("Hours by group", [g for g, _ in top], [("Hours", [h for _, h in top])],
                           "hours", stacked=False))
    lines += [r"\section{Downtime}"]
    if rep.downtime:
        lines += [r"\begin{longtable}{@{}p{0.16\linewidth}p{0.34\linewidth}llr@{}}", r"\toprule",
                  r"Instrument & Fault & Opened & Closed & Hours \\", r"\midrule\endhead"]
        for d in rep.downtime:
            closed = f"{d.closed:%d %b %Y}" if d.closed else "open"
            lines.append(f"{tex(d.instrument)} & {tex(d.fault)} & {d.opened:%d %b %Y} & "
                         f"{closed} & {_h(d.hours)} \\\\")
        lines += [r"\bottomrule", r"\end{longtable}"]
    else:
        lines.append("No faults were logged in the period.")
    lines += ["", r"\section{Training}"]
    if rep.training:
        lines += [r"\begin{longtable}{@{}p{0.3\linewidth}p{0.25\linewidth}p{0.25\linewidth}l@{}}",
                  r"\toprule", r"Person & Group & Instrument & Trained \\", r"\midrule\endhead"]
        for t in rep.training:
            lines.append(f"{tex(t.who)} & {tex(t.group)} & {tex(t.instrument)} & "
                         f"{t.trained:%d %b %Y} \\\\")
        lines += [r"\bottomrule", r"\end{longtable}"]
    else:
        lines.append("No training records for the period on this computer.")
    lines += ["", r"\end{document}", ""]
    return "\n".join(lines)


def export_khervetex(rep: Report, folder: Path, name: str = "facility-report") -> Path:
    """A KherveTeX project: a folder with the .tex file. Charts are pgfplots,
    so there are no image files to keep alongside."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.tex"
    path.write_text(report_tex(rep), encoding="utf-8")
    return path
