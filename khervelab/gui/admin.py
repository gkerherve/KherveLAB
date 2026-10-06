"""Lab manager windows: instruments and rates, users, settings, network server.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
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


class DurationEdit(QWidget):
    """A length entered in minutes or hours, always stored in minutes."""

    def __init__(self, max_minutes: int = 1440, parent=None):
        super().__init__(parent)
        self.max_minutes = max_minutes
        self.value_box = QDoubleSpinBox()
        self.unit = QComboBox()
        self.unit.addItem("min", 1)
        self.unit.addItem("h", 60)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.value_box, 1)
        lay.addWidget(self.unit)
        self.unit.currentIndexChanged.connect(self._unit_changed)
        self._factor = 1
        self._apply_limits()

    def _apply_limits(self):
        f = self.unit.currentData()
        self.value_box.setDecimals(0 if f == 1 else 2)
        self.value_box.setSingleStep(5 if f == 1 else 0.5)
        self.value_box.setRange(1 / f, self.max_minutes / f)

    def _unit_changed(self, *_):
        minutes = self.value_box.value() * self._factor
        self._factor = self.unit.currentData()
        self._apply_limits()
        self.value_box.setValue(minutes / self._factor)

    def minutes(self) -> int:
        return int(round(self.value_box.value() * self.unit.currentData()))

    def setMinutes(self, minutes: int):
        hours = minutes >= 60 and minutes % 15 == 0
        self.unit.blockSignals(True)
        self.unit.setCurrentIndex(1 if hours else 0)
        self.unit.blockSignals(False)
        self._factor = self.unit.currentData()
        self._apply_limits()
        self.value_box.setValue(minutes / self._factor)


class DaysPicker(QWidget):
    """Seven toggles, Monday first."""

    def __init__(self, days: str = "01234", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(1)
        self.boxes = []
        for n, name in enumerate(logic.DAY_NAMES):
            cb = QCheckBox(name[:2])
            cb.setChecked(str(n) in days)
            lay.addWidget(cb)
            self.boxes.append(cb)

    def days(self) -> str:
        return "".join(str(n) for n, cb in enumerate(self.boxes) if cb.isChecked())


class SessionsEditor(QWidget):
    """Table of an instrument's sessions with a fixed price per user category;
    an empty price means that category pays the hourly rate."""

    FIXED = 4   # name, starts, ends, days; then one price column per category

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.cats = db.categories(conn)
        cur = db.setting(conn, "currency")
        self.table = QTableWidget(0, self.FIXED + len(self.cats))
        self.table.setHorizontalHeaderLabels(["Session", "Starts", "Ends", "Days"]
                                             + [f"{c} ({cur})" for c in self.cats])
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(150)
        self.template = QComboBox()
        self.template.addItem("Start from a template…")
        for name in logic.SESSION_TEMPLATES:
            self.template.addItem(name)
        self.template.activated.connect(self._template)
        add, rem = QPushButton("Add session"), QPushButton("Remove selected")
        add.clicked.connect(lambda: self.add_row("New session", "08:00", "12:00", "01234", {}))
        rem.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        row.addWidget(self.template)
        hint = QLabel("Times are 24 h (HH:MM). An end at or before the start runs into the next "
                      "day, e.g. 17:00 → 08:00. Leave a price empty to charge that category the "
                      "hourly rate.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #57606a;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        lay.addLayout(row)
        lay.addWidget(hint)

    def load(self, instrument_id: int):
        self.table.setRowCount(0)
        for r in logic.sessions(self.conn, instrument_id):
            prices = {c: logic.session_price(self.conn, r["id"], c) for c in self.cats}
            self.add_row(r["name"], r["start_time"], r["end_time"], r["days"], prices, r["id"])

    def add_row(self, name, start, end, days, prices, sid=None):
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(name)
        it.setData(Qt.ItemDataRole.UserRole, sid)
        self.table.setItem(r, 0, it)
        self.table.setItem(r, 1, QTableWidgetItem(start))
        self.table.setItem(r, 2, QTableWidgetItem(end))
        self.table.setCellWidget(r, 3, DaysPicker(days))
        for n, c in enumerate(self.cats):
            p = prices.get(c)
            self.table.setItem(r, self.FIXED + n, QTableWidgetItem("" if p is None else f"{p:.2f}"))
        self.table.resizeColumnsToContents()

    def _remove(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def _template(self, index: int):
        if index <= 0:
            return
        name = self.template.itemText(index)
        if self.table.rowCount() and QMessageBox.question(
                self, "Sessions", f"Replace the sessions with “{name}”?") \
                != QMessageBox.StandardButton.Yes:
            self.template.setCurrentIndex(0)
            return
        self.table.setRowCount(0)
        for n, st, en, days in logic.SESSION_TEMPLATES[name]:
            self.add_row(n, st, en, days, {})
        self.template.setCurrentIndex(0)

    def specs(self) -> list[logic.SessionSpec]:
        out = []
        for r in range(self.table.rowCount()):
            def text(c):
                it = self.table.item(r, c)
                return it.text().strip() if it else ""
            prices = {}
            for n, c in enumerate(self.cats):
                t = text(self.FIXED + n).replace(",", "")
                try:
                    prices[c] = float(t) if t else None
                except ValueError:
                    raise ValueError(f"row {r + 1}: price {t!r} for {c} is not a number") \
                        from None
            start, end = text(1), text(2)
            for t in (start, end):
                if not _valid_time(t):
                    raise ValueError(f"row {r + 1}: {t!r} is not a time like 08:00")
            out.append(logic.SessionSpec(text(0), _norm_time(start), _norm_time(end),
                                         self.table.cellWidget(r, 3).days(), prices,
                                         self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)))
        return out


def _valid_time(t: str) -> bool:
    try:
        h, m = t.split(":")
        return 0 <= int(h) < 24 and 0 <= int(m) < 60
    except ValueError:
        return False


def _norm_time(t: str) -> str:
    h, m = t.split(":")
    return f"{int(h):02d}:{int(m):02d}"


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
        rates = QGroupBox(f"Hourly rate ({cur}/h) by user category (sessions without a fixed "
                          "price use it too)")
        self.rate_form = QFormLayout(rates)
        self.rates: dict[str, QDoubleSpinBox] = {}
        for c in db.categories(conn):
            sb = QDoubleSpinBox()
            sb.setRange(0, 100000)
            sb.setDecimals(2)
            sb.setPrefix(cur)
            self.rates[c] = sb
            self.rate_form.addRow(c, sb)

        mode = QGroupBox("How it is booked")
        ml = QVBoxLayout(mode)
        self.mode_free = QRadioButton("Free time: people choose start and end, in slots")
        self.mode_sessions = QRadioButton("Fixed sessions: people book whole sessions "
                                          "(e.g. morning, afternoon, overnight)")
        self.mode_group = QButtonGroup(self)
        for rb in (self.mode_free, self.mode_sessions):
            self.mode_group.addButton(rb)
            ml.addWidget(rb)
        self.sessions = SessionsEditor(conn)
        ml.addWidget(self.sessions)
        self.mode_sessions.toggled.connect(self._mode_changed)

        rules = QGroupBox("When and how it is booked (the lab manager is exempt)")
        rg = QGridLayout(rules)
        # slot and shortest up to a day; longest can span days on around-the-clock
        # instruments (e.g. a 72 h thermal run)
        self.slot, self.min = DurationEdit(1440), DurationEdit(1440)
        self.max = DurationEdit(60 * 24 * 30)
        self.ahead = QSpinBox()
        self.ahead.setRange(1, 3650)
        self.ahead.setSuffix(" days")

        def time_edit():
            w = QTimeEdit()
            w.setDisplayFormat("HH:mm")
            return w
        self.open_t, self.close_t = time_edit(), time_edit()
        self.all_day = QCheckBox("Around the clock")
        self.all_day.toggled.connect(self._periods_changed)
        self.period = {}
        for kind in ("evening", "weekend"):
            how = QComboBox()
            for key, label in logic.PERIOD_MODES.items():
                how.addItem(label, key)
            how.currentIndexChanged.connect(self._periods_changed)
            # a whole weekend (Sat -> Mon) can be 48 h or more, so its slot may be too
            self.period[kind] = {"mode": how, "start": time_edit(), "end": time_edit(),
                                 "slot": DurationEdit(1440 if kind == "evening" else 72 * 60)}
        self.weekend_span = QComboBox()
        self.weekend_span.addItem("Each day: Saturday and Sunday separately", "daily")
        self.weekend_span.addItem("Whole weekend: from Saturday to Monday", "whole")
        self.weekend_span.currentIndexChanged.connect(self._periods_changed)
        for kind in ("weekend",):
            self.period[kind]["start"].timeChanged.connect(self._periods_changed)
            self.period[kind]["end"].timeChanged.connect(self._periods_changed)
        for c, head in enumerate(("", "From", "To", "Booked as", "Slot")):
            lab = QLabel(f"<b>{head}</b>")
            rg.addWidget(lab, 0, c)
        rows = (("Daytime (Mon–Fri)", self.open_t, self.close_t, self.all_day, self.slot),
                ("Evening (Mon–Fri)", self.period["evening"]["start"],
                 self.period["evening"]["end"], self.period["evening"]["mode"],
                 self.period["evening"]["slot"]),
                ("Weekend", self.period["weekend"]["start"],
                 self.period["weekend"]["end"], self.period["weekend"]["mode"],
                 self.period["weekend"]["slot"]))
        for r, (label, w1, w2, w3, w4) in enumerate(rows, start=1):
            rg.addWidget(QLabel(label), r, 0)
            for c, w in enumerate((w1, w2, w3, w4), start=1):
                rg.addWidget(w, r, c)
        limits = QHBoxLayout()
        for label, w in (("Daytime shortest", self.min), ("longest", self.max),
                         ("Book ahead up to", self.ahead)):
            limits.addWidget(QLabel(label))
            limits.addWidget(w, 1)
        span_row = QHBoxLayout()
        span_row.addWidget(QLabel("Weekend runs"))
        span_row.addWidget(self.weekend_span, 1)
        self.weekend_span_note = QLabel()
        self.weekend_span_note.setStyleSheet("color: #57606a;")
        span_row.addWidget(self.weekend_span_note, 1)
        rg.addLayout(span_row, 4, 0, 1, 5)
        rg.addLayout(limits, 6, 0, 1, 5)
        hint = QLabel("An end at or before the start runs into the next day (an evening of "
                      "17:00 → 08:00). Slots are counted from the start of each period, and a "
                      "booking is a whole number of slots: a 4.5 h daytime slot from 08:00 gives "
                      "08:00, 12:30 and 17:00. “One booking” takes the whole evening or weekend "
                      "day at once. Shortest and longest apply to daytime bookings.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #57606a;")
        rg.addWidget(hint, 7, 0, 1, 5)

        trained = QGroupBox("Trained users (book at once with 'automatic for trained users')")
        tl = QVBoxLayout(trained)
        self.trained = QListWidget()
        self.trained.setMinimumHeight(110)
        tl.addWidget(self.trained)

        form = QWidget()
        fl = QVBoxLayout(form)
        fl.addLayout(basics)
        fl.addWidget(appr)
        fl.addWidget(mode)
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
        self.slot.setMinutes(i["slot_minutes"])
        self.min.setMinutes(i["min_minutes"])
        self.max.setMinutes(i["max_minutes"])
        self.ahead.setValue(i["max_days_ahead"])
        all_day = i["open_time"] == "00:00" and i["close_time"] in ("24:00", "23:59")
        self.all_day.setChecked(all_day)
        self.open_t.setTime(_time("08:00" if all_day else i["open_time"]))
        self.close_t.setTime(_time("17:00" if all_day else i["close_time"]))
        for kind, w in self.period.items():
            w["mode"].setCurrentIndex(max(0, w["mode"].findData(i[f"{kind}_mode"])))
            w["start"].setTime(_time(i[f"{kind}_start"]))
            w["end"].setTime(_time(i[f"{kind}_end"]))
            w["slot"].setMinutes(i[f"{kind}_slot"])
        self.weekend_span.setCurrentIndex(max(0, self.weekend_span.findData(i["weekend_span"])))
        (self.mode_sessions if i["booking_mode"] == "sessions" else self.mode_free).setChecked(True)
        self.sessions.load(i["id"])
        self._mode_changed()
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

    def _mode_changed(self, *_):
        sessions = self.mode_sessions.isChecked()
        self.sessions.setVisible(sessions)
        # in session mode the sessions fix the times; only "book ahead" still applies
        for w in (self.slot, self.min, self.max, self.all_day):
            w.setEnabled(not sessions)
        self._periods_changed()

    def _periods_changed(self, *_):
        free = not self.mode_sessions.isChecked()
        around = self.all_day.isChecked()
        self.open_t.setEnabled(free and not around)
        self.close_t.setEnabled(free and not around)
        for kind, w in self.period.items():
            # an around-the-clock daytime leaves no weekday evening to book
            usable = free and not (kind == "evening" and around)
            mode = w["mode"].currentData()
            w["mode"].setEnabled(usable)
            w["start"].setEnabled(usable and mode != "closed")
            w["end"].setEnabled(usable and mode != "closed")
            w["slot"].setEnabled(usable and mode == "own")
        we = self.period["weekend"]
        open_weekend = free and we["mode"].currentData() != "closed"
        self.weekend_span.setEnabled(open_weekend)
        if self.weekend_span.currentData() == "whole":
            st, en = we["start"].time(), we["end"].time()
            hours = 48 + (st.secsTo(en) / 3600)
            self.weekend_span_note.setText(
                f"Sat {st.toString('HH:mm')} → Mon {en.toString('HH:mm')} = {hours:g} h")
        else:
            self.weekend_span_note.setText("an end at or before the start is the next morning")

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
        slot, shortest, longest = self.slot.minutes(), self.min.minutes(), self.max.minutes()
        if shortest > longest:
            QMessageBox.warning(self, "Instrument", "The shortest booking is longer than the "
                                "longest.")
            return
        if self.all_day.isChecked():
            o, c = "00:00", "24:00"
        else:
            o, c = self.open_t.time().toString("HH:mm"), self.close_t.time().toString("HH:mm")
            if o >= c:
                QMessageBox.warning(self, "Instrument", "The daytime must end after it starts "
                                    "(use the evening for time past midnight).")
                return
        per = {}
        for kind, w in self.period.items():
            mode = w["mode"].currentData()
            if kind == "evening" and self.all_day.isChecked():
                mode = "closed"
            st, en = w["start"].time().toString("HH:mm"), w["end"].time().toString("HH:mm")
            if mode != "closed" and st == en and kind == "evening":
                QMessageBox.warning(self, "Instrument", "The evening needs different start and "
                                    "end times.")
                return
            per[kind] = (mode, st, en, w["slot"].minutes())
        approval = next(k for k, b in self.approval_btns.items() if b.isChecked())
        booking_mode = "sessions" if self.mode_sessions.isChecked() else "free"
        try:
            specs = self.sessions.specs()
            if booking_mode == "sessions" and not specs:
                raise ValueError("Add at least one session, or book this instrument by free time.")
            logic.save_sessions(self.conn, self.current, specs)
        except ValueError as exc:
            QMessageBox.warning(self, "Sessions", str(exc))
            return
        self.conn.execute(
            "UPDATE instruments SET name=?, description=?, location=?, colour=?, active=?, "
            "approval=?, slot_minutes=?, min_minutes=?, max_minutes=?, max_days_ahead=?, "
            "open_time=?, close_time=?, weekends=?, booking_mode=?, evening_mode=?, "
            "evening_start=?, evening_end=?, evening_slot=?, weekend_mode=?, weekend_start=?, "
            "weekend_end=?, weekend_slot=?, weekend_span=? WHERE id=?",
            (name, self.description.toPlainText().strip(), self.location.text().strip(),
             self.colour, int(self.active.isChecked()), approval, slot, shortest, longest,
             self.ahead.value(), o, c,
             int(per["weekend"][0] != "closed"), booking_mode, *per["evening"], *per["weekend"],
             self.weekend_span.currentData(),
             self.current))
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
        self.colour_buttons = {}
        colours = QHBoxLayout()
        for key, label in (("free", "Free slot"), ("booked", "Booked"),
                           ("problem", "Problem"), ("down", "Out of order")):
            b = QPushButton(label)
            self._paint_button(b, s(f"colour_{key}"))
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            self.colour_buttons[key] = b
            colours.addWidget(b)
        form.addRow("Calendar colours", colours)
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

    @staticmethod
    def _paint_button(b: QPushButton, colour: str):
        b.setProperty("colour", colour)
        b.setIcon(swatch(colour))

    def _pick(self, key: str):
        b = self.colour_buttons[key]
        c = QColorDialog.getColor(QColor(b.property("colour")), self, b.text())
        if c.isValid():
            self._paint_button(b, c.name())

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
        for key, b in self.colour_buttons.items():
            db.set_setting(self.conn, f"colour_{key}", b.property("colour"))
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


class IssuesDialog(QDialog):
    """Open and recent problems and out-of-order periods; the lab manager
    resolves them (fixed now) or removes mistaken reports."""

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.changed = False
        self.setWindowTitle("Problems and out of order")
        self.resize(900, 420)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Instrument", "What", "From", "Until", "Details",
                                              "Reported by"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        fixed = QPushButton("Fixed now")
        fixed.clicked.connect(self._resolve)
        remove = QPushButton("Remove report")
        remove.clicked.connect(self._remove)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(fixed)
        row.addWidget(remove)
        row.addStretch(1)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table)
        lay.addLayout(row)
        self.fill()

    def fill(self):
        since = logic.fmt(logic.now_local(self.conn) - timedelta(days=30))
        rows = self.conn.execute(
            "SELECT s.*, i.name AS instrument, u.full_name FROM issues s JOIN instruments i "
            "ON i.id=s.instrument_id LEFT JOIN users u ON u.id=s.reported_by "
            "WHERE s.end IS NULL OR s.end >= ? ORDER BY s.end IS NOT NULL, s.start DESC",
            (since,)).fetchall()
        self.table.setRowCount(len(rows))
        for r, x in enumerate(rows):
            what = "Out of order" if x["kind"] == "down" else "Problem"
            vals = (x["instrument"], what, x["start"].replace("T", " "),
                    x["end"].replace("T", " ") if x["end"] else "until fixed", x["note"],
                    x["full_name"] or "")
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, x["id"])
                if c == 1:
                    it.setForeground(QColor(db.setting(self.conn, "colour_down" if x["kind"] ==
                                                       "down" else "colour_problem")))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()

    def _selected(self) -> list[int]:
        return [self.table.item(i.row(), 0).data(Qt.ItemDataRole.UserRole)
                for i in self.table.selectionModel().selectedRows()]

    def _resolve(self):
        for iid in self._selected():
            logic.resolve_issue(self.conn, iid)
        self.changed = True
        self.fill()

    def _remove(self):
        ids = self._selected()
        if ids and QMessageBox.question(self, "Remove", "Remove the selected report(s)? Use "
                                        "this for mistakes; use Fixed now when a fault is "
                                        "repaired.") == QMessageBox.StandardButton.Yes:
            for iid in ids:
                self.conn.execute("DELETE FROM issues WHERE id=?", (iid,))
            self.changed = True
            self.fill()
