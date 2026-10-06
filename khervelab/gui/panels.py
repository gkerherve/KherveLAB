"""Requests queue, my bookings, and usage/cost reports.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PyQt6.QtCore import QByteArray, QDate, QRectF, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QFileDialog,
                             QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                             QInputDialog, QLabel, QMessageBox, QPushButton, QScrollArea,
                             QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import analytics, charts, db, logic, reports


def _table(headers: list[str], stretch: int = -1) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.verticalHeader().hide()
    if stretch >= 0:
        t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    return t


def _put(t: QTableWidget, row: int, values, key=None, right: tuple[int, ...] = ()):
    for c, v in enumerate(values):
        it = QTableWidgetItem(str(v))
        if key is not None:
            it.setData(Qt.ItemDataRole.UserRole, key)
        if c in right:
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        t.setItem(row, c, it)


def _when(s: str) -> str:
    return s.replace("T", " ")


class RequestsWidget(QWidget):
    """Bookings and accounts waiting for the lab manager."""
    changed = pyqtSignal()
    countChanged = pyqtSignal(int)

    def __init__(self, conn, me, parent=None):
        super().__init__(parent)
        self.conn, self.me = conn, me
        self.bookings = _table(["Instrument", "Who", "From", "To", "Purpose", "Cost"], 4)
        self.accounts = _table(["Name", "Username", "Email", "Group", "Category", "Since"], 2)
        tabs = QTabWidget()
        bw, aw = QWidget(), QWidget()
        bl, al = QVBoxLayout(bw), QVBoxLayout(aw)
        bl.addWidget(self.bookings)
        al.addWidget(self.accounts)
        row = QHBoxLayout()
        for text, slot in (("Approve", lambda: self._booking(True)),
                           ("Reject…", lambda: self._booking(False))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        bl.addLayout(row)
        row = QHBoxLayout()
        for text, slot in (("Approve account", lambda: self._account(True)),
                           ("Refuse", lambda: self._account(False))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        al.addLayout(row)
        self.tabs = tabs
        tabs.addTab(bw, "Bookings")
        tabs.addTab(aw, "Accounts")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(tabs)
        self.refresh()

    def refresh(self):
        cur = db.setting(self.conn, "currency")
        rows = self.conn.execute(
            "SELECT b.*, u.full_name, u.group_name, i.name AS instrument, "
            "EXISTS(SELECT 1 FROM authorised a WHERE a.user_id=b.user_id "
            "AND a.instrument_id=b.instrument_id) AS trained FROM bookings b "
            "JOIN users u ON u.id=b.user_id JOIN instruments i ON i.id=b.instrument_id "
            "WHERE b.status='pending' ORDER BY b.start").fetchall()
        self.bookings.setRowCount(len(rows))
        for r, b in enumerate(rows):
            who = b["full_name"] + (f" ({b['group_name']})" if b["group_name"] else "") + \
                ("" if b["trained"] else " — not trained")
            _put(self.bookings, r, (b["instrument"], who, _when(b["start"]), _when(b["end"]),
                                    b["purpose"], f"{cur}{logic.cost(self.conn, b):,.2f}"),
                 b["id"], right=(5,))
        accs = self.conn.execute("SELECT * FROM users WHERE status='pending' ORDER BY created"
                                 ).fetchall()
        self.accounts.setRowCount(len(accs))
        for r, u in enumerate(accs):
            _put(self.accounts, r, (u["full_name"], u["username"], u["email"], u["group_name"],
                                    u["category"], _when(u["created"])), u["id"])
        for t in (self.bookings, self.accounts):
            t.resizeColumnsToContents()
        self.tabs.setTabText(0, f"Bookings ({len(rows)})")
        self.tabs.setTabText(1, f"Accounts ({len(accs)})")
        self.countChanged.emit(len(rows) + len(accs))

    def _selected(self, t: QTableWidget) -> list[int]:
        return [t.item(i.row(), 0).data(Qt.ItemDataRole.UserRole)
                for i in t.selectionModel().selectedRows()]

    def _booking(self, approve: bool):
        ids = self._selected(self.bookings)
        if not ids:
            QMessageBox.information(self, "Requests", "Select one or more bookings.")
            return
        note = ""
        if not approve:
            note, ok = QInputDialog.getText(self, "Reject", "Reason (shown to the user):")
            if not ok:
                return
        for bid in ids:
            try:
                logic.decide(self.conn, bid, self.me["id"], approve, note)
            except logic.BookingError:
                pass
        self.refresh()
        self.changed.emit()

    def _account(self, approve: bool):
        for uid in self._selected(self.accounts):
            self.conn.execute("UPDATE users SET status=? WHERE id=? AND status='pending'",
                              ("active" if approve else "disabled", uid))
        self.refresh()
        self.changed.emit()


class MyBookingsDialog(QDialog):
    """A person's bookings: your own, or (for the lab manager or a super user)
    anyone's, to open, change or cancel on their behalf."""

    def __init__(self, conn, me, parent=None, owner=None):
        super().__init__(parent)
        self.conn, self.me = conn, me
        self.owner = owner or me
        self.changed = False
        self.setWindowTitle("My bookings" if self.owner["id"] == me["id"] else
                            f"Bookings of {self.owner['full_name']}")
        self.resize(900, 520)
        self.summary = QLabel()
        self.table = _table(["Instrument", "From", "To", "Status", "Cost", "Purpose / note"], 5)
        self.table.doubleClicked.connect(self._open)
        cancel = QPushButton("Cancel booking…")
        cancel.clicked.connect(self._cancel)
        statement = QPushButton("My statement (PDF)…")
        statement.clicked.connect(self._statement)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(cancel)
        row.addWidget(statement)
        row.addStretch(1)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addWidget(self.table)
        lay.addLayout(row)
        self.refresh()

    def refresh(self):
        cur = db.setting(self.conn, "currency")
        rows = self.conn.execute(
            "SELECT b.*, i.name AS instrument FROM bookings b JOIN instruments i "
            "ON i.id=b.instrument_id WHERE b.user_id=? ORDER BY b.start DESC LIMIT 500",
            (self.owner["id"],)).fetchall()
        self.table.setRowCount(len(rows))
        for r, b in enumerate(rows):
            note = b["purpose"] + (f"  — manager: {b['note']}" if b["note"] else "")
            _put(self.table, r, (b["instrument"], _when(b["start"]), _when(b["end"]), b["status"],
                                 f"{cur}{logic.cost(self.conn, b):,.2f}", note), b["id"],
                 right=(4,))
            colour = {"approved": "#1a7f37", "pending": "#9a6700"}.get(b["status"], "#8c959f")
            self.table.item(r, 3).setForeground(QColor(colour))
        self.table.resizeColumnsToContents()
        today = date.today()
        rep = reports.build(self.conn, today.replace(day=1), today, user_id=self.owner["id"])
        self.summary.setText(f"This month so far: <b>{rep.total_hours:.2f} h</b>, "
                             f"<b>{cur}{rep.total_cost:,.2f}</b> (approved bookings).")

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole) if rows else None

    def _open(self, *_):
        from .dialogs import BookingDetailsDialog
        bid = self._selected()
        if bid is None:
            return
        dlg = BookingDetailsDialog(self.conn, self.me, bid, self)
        dlg.exec()
        if dlg.changed:
            self.changed = True
            self.refresh()

    def _cancel(self):
        bid = self._selected()
        if bid is None:
            return
        if QMessageBox.question(self, "Cancel booking", "Cancel this booking?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            logic.cancel(self.conn, bid, self.me)
        except logic.BookingError as exc:
            QMessageBox.warning(self, "Cancel booking", str(exc))
            return
        self.changed = True
        self.refresh()

    def _statement(self):
        dlg = ReportsDialog(self.conn, self.owner, self, only_me=True)
        dlg.exec()


class ChartWidget(QWidget):
    """Shows a charts.to_svg drawing, kept at its aspect ratio."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.renderer = QSvgRenderer(self)
        self.setMinimumWidth(380)

    def set_chart(self, chart):
        self.renderer.load(QByteArray(charts.to_svg(chart).encode()))
        self.setToolTip(chart.title)
        self.update()

    def resizeEvent(self, e):
        # The width comes from the page; the height follows it, so a chart
        # grows to fill its column instead of shrinking to the minimum.
        h = int(self.width() * charts.H / charts.W)
        if self.minimumHeight() != h:
            self.setMinimumHeight(h)
        super().resizeEvent(e)

    def paintEvent(self, _):
        w = min(self.width(), self.height() * charts.W / charts.H)
        h = w * charts.H / charts.W
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.renderer.render(p, QRectF((self.width() - w) / 2, 0, w, h))
        p.end()


class _ChartPage(QScrollArea):
    """A scrolling page of headline cards, charts two to a row, and tables."""

    def __init__(self, keys: list[str], kpis: bool = False, tables: tuple = ()):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("chartPage")
        body.setStyleSheet("#chartPage { background: #e9edf2; }")
        lay = QVBoxLayout(body)
        self.cards: list[QLabel] = []
        if kpis:
            g = QGridLayout()
            for n in range(6):
                card = QLabel()
                card.setStyleSheet("background: white; border-radius: 8px; padding: 10px;")
                card.setTextFormat(Qt.TextFormat.RichText)
                g.addWidget(card, n // 3, n % 3)
                self.cards.append(card)
            lay.addLayout(g)
        grid = QGridLayout()
        self.charts = {}
        for n, k in enumerate(keys):
            w = self.charts[k] = ChartWidget()
            grid.addWidget(w, n // 2, n % 2)
        lay.addLayout(grid)
        self.tables = {}
        for key, headers in tables:
            lay.addWidget(QLabel(f"<b>{key.capitalize()}</b>"))
            t = self.tables[key] = _table(headers, 0)
            t.setMinimumHeight(220)
            lay.addWidget(t)
        lay.addStretch(1)
        self.setWidget(body)

    def show_analytics(self, a: analytics.Analytics):
        for card, k in zip(self.cards, a.kpis):
            card.setText(f"<span style='color:#6b7785'>{k.label}</span><br>"
                         f"<span style='font-size:20px; font-weight:600'>{k.value}</span><br>"
                         f"<span style='color:#6b7785; font-size:11px'>{k.note}</span>")
        for k, w in self.charts.items():
            w.set_chart(a.charts[k])
        cur = a.currency
        if "instruments" in self.tables:
            t = self.tables["instruments"]
            t.setRowCount(len(a.instruments))
            for r, i in enumerate(a.instruments):
                _put(t, r, (i["name"], i["bookings"], i["users"], f"{i['hours']:.1f}",
                            f"{i['bookable']:.1f}", f"{i['utilisation']:.1f}%",
                            f"{cur}{i['revenue']:,.2f}", f"{i['down_hours']:.1f}",
                            f"{i['problem_hours']:.1f}"), right=tuple(range(1, 9)))
            t.resizeColumnsToContents()
        if "groups" in self.tables:
            t = self.tables["groups"]
            t.setRowCount(len(a.groups))
            for r, g in enumerate(a.groups):
                _put(t, r, (g["group"], g["users"], g["bookings"], f"{g['hours']:.1f}",
                            f"{cur}{g['revenue']:,.2f}"), right=(1, 2, 3, 4))
            t.resizeColumnsToContents()


class ReportsDialog(QDialog):
    """Usage and costs for a period; the lab manager sees everyone, a user
    sees only their own statement."""

    def __init__(self, conn, me, parent=None, only_me: bool = False):
        super().__init__(parent)
        self.conn, self.me = conn, me
        self.only_me = only_me or me["role"] != "admin"
        self.rep: reports.Report | None = None
        self.ana: analytics.Analytics | None = None
        self.setWindowTitle("My statement" if self.only_me else "Reports: finance and usage")
        self.resize(1180 if not self.only_me else 980, 780 if not self.only_me else 640)
        today = date.today()
        last_end = today.replace(day=1) - timedelta(days=1)
        self.preset = QComboBox()
        self.preset.addItems(["Last month", "This month", "Last 3 months", "This year",
                              "Last year", "All time", "Custom"])
        self.start = QDateEdit(QDate(last_end.year, last_end.month, 1))
        self.end = QDateEdit(QDate(last_end.year, last_end.month, last_end.day))
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("d MMM yyyy")
        self.user = QComboBox()
        self.user.addItem("Everyone", 0)
        for u in conn.execute("SELECT * FROM users ORDER BY full_name"):
            self.user.addItem(f"{u['full_name']} ({u['username']})", u["id"])
        if self.only_me:
            self.user.setCurrentIndex(self.user.findData(me["id"]))
            self.user.setEnabled(False)
        self.instrument = QComboBox()
        self.instrument.addItem("All instruments", 0)
        for i in conn.execute("SELECT * FROM instruments ORDER BY name"):
            self.instrument.addItem(i["name"], i["id"])
        form = QFormLayout()
        rng = QHBoxLayout()
        rng.addWidget(self.preset)
        rng.addWidget(self.start)
        rng.addWidget(QLabel("to"))
        rng.addWidget(self.end)
        form.addRow("Period", rng)
        form.addRow("User", self.user)
        form.addRow("Instrument", self.instrument)
        self.summary = QLabel()
        self.by_user = _table(["User", "Group", "Bookings", "Hours", "Cost"], 0)
        self.lines = _table(["User", "Instrument", "From", "To", "Hours", "Rate", "Cost"], 1)
        tabs = self.tabs = QTabWidget()
        self.pages: list[_ChartPage] = []
        if not self.only_me:
            S = analytics.Analytics.SECTIONS
            for title, page in (
                    ("Overview", _ChartPage(["revenue_month", "revenue_instrument",
                                             "revenue_category", "utilisation"], kpis=True)),
                    ("Finance", _ChartPage(S["Finance"], tables=(
                        ("groups", ["Group", "Users", "Bookings", "Hours", "Revenue"]),))),
                    ("Usage", _ChartPage(S["Usage"])),
                    ("Instruments", _ChartPage(S["Instruments"] + S["Downtime"], tables=(
                        ("instruments", ["Instrument", "Bookings", "Users", "Hours booked",
                                         "Bookable h", "Utilisation", "Revenue",
                                         "Out of order h", "Problem h"]),)))):
                tabs.addTab(page, title)
                self.pages.append(page)
            tabs.addTab(self.by_user, "By user")
        tabs.addTab(self.lines, "Bookings")
        row = QHBoxLayout()
        exports = [("Save PDF statement…", "pdf"), ("Save Excel…", "xlsx"), ("Save CSV…", "csv")]
        if not self.only_me:
            exports = [("Lab report PDF (charts)…", "lab-pdf"),
                       ("Lab report Excel (charts)…", "lab-xlsx")] + exports
        for text, kind in exports:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, k=kind: self._export(k))
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.summary)
        lay.addWidget(tabs, 1)
        lay.addLayout(row)
        self.preset.currentIndexChanged.connect(self._preset)
        for sig in (self.start.dateChanged, self.end.dateChanged):
            sig.connect(self._custom)
        for sig in (self.user.currentIndexChanged, self.instrument.currentIndexChanged):
            sig.connect(self.compute)
        self.compute()

    def _preset(self, i: int):
        today = date.today()
        first = today.replace(day=1)
        last_end = first - timedelta(days=1)
        three = (first - timedelta(days=62)).replace(day=1)
        ranges = {0: (last_end.replace(day=1), last_end), 1: (first, today),
                  2: (three, last_end), 3: (date(today.year, 1, 1), today),
                  4: (date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
                  5: self._all_time()}
        if i in ranges:
            s, e = ranges[i]
            for w, d in ((self.start, s), (self.end, e)):
                w.blockSignals(True)
                w.setDate(QDate(d.year, d.month, d.day))
                w.blockSignals(False)
            self.compute()

    def _all_time(self) -> tuple[date, date]:
        """From the first booking to the last, imported history included."""
        q = "SELECT MIN(start), MAX(start) FROM bookings" + (" WHERE user_id=?" if self.only_me
                                                              else "")
        lo, hi = self.conn.execute(q, (self.me["id"],) if self.only_me else ()).fetchone()
        today = date.today()
        return (date.fromisoformat(lo[:10]) if lo else today,
                max(date.fromisoformat(hi[:10]), today) if hi else today)

    def _custom(self, *_):
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(self.preset.count() - 1)
        self.preset.blockSignals(False)
        self.compute()

    def _dates(self) -> tuple[date, date]:
        q0, q1 = self.start.date(), self.end.date()
        return date(q0.year(), q0.month(), q0.day()), date(q1.year(), q1.month(), q1.day())

    def compute(self, *_):
        s, e = self._dates()
        if e < s:
            self.summary.setText("The end is before the start.")
            return
        rep = self.rep = reports.build(self.conn, s, e, self.user.currentData() or None,
                                       self.instrument.currentData() or None)
        cur = rep.currency
        self.summary.setText(f"<b>{rep.period()}</b>: {len(rep.lines)} approved bookings, "
                             f"{rep.total_hours:.2f} h, <b>{cur}{rep.total_cost:,.2f}</b>")
        groups = {ln.user: ln.group for ln in rep.lines}
        totals = rep.by_user()
        self.by_user.setRowCount(len(totals))
        for r, t in enumerate(totals):
            _put(self.by_user, r, (t.key, groups.get(t.key, ""), t.bookings, f"{t.hours:.2f}",
                                   f"{cur}{t.cost:,.2f}"), right=(2, 3, 4))
        self.lines.setRowCount(len(rep.lines))
        for r, ln in enumerate(rep.lines):
            _put(self.lines, r, (ln.user, ln.instrument + (f" · {ln.session}" if ln.session
                                                           else ""),
                                 _when(ln.start), _when(ln.end), f"{ln.hours:.2f}",
                                 ln.basis(cur), f"{cur}{ln.cost:,.2f}"), right=(4, 5, 6))
        for t in (self.by_user, self.lines):
            t.resizeColumnsToContents()
        if self.pages:
            self.ana = analytics.build(self.conn, s, e, self.user.currentData() or None,
                                       self.instrument.currentData() or None)
            for page in self.pages:
                page.show_analytics(self.ana)

    def _export(self, kind: str):
        if self.rep is None:
            return
        s, e = self._dates()
        who = ""
        if self.user.currentData():
            u = self.conn.execute("SELECT username FROM users WHERE id=?",
                                  (self.user.currentData(),)).fetchone()
            who = f"-{u['username']}"
        lab = kind.startswith("lab-")
        ext = kind.removeprefix("lab-")
        name = f"{'lab-report' if lab else 'usage'}{who}-{s:%Y%m%d}-{e:%Y%m%d}.{ext}"
        filters = {"pdf": "PDF (*.pdf)", "xlsx": "Excel workbook (*.xlsx)", "csv": "CSV (*.csv)"}
        path, _ = QFileDialog.getSaveFileName(self, "Save report", name, filters[ext])
        if not path:
            return
        data = {"lab-pdf": lambda: analytics.to_pdf(self.ana),
                "lab-xlsx": lambda: analytics.to_xlsx(self.ana),
                "pdf": lambda: reports.to_pdf(self.rep),
                "xlsx": lambda: reports.to_xlsx(self.rep),
                "csv": lambda: reports.to_csv(self.rep).encode("utf-8-sig")}[kind]()
        Path(path).write_bytes(data)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
