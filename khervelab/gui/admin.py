"""Lab manager windows: instruments and rates, users, settings, network server.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTime
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                             QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                             QRadioButton, QScrollArea, QSpinBox, QSplitter, QTableWidget,
                             QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget)

from .. import db, logic


def swatch(colour: str) -> QIcon:
    pm = QPixmap(12, 12)
    pm.fill(QColor(colour))
    return QIcon(pm)


def _time(hhmm: str) -> QTime:
    h, m = (int(x) for x in hhmm.split(":"))
    return QTime(min(h, 23), m if h < 24 else 59)


class InstrumentsDialog(QDialog):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.changed = False
        self.current: int | None = None
        self.setWindowTitle("Instruments and rates")
        self.resize(940, 680)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load)
        add = QPushButton("Add instrument")
        add.clicked.connect(self._add)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.list)
        ll.addWidget(add)

        self.name, self.location = QLineEdit(), QLineEdit()
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(54)
        self.colour = "#1f6feb"
        self.colour_btn = QPushButton()
        self.colour_btn.clicked.connect(self._pick_colour)
        self.active = QCheckBox("Available for booking")
        basics = QFormLayout()
        basics.addRow("Name", self.name)
        basics.addRow("Description", self.description)
        basics.addRow("Location", self.location)
        basics.addRow("Colour", self.colour_btn)
        basics.addRow("", self.active)

        appr = QGroupBox("Approval")
        al = QVBoxLayout(appr)
        self.approval = QButtonGroup(self)
        self.approval_btns = {}
        for key, label in logic.APPROVAL_LABELS.items():
            rb = QRadioButton(label)
            self.approval.addButton(rb)
            self.approval_btns[key] = rb
            al.addWidget(rb)

        cur = db.setting(conn, "currency")
        rates = QGroupBox(f"Hourly rate ({cur}/h) by user category")
        self.rate_form = QFormLayout(rates)
        self.rates: dict[str, QDoubleSpinBox] = {}
        for c in db.categories(conn):
            sb = QDoubleSpinBox()
            sb.setRange(0, 100000)
            sb.setDecimals(2)
            sb.setPrefix(cur)
            self.rates[c] = sb
            self.rate_form.addRow(c, sb)

        rules = QGroupBox("Booking rules (the lab manager is exempt)")
        rg = QGridLayout(rules)
        self.slot, self.min, self.max, self.ahead = (QSpinBox() for _ in range(4))
        for w, lo, hi, suf in ((self.slot, 5, 240, " min"), (self.min, 1, 100000, " min"),
                               (self.max, 1, 100000, " min"), (self.ahead, 1, 3650, " days")):
            w.setRange(lo, hi)
            w.setSuffix(suf)
        self.open_t, self.close_t = QTimeEdit(), QTimeEdit()
        for w in (self.open_t, self.close_t):
            w.setDisplayFormat("HH:mm")
        self.all_day = QCheckBox("Around the clock (overnight runs allowed)")
        self.all_day.toggled.connect(lambda on: (self.open_t.setEnabled(not on),
                                                 self.close_t.setEnabled(not on)))
        self.weekends = QCheckBox("Bookable at weekends")
        for r, (label, w) in enumerate((("Slot", self.slot), ("Shortest", self.min),
                                        ("Longest", self.max), ("Book ahead up to", self.ahead))):
            rg.addWidget(QLabel(label), r // 2, (r % 2) * 2)
            rg.addWidget(w, r // 2, (r % 2) * 2 + 1)
        rg.addWidget(QLabel("Opens"), 2, 0)
        rg.addWidget(self.open_t, 2, 1)
        rg.addWidget(QLabel("Closes"), 2, 2)
        rg.addWidget(self.close_t, 2, 3)
        rg.addWidget(self.all_day, 3, 0, 1, 4)
        rg.addWidget(self.weekends, 4, 0, 1, 4)

        trained = QGroupBox("Trained users (book at once with 'automatic for trained users')")
        tl = QVBoxLayout(trained)
        self.trained = QListWidget()
        self.trained.setMinimumHeight(110)
        tl.addWidget(self.trained)

        form = QWidget()
        fl = QVBoxLayout(form)
        fl.addLayout(basics)
        fl.addWidget(appr)
        fl.addWidget(rates)
        fl.addWidget(rules)
        fl.addWidget(trained)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(scroll)
        split.setSizes([240, 700])
        save = QPushButton("Save instrument")
        save.clicked.connect(self._save)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(save)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        lay.addLayout(row)
        self._fill()

    def _fill(self, select: int | None = None):
        self.list.blockSignals(True)
        self.list.clear()
        for i in self.conn.execute("SELECT * FROM instruments ORDER BY active DESC, name"):
            it = QListWidgetItem(swatch(i["colour"]), i["name"] + ("" if i["active"]
                                                                    else "  (unavailable)"))
            it.setData(Qt.ItemDataRole.UserRole, i["id"])
            self.list.addItem(it)
            if i["id"] == select:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self._load(self.list.currentItem())

    def _set_colour(self, colour: str):
        self.colour = colour
        self.colour_btn.setIcon(swatch(colour))
        self.colour_btn.setText(colour)

    def _pick_colour(self):
        c = QColorDialog.getColor(QColor(self.colour), self, "Instrument colour")
        if c.isValid():
            self._set_colour(c.name())

    def _load(self, item, _prev=None):
        if item is None:
            self.current = None
            return
        i = self.conn.execute("SELECT * FROM instruments WHERE id=?",
                              (item.data(Qt.ItemDataRole.UserRole),)).fetchone()
        self.current = i["id"]
        self.name.setText(i["name"])
        self.description.setPlainText(i["description"])
        self.location.setText(i["location"])
        self._set_colour(i["colour"])
        self.active.setChecked(bool(i["active"]))
        self.approval_btns[i["approval"]].setChecked(True)
        for c, sb in self.rates.items():
            sb.setValue(logic.rate_for(self.conn, i["id"], c))
        self.slot.setValue(i["slot_minutes"])
        self.min.setValue(i["min_minutes"])
        self.max.setValue(i["max_minutes"])
        self.ahead.setValue(i["max_days_ahead"])
        all_day = i["open_time"] == "00:00" and i["close_time"] in ("24:00", "23:59")
        self.all_day.setChecked(all_day)
        self.open_t.setTime(_time("08:00" if all_day else i["open_time"]))
        self.close_t.setTime(_time("20:00" if all_day else i["close_time"]))
        self.weekends.setChecked(bool(i["weekends"]))
        trained = {r[0] for r in self.conn.execute(
            "SELECT user_id FROM authorised WHERE instrument_id=?", (i["id"],))}
        self.trained.clear()
        for u in self.conn.execute("SELECT * FROM users WHERE status='active' ORDER BY full_name"):
            it = QListWidgetItem(f"{u['full_name']}  ({u['group_name'] or u['username']})")
            it.setData(Qt.ItemDataRole.UserRole, u["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if u["id"] in trained
                             else Qt.CheckState.Unchecked)
            self.trained.addItem(it)

    def _add(self):
        iid = logic.add_instrument(self.conn, "New instrument", "", "manual", "#1f6feb", "08:00",
                                   "20:00", 0, 480)
        self.changed = True
        self._fill(iid)
        self.name.setFocus()
        self.name.selectAll()

    def _save(self):
        if self.current is None:
            return
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Instrument", "Give the instrument a name.")
            return
        if 1440 % self.slot.value():
            QMessageBox.warning(self, "Instrument", "The slot must divide a day "
                                "(e.g. 15, 30, 60 min).")
            return
        if self.min.value() > self.max.value():
            QMessageBox.warning(self, "Instrument", "The shortest booking is longer than the "
                                "longest.")
            return
        if self.all_day.isChecked():
            o, c = "00:00", "24:00"
        else:
            o, c = self.open_t.time().toString("HH:mm"), self.close_t.time().toString("HH:mm")
            if o >= c:
                QMessageBox.warning(self, "Instrument", "Closing must be after opening.")
                return
        approval = next(k for k, b in self.approval_btns.items() if b.isChecked())
        self.conn.execute(
            "UPDATE instruments SET name=?, description=?, location=?, colour=?, active=?, "
            "approval=?, slot_minutes=?, min_minutes=?, max_minutes=?, max_days_ahead=?, "
            "open_time=?, close_time=?, weekends=? WHERE id=?",
            (name, self.description.toPlainText().strip(), self.location.text().strip(),
             self.colour, int(self.active.isChecked()), approval, self.slot.value(),
             self.min.value(), self.max.value(), self.ahead.value(), o, c,
             int(self.weekends.isChecked()), self.current))
        for cat, sb in self.rates.items():
            self.conn.execute("INSERT INTO rates (instrument_id, category, rate) VALUES (?,?,?) "
                              "ON CONFLICT(instrument_id, category) DO UPDATE SET rate=excluded.rate",
                              (self.current, cat, sb.value()))
        self.conn.execute("DELETE FROM authorised WHERE instrument_id=?", (self.current,))
        for r in range(self.trained.count()):
            it = self.trained.item(r)
            if it.checkState() == Qt.CheckState.Checked:
                self.conn.execute("INSERT INTO authorised (user_id, instrument_id) VALUES (?,?)",
                                  (it.data(Qt.ItemDataRole.UserRole), self.current))
        self.changed = True
        self._fill(self.current)


class UsersDialog(QDialog):
    def __init__(self, conn, me, parent=None):
        super().__init__(parent)
        self.conn, self.me = conn, me
        self.changed = False
        self.current: int | None = None
        self.setWindowTitle("Users")
        self.resize(980, 600)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Username", "Group", "Category", "Role",
                                              "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.itemSelectionChanged.connect(self._load)
        self.full_name, self.email, self.group = QLineEdit(), QLineEdit(), QLineEdit()
        self.category, self.role, self.status = QComboBox(), QComboBox(), QComboBox()
        self.category.addItems(db.categories(conn))
        self.role.addItem("User", "user")
        self.role.addItem("Lab manager", "admin")
        self.status.addItems(["pending", "active", "disabled"])
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("leave empty to keep")
        self.trained = QListWidget()
        form = QFormLayout()
        for label, w in (("Full name", self.full_name), ("Email", self.email),
                         ("Group / supervisor", self.group), ("Rate category", self.category),
                         ("Role", self.role), ("Status", self.status),
                         ("New password", self.password)):
            form.addRow(label, w)
        form.addRow(QLabel("<b>Trained on</b>"))
        form.addRow(self.trained)
        save = QPushButton("Save user")
        save.clicked.connect(self._save)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addLayout(form)
        rl.addWidget(save)
        split = QSplitter()
        split.addWidget(self.table)
        split.addWidget(right)
        split.setSizes([560, 420])
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self._fill()

    def _fill(self, select: int | None = None):
        rows = self.conn.execute("SELECT * FROM users ORDER BY status='pending' DESC, full_name"
                                 ).fetchall()
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for r, u in enumerate(rows):
            for c, v in enumerate((u["full_name"], u["username"], u["group_name"], u["category"],
                                   "manager" if u["role"] == "admin" else "user", u["status"])):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, u["id"])
                if c == 5 and v == "pending":
                    it.setForeground(QColor("#9a6700"))
                self.table.setItem(r, c, it)
            if u["id"] == select:
                self.table.selectRow(r)
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)
        if select is None and rows:
            self.table.selectRow(0)
        self._load()

    def _load(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        uid = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        u = self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        self.current = uid
        self.full_name.setText(u["full_name"])
        self.email.setText(u["email"])
        self.group.setText(u["group_name"])
        self.category.setCurrentText(u["category"])
        self.role.setCurrentIndex(self.role.findData(u["role"]))
        self.status.setCurrentText(u["status"])
        self.password.clear()
        trained = {r[0] for r in self.conn.execute(
            "SELECT instrument_id FROM authorised WHERE user_id=?", (uid,))}
        self.trained.clear()
        for i in self.conn.execute("SELECT * FROM instruments ORDER BY name"):
            it = QListWidgetItem(swatch(i["colour"]), i["name"])
            it.setData(Qt.ItemDataRole.UserRole, i["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if i["id"] in trained
                             else Qt.CheckState.Unchecked)
            self.trained.addItem(it)

    def _save(self):
        uid = self.current
        if uid is None:
            return
        role, status = self.role.currentData(), self.status.currentText()
        if uid == self.me["id"] and (role != "admin" or status != "active"):
            QMessageBox.warning(self, "Users", "You cannot remove your own lab manager access.")
            return
        if self.password.text():
            try:
                logic.set_password(self.conn, uid, self.password.text())
            except ValueError as exc:
                QMessageBox.warning(self, "Users", str(exc))
                return
        self.conn.execute("UPDATE users SET full_name=?, email=?, group_name=?, category=?, "
                          "role=?, status=? WHERE id=?",
                          (self.full_name.text().strip(), self.email.text().strip(),
                           self.group.text().strip(), self.category.currentText(), role, status,
                           uid))
        self.conn.execute("DELETE FROM authorised WHERE user_id=?", (uid,))
        for r in range(self.trained.count()):
            it = self.trained.item(r)
            if it.checkState() == Qt.CheckState.Checked:
                self.conn.execute("INSERT INTO authorised (user_id, instrument_id) VALUES (?,?)",
                                  (uid, it.data(Qt.ItemDataRole.UserRole)))
        self.changed = True
        self._fill(uid)


class SettingsDialog(QDialog):
    def __init__(self, conn, data_dir: Path, parent=None):
        super().__init__(parent)
        self.conn, self.data_dir = conn, data_dir
        self.setWindowTitle("Lab settings")
        self.setMinimumWidth(500)
        s = lambda k: db.setting(conn, k)  # noqa: E731
        self.lab, self.currency, self.tz = QLineEdit(s("lab_name")), QLineEdit(s("currency")), \
            QLineEdit(s("timezone"))
        self.categories = QPlainTextEdit(s("categories"))
        self.categories.setFixedHeight(80)
        self.account_approval = QCheckBox("New accounts wait for my approval")
        self.account_approval.setChecked(s("account_approval") == "1")
        self.show_names = QCheckBox("Users see who booked each slot")
        self.show_names.setChecked(s("show_names") == "1")
        form = QFormLayout()
        form.addRow("Lab name", self.lab)
        form.addRow("Currency symbol", self.currency)
        form.addRow("Time zone", self.tz)
        form.addRow("Rate categories\n(one per line)", self.categories)
        form.addRow("", self.account_approval)
        form.addRow("", self.show_names)
        backup = QPushButton("Back up the lab database…")
        backup.clicked.connect(self._backup)
        where = QLabel(f"Data: <code>{data_dir / 'lab.db'}</code>")
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(where)
        lay.addWidget(backup)
        lay.addWidget(bb)

    def _backup(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Back up", f"khervelab-backup-{datetime.now():%Y%m%d-%H%M}.db",
            "SQLite database (*.db)")
        if path:
            db.backup(self.conn, path)
            QMessageBox.information(self, "Back up", f"Saved {path}.")

    def _ok(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        tzname = self.tz.text().strip() or "Europe/London"
        try:
            ZoneInfo(tzname)
        except (ZoneInfoNotFoundError, ValueError):
            QMessageBox.warning(self, "Settings", f"Unknown time zone {tzname}.")
            return
        cats = [c.strip() for c in self.categories.toPlainText().splitlines() if c.strip()]
        if not cats:
            QMessageBox.warning(self, "Settings", "Keep at least one rate category.")
            return
        db.set_setting(self.conn, "lab_name", self.lab.text().strip() or "My lab")
        db.set_setting(self.conn, "currency", self.currency.text().strip() or "£")
        db.set_setting(self.conn, "timezone", tzname)
        db.set_setting(self.conn, "categories", "\n".join(cats))
        db.set_setting(self.conn, "account_approval",
                       "1" if self.account_approval.isChecked() else "0")
        db.set_setting(self.conn, "show_names", "1" if self.show_names.isChecked() else "0")
        self.accept()


class NetworkServer:
    """The optional web interface, served from this app on the same database,
    so people can also book from their own computer's browser."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.server = None
        self.thread: threading.Thread | None = None
        self.port = 0
        self.host = ""

    @property
    def running(self) -> bool:
        return self.server is not None

    def start(self, port: int, everyone: bool) -> None:
        # werkzeug's threaded server stops cleanly from another thread
        # (shutdown), which waitress does not; the headless --serve mode
        # still uses waitress
        from werkzeug.serving import make_server
        from ..web import create_app
        self.host = "0.0.0.0" if everyone else "127.0.0.1"
        self.server = make_server(self.host, port, create_app(self.data_dir), threaded=True)
        self.port = port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def urls(self) -> list[str]:
        from ..server import lan_address
        out = [f"http://localhost:{self.port}"]
        if self.host == "0.0.0.0":
            out.append(f"http://{lan_address()}:{self.port}")
        return out


