"""Setup, login, account and booking dialogs.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import QDateTime, Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDateTimeEdit, QDialog,
                             QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                             QPushButton, QVBoxLayout, QWidget)

from .. import db, logic


def qdt(dt: datetime) -> QDateTime:
    return QDateTime(dt.year, dt.month, dt.day, dt.hour, dt.minute)


def pdt(q: QDateTime) -> datetime:
    d, t = q.date(), q.time()
    return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())


def fill_user_combo(combo: QComboBox, conn, select: int | None) -> None:
    """Active users, searchable by typing part of a name or username."""
    combo.clear()
    for u in conn.execute("SELECT * FROM users WHERE status='active' ORDER BY full_name"):
        label = f"{u['full_name']} ({u['username']})" + (f" · {u['group_name']}"
                                                          if u["group_name"] else "")
        combo.addItem(label, u["id"])
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    comp = combo.completer()
    comp.setFilterMode(Qt.MatchFlag.MatchContains)
    comp.setCompletionMode(comp.CompletionMode.PopupCompletion)
    if select is not None:
        combo.setCurrentIndex(max(0, combo.findData(select)))


def _password(placeholder: str = "") -> QLineEdit:
    w = QLineEdit()
    w.setEchoMode(QLineEdit.EchoMode.Password)
    w.setPlaceholderText(placeholder)
    return w


def _buttons(dialog: QDialog, ok_text: str = "OK") -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                          | QDialogButtonBox.StandardButton.Cancel)
    bb.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
    bb.rejected.connect(dialog.reject)
    return bb


class SetupDialog(QDialog):
    """First run: name the lab and create the lab manager's account."""

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.user_id: int | None = None
        self.setWindowTitle("Set up your lab")
        self.setMinimumWidth(520)
        self.lab = QLineEdit()
        self.lab.setPlaceholderText("e.g. Advanced Photoelectron Spectroscopy Lab")
        self.currency = QLineEdit("£")
        self.full_name, self.email, self.username = QLineEdit(), QLineEdit(), QLineEdit()
        self.pw1, self.pw2 = _password("8+ characters"), _password()
        form = QFormLayout()
        form.addRow("Lab name", self.lab)
        form.addRow("Currency symbol", self.currency)
        form.addRow(QLabel("<b>Lab manager (administrator)</b>"))
        form.addRow("Full name", self.full_name)
        form.addRow("Email", self.email)
        form.addRow("Username", self.username)
        form.addRow("Password", self.pw1)
        form.addRow("Repeat password", self.pw2)
        box = QGroupBox("Start with some instruments (you can rename, edit or remove them)")
        bl = QVBoxLayout(box)
        self.examples = []
        for ex in logic.EXAMPLES:
            cb = QCheckBox(f"{ex[0]} — {ex[1]}")
            bl.addWidget(cb)
            self.examples.append((cb, ex))
        bb = _buttons(self, "Create the lab")
        bb.accepted.connect(self._ok)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Welcome to KherveLAB. Create the lab manager's account; everyone "
                             "else creates their own account from the login window."))
        lay.addLayout(form)
        lay.addWidget(box)
        lay.addWidget(bb)

    def _ok(self):
        if self.pw1.text() != self.pw2.text():
            QMessageBox.warning(self, "Set up", "The passwords differ.")
            return
        try:
            uid = logic.create_user(self.conn, self.username.text(), self.pw1.text(),
                                    self.full_name.text(), self.email.text(), role="admin",
                                    status="active")
        except ValueError as exc:
            QMessageBox.warning(self, "Set up", str(exc))
            return
        db.set_setting(self.conn, "lab_name", self.lab.text().strip() or "My lab")
        db.set_setting(self.conn, "currency", self.currency.text().strip() or "£")
        for cb, ex in self.examples:
            if cb.isChecked():
                logic.add_instrument(self.conn, *ex)
        self.user_id = uid
        self.accept()


