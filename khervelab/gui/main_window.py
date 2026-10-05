"""Main window: one schedule tab for all instruments, and one per instrument.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QDialog, QDockWidget, QLabel, QMainWindow,
                             QMessageBox, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from .. import __app_name__, __version__, db, logic
from . import theme
from .admin import InstrumentsDialog, NetworkServer, ServerDialog, SettingsDialog, UsersDialog, \
    swatch
from .calendar import DAY, MONTH, WEEK, CalendarView
from .dialogs import AccountDialog, BookingDetailsDialog, BookingDialog
from .panels import MyBookingsDialog, ReportsDialog, RequestsWidget

REFRESH_MS = 30_000      # bookings made through the web pages appear within this


class ScheduleTab(QWidget):
    def __init__(self, instrument_id: int | None, parent=None):
        super().__init__(parent)
        self.instrument_id = instrument_id
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setContentsMargins(8, 4, 8, 2)
        self.view = CalendarView()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.info)
        lay.addWidget(self.view)


class MainWindow(QMainWindow):
    logoutRequested = pyqtSignal()

    def __init__(self, conn, me, data_dir: Path, settings: QSettings,
                 server: NetworkServer | None = None):
        super().__init__()
        self.conn, self.me, self.data_dir, self.settings = conn, me, data_dir, settings
        self.server = server or NetworkServer(data_dir)
        self.dark = settings.value("dark", False, type=bool)
        self.colours = theme.apply(QApplication.instance(), self.dark)
        self.anchor = date.today()
        self.mode = settings.value("view", WEEK) or WEEK
        self.admin = me["role"] == "admin"
        self.resize(1400, 880)
        self._build()
        self.reload_instruments()
        self.timer = QTimer(self, interval=REFRESH_MS, timeout=self.refresh)
        self.timer.start()

    # -- layout ---------------------------------------------------------------
    def _build(self):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        self.setCentralWidget(self.tabs)

        tb = QToolBar("Schedule")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, shortcut=None, tip=None, checkable=False):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
            a.setCheckable(checkable)
            return a

        tb.addAction(act("◀", lambda: self.step(-1), "Ctrl+Left", "Previous"))
        tb.addAction(act("Today", self.today, "Ctrl+T"))
        tb.addAction(act("▶", lambda: self.step(1), "Ctrl+Right", "Next"))
        self.range_label = QLabel()
        f = self.range_label.font()
        f.setPointSizeF(f.pointSizeF() + 2)
        f.setBold(True)
        self.range_label.setFont(f)
        self.range_label.setContentsMargins(12, 0, 12, 0)
        tb.addWidget(self.range_label)
        tb.addSeparator()
        group = QActionGroup(self)
        self.view_actions = {}
        for key, label, sc in ((DAY, "Day", "Ctrl+1"), (WEEK, "Week", "Ctrl+2"),
                               (MONTH, "Month", "Ctrl+3")):
            a = act(label, lambda _=False, k=key: self.set_mode(k), sc, checkable=True)
            a.setChecked(key == self.mode)
            group.addAction(a)
            tb.addAction(a)
            self.view_actions[key] = a
        tb.addSeparator()
        tb.addAction(act("New booking", self.new_booking, "Ctrl+N"))
        tb.addAction(act("My bookings", self.my_bookings, "Ctrl+B"))

        self.requests = None
        if self.admin:
            self.requests = RequestsWidget(self.conn, self.me)
            self.requests.changed.connect(self.refresh)
            self.requests.countChanged.connect(self._requests_count)
            self.requests_dock = QDockWidget("Waiting for approval", self)
            self.requests_dock.setObjectName("requests")
            self.requests_dock.setWidget(self.requests)
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.requests_dock)
            self.resizeDocks([self.requests_dock], [200], Qt.Orientation.Vertical)
            self.a_requests = self.requests_dock.toggleViewAction()
            self.a_requests.setShortcut(QKeySequence("Ctrl+R"))
            tb.addSeparator()
            tb.addAction(self.a_requests)
            tb.addAction(act("Instruments", self.edit_instruments))
            tb.addAction(act("Users", self.edit_users))
            tb.addAction(act("Reports", self.reports, "Ctrl+E"))
            self.requests.refresh()

        mb = self.menuBar()
        fm = mb.addMenu("&Lab")
        fm.addAction(act("My account…", self.account))
        fm.addAction(act("My statement…", lambda: ReportsDialog(self.conn, self.me, self,
                                                                 only_me=True).exec()))
        fm.addSeparator()
        fm.addAction(act("Switch user / log out", self.logout, "Ctrl+Shift+L"))
        fm.addAction(act("Quit", self.close, "Ctrl+Q"))
        vm = mb.addMenu("&View")
        for a in self.view_actions.values():
            vm.addAction(a)
        vm.addSeparator()
        dark = act("Dark theme", self._dark, checkable=True)
        dark.setChecked(self.dark)
        vm.addAction(dark)
        if self.admin:
            am = mb.addMenu("&Manage")
            am.addAction(self.a_requests)
            am.addAction(act("Instruments and rates…", self.edit_instruments))
            am.addAction(act("Users…", self.edit_users))
            am.addAction(act("Usage and costs…", self.reports))
            am.addAction(act("Lab settings…", self.edit_settings))
            am.addSeparator()
            am.addAction(act("Booking from other computers…", self.network))
        hm = mb.addMenu("&Help")
        hm.addAction(act("About KherveLAB", self.about))

        for key, slot in (("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)),
                          ("T", self.today)):
            s = QShortcut(QKeySequence(key), self.tabs)
            s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            s.activated.connect(slot)

        self.user_label = QLabel()
        self.net_label = QLabel()
        self.statusBar().addPermanentWidget(self.net_label)
        self.statusBar().addPermanentWidget(self.user_label)

    def _title(self):
        lab = db.setting(self.conn, "lab_name")
        self.setWindowTitle(f"{lab} — {__app_name__} v{__version__}")
        role = "lab manager" if self.admin else (self.me["category"] or "user")
        self.user_label.setText(f"  {self.me['full_name']} ({role})  ")
        self.net_label.setText("  Sharing on " + self.server.urls()[-1] + "  "
                               if self.server.running else "")

    # -- tabs -----------------------------------------------------------------
    def instruments(self) -> list:
        return self.conn.execute("SELECT * FROM instruments WHERE active=1 ORDER BY name"
                                 ).fetchall()

    def reload_instruments(self):
        current = self.tabs.currentWidget().instrument_id if self.tabs.count() else None
        self.tabs.blockSignals(True)
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.deleteLater()
        all_tab = self._make_tab(None)
        self.tabs.addTab(all_tab, "All instruments")
        for i in self.instruments():
            t = self._make_tab(i["id"])
            idx = self.tabs.addTab(t, swatch(i["colour"]), i["name"])
            if i["id"] == current:
                self.tabs.setCurrentIndex(idx)
        self.tabs.blockSignals(False)
        self.refresh()

    def _make_tab(self, iid: int | None) -> ScheduleTab:
        t = ScheduleTab(iid)
        t.view.createRequested.connect(self.create_booking)
        t.view.moveRequested.connect(self.move_booking)
        t.view.openRequested.connect(self.open_booking)
        t.view.dayActivated.connect(self.go_to_day)
        return t

    def refresh(self):
        self.me = self.conn.execute("SELECT * FROM users WHERE id=?", (self.me["id"],)).fetchone()
        self._title()
        tab: ScheduleTab | None = self.tabs.currentWidget()
        if tab is None:
            return
        if tab.instrument_id is None:
            insts = self.instruments()
            tab.info.setText("Every instrument. Day view shows them side by side; drag in a "
                             "column to book." if insts else
                             "No instruments yet." + (" Add them under Manage ▸ Instruments."
                                                      if self.admin else ""))
            tab.view.set_state(self.conn, self.me, insts, False, self.anchor, self.mode,
                               self.colours)
        else:
            inst = self.conn.execute("SELECT * FROM instruments WHERE id=?",
                                     (tab.instrument_id,)).fetchone()
            tab.info.setText(self._info(inst))
            tab.view.set_state(self.conn, self.me, [inst], True, self.anchor, self.mode,
                               self.colours)
        first, last = tab.view.visible_range()
        self.range_label.setText(f"{first:%A %d %B %Y}" if self.mode == DAY else
                                 f"{first:%d %b} – {last:%d %b %Y}" if self.mode == WEEK else
                                 f"{first:%B %Y}")
        if self.requests is not None:
            self.requests.refresh()

    def _info(self, inst) -> str:
        cur = db.setting(self.conn, "currency")
        rate = logic.rate_for(self.conn, inst["id"], self.me["category"])
        trained = logic.is_authorised(self.conn, self.me["id"], inst["id"])
        instant = self.admin or inst["approval"] == "auto" or (inst["approval"] == "trained"
                                                               and trained)
        hours = ("around the clock" if inst["open_time"] == "00:00" and
                 inst["close_time"] in ("24:00", "23:59") else
                 f"{inst['open_time']}–{inst['close_time']}")
        approval = ("books at once" if instant else
                    "<span style='color:#9a6700'>bookings need approval</span>")
        head = [f"<b>{html.escape(inst['name'])}</b>",
                html.escape(inst["description"]) if inst["description"] else "", approval]
        if inst["booking_mode"] == "sessions":
            parts = []
            for r in logic.sessions(self.conn, inst["id"]):
                price = logic.session_price(self.conn, r["id"], self.me["category"])
                h = logic.session_hours(r["start_time"], r["end_time"])
                cost = (f"{cur}{price:,.2f}" if price is not None else
                        f"{cur}{h * rate:,.2f} ({cur}{rate:,.2f}/h)")
                parts.append(f"{html.escape(r['name'])} {r['start_time']}–{r['end_time']} "
                             f"{logic.days_label(r['days'])}: {cost}")
            return " · ".join(b for b in head if b) + "<br>Sessions — " + \
                ("; ".join(parts) if parts else "none defined yet")
        bits = head + [f"{cur}{rate:,.2f}/h for you",
                       f"{hours}, {'every day' if inst['weekends'] else 'weekdays'}",
                       f"{logic.fmt_duration(inst['min_minutes'])}–"
                       f"{logic.fmt_duration(inst['max_minutes'])} in "
                       f"{logic.fmt_duration(inst['slot_minutes'])} slots"]
        return " · ".join(b for b in bits if b)

    # -- navigation -------------------------------------------------------------
    def step(self, direction: int):
        if self.mode == DAY:
            self.anchor += timedelta(days=direction)
        elif self.mode == WEEK:
            self.anchor += timedelta(days=7 * direction)
        else:
            m = self.anchor.month - 1 + direction
            self.anchor = date(self.anchor.year + m // 12, m % 12 + 1, 1)
        self.refresh()

    def today(self):
        self.anchor = date.today()
        self.refresh()

    def set_mode(self, mode: str):
        self.mode = mode
        self.view_actions[mode].setChecked(True)
        self.settings.setValue("view", mode)
        self.refresh()

    def go_to_day(self, d: date):
        self.anchor = d
        self.set_mode(DAY)

    # -- bookings -----------------------------------------------------------------
    def new_booking(self):
        tab = self.tabs.currentWidget()
        insts = self.instruments()
        if not insts:
            return
        iid = tab.instrument_id or insts[0]["id"]
        now = logic.now_local(self.conn)
        start = now.replace(minute=0) + timedelta(hours=1)
        if self.anchor != now.date():
            start = datetime(self.anchor.year, self.anchor.month, self.anchor.day, 9)
        self.create_booking(iid, start, start + timedelta(hours=1))

    def create_booking(self, iid: int, start: datetime, end: datetime):
        inst = self.conn.execute("SELECT * FROM instruments WHERE id=?", (iid,)).fetchone()
        if inst is not None and inst["booking_mode"] == "free" and \
                (end - start) < timedelta(minutes=inst["min_minutes"]):
            end = start + timedelta(minutes=inst["min_minutes"])
        dlg = BookingDialog(self.conn, self.me, iid, start, end, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            n = len(dlg.result_ids)
            what = f"{n} sessions" if n > 1 else "Booking"
            if "pending" in dlg.result_statuses:
                self.statusBar().showMessage(f"{what} requested: shown dashed until the lab "
                                             "manager approves.", 8000)
            else:
                self.statusBar().showMessage(f"{what} booked.", 5000)
        self.refresh()

    def move_booking(self, bid: int, start: datetime, end: datetime, iid: int):
        inst = self.conn.execute("SELECT * FROM instruments WHERE id=?", (iid,)).fetchone()
        if inst["booking_mode"] == "sessions":
            # snap to the session the dragged booking now sits in (its middle)
            mid = start + (end - start) / 2
            occ = [o for o in logic.occurrences(self.conn, iid, mid, mid + timedelta(minutes=1))
                   if o.start <= mid < o.end]
            if occ:
                start, end = occ[0].start, occ[0].end
            elif not self.admin:
                QMessageBox.warning(self, "Not moved", "Drop the booking onto one of the "
                                    f"{inst['name']} sessions.")
                self.refresh()
                return
        try:
            status = logic.reschedule(self.conn, bid, self.me, start, end, iid)
            if status == "pending":
                self.statusBar().showMessage("Moved; the change needs the lab manager's "
                                             "approval.", 8000)
        except logic.BookingError as exc:
            QMessageBox.warning(self, "Not moved", str(exc))
        self.refresh()

    def open_booking(self, bid: int):
        dlg = BookingDetailsDialog(self.conn, self.me, bid, self)
        dlg.exec()
        if dlg.changed:
            self.refresh()

    def my_bookings(self):
        dlg = MyBookingsDialog(self.conn, self.me, self)
        dlg.exec()
        self.refresh()

    def _requests_count(self, n: int):
        self.a_requests.setText(f"Requests ({n})" if n else "Requests")

    # -- management -------------------------------------------------------------
    def edit_instruments(self):
        dlg = InstrumentsDialog(self.conn, self)
        dlg.exec()
        self.reload_instruments()

    def edit_users(self):
        UsersDialog(self.conn, self.me, self).exec()
        self.refresh()

    def reports(self):
        ReportsDialog(self.conn, self.me, self).exec()

    def edit_settings(self):
        if SettingsDialog(self.conn, self.data_dir, self).exec() == QDialog.DialogCode.Accepted:
            self.reload_instruments()

    def network(self):
        ServerDialog(self.server, self).exec()
        self._title()

    def account(self):
        if AccountDialog(self.conn, self.me, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def logout(self):
        self.timer.stop()
        self.logoutRequested.emit()

    def _dark(self, on: bool):
        self.dark = on
        self.settings.setValue("dark", on)
        self.colours = theme.apply(QApplication.instance(), on)
        self.refresh()

    def about(self):
        QMessageBox.about(self, "About KherveLAB",
                          f"<b>{__app_name__} v{__version__}</b><br>Instrument booking for one "
                          "lab.<br><br>Lab database:<br>"
                          f"<code>{html.escape(str(self.data_dir / 'lab.db'))}</code><br><br>"
                          "© 2026 Gwilherm Kerherve — GPL v3")