class ServerDialog(QDialog):
    def __init__(self, server: NetworkServer, parent=None):
        super().__init__(parent)
        self.server = server
        self.setWindowTitle("Booking from other computers")
        self.setMinimumWidth(480)
        intro = QLabel("KherveLAB can also serve its booking pages on the lab network, so "
                       "people can book from their own computer's browser. They log in with the "
                       "same accounts, and everything lands in this app's calendar.")
        intro.setWordWrap(True)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(server.port or 8080)
        self.everyone = QCheckBox("Allow other computers on the network")
        self.everyone.setChecked(True)
        self.status = QLabel()
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setWordWrap(True)
        self.toggle = QPushButton()
        self.toggle.clicked.connect(self._toggle)
        form = QFormLayout()
        form.addRow("Port", self.port)
        form.addRow("", self.everyone)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(self.toggle)
        row.addStretch(1)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(self.status)
        lay.addLayout(row)
        self._update()

    def _update(self):
        on = self.server.running
        self.toggle.setText("Stop sharing" if on else "Start sharing")
        self.port.setEnabled(not on)
        self.everyone.setEnabled(not on)
        if on:
            links = "<br>".join(f"<b>{u}</b>" for u in self.server.urls())
            self.status.setText(f"Running. Give people this address:<br>{links}")
        else:
            self.status.setText("Not running: bookings are made in this app only.")

    def _toggle(self):
        if self.server.running:
            self.server.stop()
        else:
            try:
                self.server.start(self.port.value(), self.everyone.isChecked())
            except OSError as exc:
                QMessageBox.warning(self, "Network", f"Could not start on port "
                                    f"{self.port.value()}: {exc}")
        self._update()