class LoginDialog(QDialog):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.user = None
        self.setWindowTitle(f"{db.setting(conn, 'lab_name')} — log in")
        self.setMinimumWidth(380)
        title = QLabel(f"<h2 style='margin:0'>{db.setting(conn, 'lab_name')}</h2>"
                       "<p>Log in to book instrument time.</p>")
        self.username = QLineEdit()
        self.password = _password()
        form = QFormLayout()
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        register = QPushButton("Create an account…")
        register.clicked.connect(self._register)
        bb = _buttons(self, "Log in")
        bb.accepted.connect(self._ok)
        row = QHBoxLayout()
        row.addWidget(register)
        row.addStretch(1)
        row.addWidget(bb)
        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addLayout(form)
        lay.addLayout(row)
        self.username.setFocus()

    def _ok(self):
        u = logic.authenticate(self.conn, self.username.text(), self.password.text())
        if u is None:
            QMessageBox.warning(self, "Log in", "Wrong username or password.")
            self.password.clear()
            return
        if u["status"] == "pending":
            QMessageBox.information(self, "Log in", "Your account is waiting for the lab "
                                    "manager's approval.")
            return
        if u["status"] == "disabled":
            QMessageBox.warning(self, "Log in", "Your account is disabled; contact the lab "
                                "manager.")
            return
        self.user = u
        self.accept()

    def _register(self):
        dlg = RegisterDialog(self.conn, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.username.setText(dlg.username.text())
            self.password.setFocus()


class RegisterDialog(QDialog):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Create an account")
        self.setMinimumWidth(440)
        self.full_name, self.email, self.group = QLineEdit(), QLineEdit(), QLineEdit()
        self.category = QComboBox()
        self.category.addItems(db.categories(conn))
        self.username = QLineEdit()
        self.pw1, self.pw2 = _password("8+ characters"), _password()
        form = QFormLayout()
        for label, w in (("Full name", self.full_name), ("Email", self.email),
                         ("Group / supervisor", self.group), ("Category", self.category),
                         ("Username", self.username), ("Password", self.pw1),
                         ("Repeat password", self.pw2)):
            form.addRow(label, w)
        bb = _buttons(self, "Create account")
        bb.accepted.connect(self._ok)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _ok(self):
        if self.pw1.text() != self.pw2.text():
            QMessageBox.warning(self, "Create an account", "The passwords differ.")
            return
        try:
            logic.create_user(self.conn, self.username.text(), self.pw1.text(),
                              self.full_name.text(), self.email.text(), self.group.text(),
                              self.category.currentText())
        except ValueError as exc:
            QMessageBox.warning(self, "Create an account", str(exc))
            return
        if db.setting(self.conn, "account_approval") == "1":
            QMessageBox.information(self, "Create an account", "Account created. The lab "
                                    "manager will approve it; you can then log in.")
        self.accept()


class AccountDialog(QDialog):
    def __init__(self, conn, me, parent=None):
        super().__init__(parent)
        self.conn, self.me = conn, me
        self.setWindowTitle("My account")
        self.full_name = QLineEdit(me["full_name"])
        self.email = QLineEdit(me["email"])
        self.group = QLineEdit(me["group_name"])
        self.current, self.pw1, self.pw2 = _password(), _password("leave empty to keep"), \
            _password()
        form = QFormLayout()
        form.addRow("Username", QLabel(me["username"]))
        form.addRow("Rate category", QLabel(me["category"] or "—"))
        form.addRow("Full name", self.full_name)
        form.addRow("Email", self.email)
        form.addRow("Group / supervisor", self.group)
        form.addRow(QLabel("<b>Change password</b>"))
        form.addRow("Current password", self.current)
        form.addRow("New password", self.pw1)
        form.addRow("Repeat new password", self.pw2)
        bb = _buttons(self, "Save")
        bb.accepted.connect(self._ok)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _ok(self):
        if self.pw1.text():
            if not logic.authenticate(self.conn, self.me["username"], self.current.text()):
                QMessageBox.warning(self, "My account", "The current password is wrong.")
                return
            if self.pw1.text() != self.pw2.text():
                QMessageBox.warning(self, "My account", "The new passwords differ.")
                return
            try:
                logic.set_password(self.conn, self.me["id"], self.pw1.text())
            except ValueError as exc:
                QMessageBox.warning(self, "My account", str(exc))
                return
        self.conn.execute("UPDATE users SET full_name=?, email=?, group_name=? WHERE id=?",
                          (self.full_name.text().strip() or self.me["full_name"],
                           self.email.text().strip(), self.group.text().strip(), self.me["id"]))
        self.accept()


class BookingDialog(QDialog):
    """New booking: rules, approval and cost are shown before saving. A
    session-booked instrument lists its sessions instead of free times."""

    def __init__(self, conn, me, instrument_id: int, start: datetime, end: datetime,
                 parent=None):
        super().__init__(parent)
        self.conn, self.me = conn, me
        self.wanted = (start, end)           # what was dragged: preselects sessions
        self.result_id: int | None = None
        self.result_status = ""
        self.result_ids: list[int] = []
        self.result_statuses: list[str] = []
        self.setWindowTitle("Book instrument time")
        self.setMinimumWidth(520)
        self.instrument = QComboBox()
        for i in conn.execute("SELECT * FROM instruments WHERE active=1 OR id=? ORDER BY name",
                              (instrument_id,)):
            self.instrument.addItem(i["name"], i["id"])
        self.instrument.setCurrentIndex(max(0, self.instrument.findData(instrument_id)))
        self.user = QComboBox()
        self.for_others = logic.acts_for_others(me)
        if self.for_others:          # the manager or a super user books on someone's behalf
            fill_user_combo(self.user, conn, me["id"])
        self.start, self.end = QDateTimeEdit(qdt(start)), QDateTimeEdit(qdt(end))
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("ddd d MMM yyyy  HH:mm")
        self.day0, self.day1 = QDateEdit(start.date()), QDateEdit((end - timedelta(minutes=1))
                                                                  .date())
        for w in (self.day0, self.day1):
            w.setCalendarPopup(True)
            w.setDisplayFormat("ddd d MMM yyyy")
        self.session_list = QListWidget()
        self.session_list.setMinimumHeight(150)
        self.purpose = QPlainTextEdit()
        self.purpose.setPlaceholderText("What you will measure, samples")
        self.purpose.setFixedHeight(70)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.form = QFormLayout()
        self.form.addRow("Instrument", self.instrument)
        if self.for_others:
            self.form.addRow("For", self.user)
        self.form.addRow("Start", self.start)
        self.form.addRow("End", self.end)
        days = QHBoxLayout()
        days.addWidget(self.day0)
        days.addWidget(QLabel("to"))
        days.addWidget(self.day1)
        self.days_row = QWidget()
        self.days_row.setLayout(days)
        days.setContentsMargins(0, 0, 0, 0)
        self.form.addRow("Sessions from", self.days_row)
        self.form.addRow("Sessions", self.session_list)
        self.form.addRow("Purpose", self.purpose)
        self.bb = _buttons(self, "Book")
        self.bb.accepted.connect(self._ok)
        lay = QVBoxLayout(self)
        lay.addLayout(self.form)
        lay.addWidget(self.info)
        lay.addWidget(self.bb)
        for sig in (self.instrument.currentIndexChanged, self.user.currentIndexChanged,
                    self.day0.dateChanged, self.day1.dateChanged):
            sig.connect(self._rebuild)
        for sig in (self.start.dateTimeChanged, self.end.dateTimeChanged):
            sig.connect(self._update)
        self.session_list.itemChanged.connect(self._update)
        self._rebuild()

    # -- helpers ----------------------------------------------------------------
    def _inst(self):
        return self.conn.execute("SELECT * FROM instruments WHERE id=?",
                                 (self.instrument.currentData(),)).fetchone()

    def _who(self):
        uid = self.user.currentData() if self.for_others else self.me["id"]
        return self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    def sessions_mode(self) -> bool:
        return self._inst()["booking_mode"] == "sessions"

    def _show(self, widget, visible: bool):
        widget.setVisible(visible)
        label = self.form.labelForField(widget)
        if label is not None:
            label.setVisible(visible)

    def _instant(self, inst, who) -> bool:
        return logic.instant_for(self.conn, inst, who, self.me)

    def _rules_for(self, who):
        """The manager's rights for the manager; a super user books as the person."""
        return self.me if self.me["role"] == "admin" else who

    def _cost_text(self, inst, who, s: datetime, e: datetime, session_id: int | None) -> tuple:
        cur = db.setting(self.conn, "currency")
        price = logic.session_price(self.conn, session_id, who["category"]) if session_id else None
        if price is not None:
            return price, f"{cur}{price:,.2f}"
        rate = logic.rate_for(self.conn, inst["id"], who["category"])
        h = logic.hours(self.conn, s, e) if e > s else 0
        return h * rate, f"{h:g} h × {cur}{rate:,.2f}/h = {cur}{h * rate:,.2f}"

    # -- building -----------------------------------------------------------------
    def _rebuild(self, *_):
        sessions = self.sessions_mode()
        for w in (self.start, self.end):
            self._show(w, not sessions)
        self._show(self.days_row, sessions)
        self._show(self.session_list, sessions)
        if sessions:
            self._fill_sessions()
        self._update()

    def _fill_sessions(self):
        inst, who = self._inst(), self._who()
        q0, q1 = self.day0.date(), self.day1.date()
        d0 = datetime(q0.year(), q0.month(), q0.day())
        d1 = datetime(q1.year(), q1.month(), q1.day()) + timedelta(days=1)
        w0, w1 = self.wanted
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for o in logic.occurrences(self.conn, inst["id"], d0, d1):
            if o.start < d0:            # last night's run belongs to the day before
                continue
            errors = logic.check(self.conn, inst, self._rules_for(who), o.start, o.end)
            _, cost = self._cost_text(inst, who, o.start, o.end, o.session_id)
            end = f"{o.end:%H:%M}" + ("" if o.end.date() == o.start.date() else
                                      f" ({o.end:%a})")
            text = f"{o.start:%a %d %b}  ·  {o.name}  {o.start:%H:%M}–{end}  ·  {cost}"
            it = QListWidgetItem(text + (f"  —  {errors[0]}" if errors else ""))
            it.setData(Qt.ItemDataRole.UserRole, (o.start, o.end, o.session_id))
            if errors:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                it.setCheckState(Qt.CheckState.Unchecked)
            else:
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                hit = o.start < w1 and o.end > w0
                it.setCheckState(Qt.CheckState.Checked if hit else Qt.CheckState.Unchecked)
            self.session_list.addItem(it)
        if not self.session_list.count():
            it = QListWidgetItem("No sessions on these days.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.session_list.addItem(it)
        self.session_list.blockSignals(False)

    def chosen(self) -> list[tuple[datetime, datetime, int]]:
        out = []
        for r in range(self.session_list.count()):
            it = self.session_list.item(r)
            if it.checkState() == Qt.CheckState.Checked and it.data(Qt.ItemDataRole.UserRole):
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def _update(self, *_):
        inst, who = self._inst(), self._who()
        instant = self._instant(inst, who)
        approval = ("Approved at once." if instant else
                    "<span style='color:#9a6700'>Needs the lab manager's approval.</span>")
        cur = db.setting(self.conn, "currency")
        ok = self.bb.button(QDialogButtonBox.StandardButton.Ok)
        if self.sessions_mode():
            picks = self.chosen()
            total = sum(self._cost_text(inst, who, s, e, sid)[0] for s, e, sid in picks)
            self.info.setText(f"{len(picks)} session(s) · total <b>{cur}{total:,.2f}</b><br>"
                              + approval)
            ok.setEnabled(bool(picks))
        else:
            s, e = pdt(self.start.dateTime()), pdt(self.end.dateTime())
            # the manager's own rights decide the rules; the booker's category the rate
            errors = logic.check(self.conn, inst, self._rules_for(who), s, e)
            _, cost = self._cost_text(inst, who, s, e, None)
            lines = [f"<b>{cost}</b>", approval]
            lines += [f"<span style='color:#cf222e'>• {x}</span>" for x in errors]
            self.info.setText("<br>".join(lines))
            ok.setEnabled(not errors)
        ok.setText("Book" if instant else "Request approval")

    def _ok(self):
        who = self._who()
        iid = self.instrument.currentData()
        ranges = ([(s, e) for s, e, _ in self.chosen()] if self.sessions_mode() else
                  [(pdt(self.start.dateTime()), pdt(self.end.dateTime()))])
        problems = []
        for s, e in ranges:
            try:
                res = logic.book(self.conn, iid, who["id"], s, e, self.purpose.toPlainText(),
                                 actor_id=self.me["id"])
                self.result_ids.append(res.id)
                self.result_statuses.append(res.status)
            except logic.BookingError as exc:
                problems.append(f"{s:%a %d %b %H:%M}: {exc}")
        if problems:
            QMessageBox.warning(self, "Not booked", "\n".join(problems))
        if not self.result_ids:
            return
        self.result_id, self.result_status = self.result_ids[0], self.result_statuses[0]
        self.accept()


class BookingDetailsDialog(QDialog):
    """View a booking; its owner (before it starts) or the manager can change
    or cancel it, and the manager can approve or reject it."""

    def __init__(self, conn, me, booking_id: int, parent=None):
        super().__init__(parent)
        self.conn, self.me = conn, me
        self.changed = False
        b = conn.execute("SELECT b.*, u.full_name, u.group_name, u.category, i.name AS instrument "
                         "FROM bookings b JOIN users u ON u.id=b.user_id JOIN instruments i "
                         "ON i.id=b.instrument_id WHERE b.id=?", (booking_id,)).fetchone()
        self.b = b
        admin = me["role"] == "admin"
        mine = b["user_id"] == me["id"]
        self.for_others = logic.acts_for_others(me)
        show = self.for_others or mine or db.setting(conn, "show_names") == "1"
        now = logic.fmt(logic.now_local(conn))
        self.editable = (admin or ((mine or self.for_others) and b["start"] > now)) and \
            b["status"] in ("pending", "approved")
        self.setWindowTitle(f"{b['instrument']} booking")
        self.setMinimumWidth(460)
        cur = db.setting(conn, "currency")
        form = QFormLayout()
        form.addRow("Instrument", QLabel(f"<b>{b['instrument']}</b>"))
        self.owner = None
        if self.for_others and self.editable:          # give it to someone else
            self.owner = QComboBox()
            fill_user_combo(self.owner, conn, b["user_id"])
            form.addRow("Booked for", self.owner)
        else:
            form.addRow("Booked by", QLabel((b["full_name"] + (f" ({b['group_name']})"
                                                                if b["group_name"] else ""))
                                            if show else "another user"))
        form.addRow("Status", QLabel(b["status"] + (f" — {b['note']}" if b["note"] else "")))
        self.start = QDateTimeEdit(qdt(logic.parse(b["start"])))
        self.end = QDateTimeEdit(qdt(logic.parse(b["end"])))
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("ddd d MMM yyyy  HH:mm")
            w.setEnabled(self.editable)
        session = conn.execute("SELECT * FROM sessions WHERE id=?",
                               (b["session_id"],)).fetchone() if b["session_id"] else None
        if session is not None:
            # a session booking moves by dragging onto another session; its
            # times are the session's, so only the manager may type new ones
            form.addRow("Session", QLabel(f"{session['name']} "
                                          f"({session['start_time']}–{session['end_time']})"))
            if not admin:
                self.start.setEnabled(False)
                self.end.setEnabled(False)
        form.addRow("Start", self.start)
        form.addRow("End", self.end)
        private = self.for_others or mine
        self.purpose = QPlainTextEdit(b["purpose"] if private else "")
        self.purpose.setFixedHeight(60)
        self.purpose.setEnabled(self.editable)
        if private:
            form.addRow("Purpose", self.purpose)
            form.addRow("Cost", QLabel(f"{cur}{logic.cost(conn, b):,.2f} "
                                       f"({logic.price_label(conn, b)}, {b['category']})"))
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        row = QHBoxLayout()
        if self.editable:
            save = QPushButton("Save changes")
            save.clicked.connect(self._save)
            cancel = QPushButton("Cancel booking…")
            cancel.clicked.connect(self._cancel)
            row.addWidget(save)
            row.addWidget(cancel)
        if admin and b["status"] == "pending":
            ok = QPushButton("Approve")
            ok.clicked.connect(lambda: self._decide(True))
            no = QPushButton("Reject…")
            no.clicked.connect(lambda: self._decide(False))
            row.addWidget(ok)
            row.addWidget(no)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def _save(self):
        try:
            if self.owner is not None and self.owner.currentData() != self.b["user_id"]:
                logic.reassign(self.conn, self.b["id"], self.me, self.owner.currentData())
            status = logic.reschedule(self.conn, self.b["id"], self.me, pdt(self.start.dateTime()),
                                      pdt(self.end.dateTime()),
                                      purpose=self.purpose.toPlainText())
        except logic.BookingError as exc:
            QMessageBox.warning(self, "Not changed", str(exc))
            return
        if status == "pending" and self.b["status"] == "approved":
            QMessageBox.information(self, "Changed", "The change needs the lab manager's "
                                    "approval again.")
        self.changed = True
        self.accept()

    def _cancel(self):
        if QMessageBox.question(self, "Cancel booking", "Cancel this booking?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            logic.cancel(self.conn, self.b["id"], self.me)
        except logic.BookingError as exc:
            QMessageBox.warning(self, "Cancel booking", str(exc))
            return
        self.changed = True
        self.accept()

    def _decide(self, approve: bool):
        note = ""
        if not approve:
            from PyQt6.QtWidgets import QInputDialog
            note, ok = QInputDialog.getText(self, "Reject", "Reason (shown to the user):")
            if not ok:
                return
        logic.decide(self.conn, self.b["id"], self.me["id"], approve, note)
        self.changed = True
        self.accept()


class IssueDialog(QDialog):
    """Report a problem or an accident with an instrument."""

    def __init__(self, conn, me, instrument_id: int | None = None, parent=None):
        super().__init__(parent)
        self.conn, self.me = conn, me
        self.issue_id: int | None = None
        self.setWindowTitle("Report a problem")
        self.setMinimumWidth(460)
        self.instrument = QComboBox()
        for i in conn.execute("SELECT * FROM instruments ORDER BY name"):
            self.instrument.addItem(i["name"], i["id"])
        if instrument_id:
            self.instrument.setCurrentIndex(max(0, self.instrument.findData(instrument_id)))
        self.kind = QComboBox()
        for key, label in logic.ISSUE_LABELS.items():
            self.kind.addItem(label, key)
        now = logic.now_local(conn)
        self.start = QDateTimeEdit(qdt(now))
        self.end = QDateTimeEdit(qdt(now + timedelta(days=1)))
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("ddd d MMM yyyy  HH:mm")
        self.until_fixed = QCheckBox("Until it is fixed (the lab manager marks it resolved)")
        self.until_fixed.setChecked(True)
        self.until_fixed.toggled.connect(lambda on: self.end.setEnabled(not on))
        self.end.setEnabled(False)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("What happened, e.g. 'X-ray source tripped', 'vacuum leak'")
        self.note.setFixedHeight(70)
        form = QFormLayout()
        form.addRow("Instrument", self.instrument)
        form.addRow("What", self.kind)
        form.addRow("From", self.start)
        form.addRow("", self.until_fixed)
        form.addRow("Until", self.end)
        form.addRow("Details", self.note)
        info = QLabel("<i>Out of order</i> stops new bookings for that time and shows the slots "
                      "red; a <i>problem</i> shows them yellow and lets people book with care.")
        info.setWordWrap(True)
        bb = _buttons(self, "Report")
        bb.accepted.connect(self._ok)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(info)
        lay.addWidget(bb)

    def _ok(self):
        end = None if self.until_fixed.isChecked() else pdt(self.end.dateTime())
        try:
            self.issue_id = logic.report_issue(self.conn, self.instrument.currentData(),
                                               self.kind.currentData(), pdt(self.start.dateTime()),
                                               end, self.note.toPlainText(), self.me["id"])
        except ValueError as exc:
            QMessageBox.warning(self, "Report a problem", str(exc))
            return
        self.accept()
