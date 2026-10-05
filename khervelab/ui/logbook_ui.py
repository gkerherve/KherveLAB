"""Logbook quick entry, fault updates and the instrument dashboard.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import html
from datetime import date, datetime, timezone

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                             QVBoxLayout, QWidget)

from ..core import logbook
from ..core.facility import FacilityService

TYPE_LABELS = {
    "usage": "Usage (unbooked hours)", "fault": "Fault", "repair": "Repair",
    "calibration": "Calibration", "consumable_change": "Consumable change",
    "bake_out": "Bake-out", "maintenance": "Maintenance done", "correction": "Counter correction",
    "note": "Note",
}


class QuickLogDialog(QDialog):
    """One keystroke away (Ctrl+L): if logging a fault takes more than ten
    seconds nobody will do it."""

    def __init__(self, svc: FacilityService, instrument: str | None, user: str, parent=None,
                 kind: str = "note"):
        super().__init__(parent)
        self.svc, self.user = svc, user
        self.setWindowTitle("Log entry")
        self.setMinimumWidth(440)
        self.instrument = QComboBox()
        for inst in sorted(svc.instruments.values(), key=lambda i: i.name):
            self.instrument.addItem(inst.name, inst.id)
        self.instrument.setCurrentIndex(max(0, self.instrument.findData(instrument)))
        self.type = QComboBox()
        for k, v in TYPE_LABELS.items():
            self.type.addItem(v, k)
        self.type.setCurrentIndex(max(0, self.type.findData(kind)))
        self.text = QLineEdit()
        self.text.setPlaceholderText("What happened (no names or emails)")
        self.hours = QDoubleSpinBox()
        self.hours.setRange(-10000, 10000)
        self.hours.setDecimals(2)
        self.hours.setSuffix(" h")
        self.consumable = QComboBox()
        self.task = QComboBox()
        self.task.setEditable(True)
        self.blocking = QCheckBox("Blocks bookings until resolved")
        self.back = QDateEdit(QDate.currentDate().addDays(3))
        self.back.setCalendarPopup(True)
        self.has_back = QCheckBox("Expected back")
        row = QHBoxLayout()
        row.addWidget(self.has_back)
        row.addWidget(self.back, 1)

        self.form = QFormLayout(self)
        self.form.addRow("Instrument", self.instrument)
        self.form.addRow("Type", self.type)
        self.form.addRow("Text", self.text)
        self.form.addRow("Hours", self.hours)
        self.form.addRow("Consumable", self.consumable)
        self.form.addRow("Task", self.task)
        self.form.addRow("", self.blocking)
        self.form.addRow("", row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        self.form.addRow(bb)
        self.instrument.currentIndexChanged.connect(self._update)
        self.type.currentIndexChanged.connect(self._update)
        self._update()
        self.text.setFocus()
        self.entry: logbook.LogEntry | None = None

    def _update(self):
        inst = self.svc.instruments[self.instrument.currentData()]
        kind = self.type.currentData()
        self.consumable.clear()
        self.consumable.addItems([c.name for c in inst.consumables])
        current = self.task.currentText()
        self.task.clear()
        self.task.addItems([m.task for m in inst.maintenance])
        if current:
            self.task.setEditText(current)
        show = {
            "Hours": kind in ("usage", "correction"),
            "Consumable": kind in ("consumable_change", "correction"),
            "Task": kind in logbook.MAINTENANCE_TYPES,
        }
        for label, visible in show.items():
            for w in (self.form.labelForField(self._field(label)), self._field(label)):
                if w is not None:
                    w.setVisible(visible)
        is_fault = kind == "fault"
        self.blocking.setVisible(is_fault)
        self.has_back.setVisible(is_fault)
        self.back.setVisible(is_fault)

    def _field(self, label: str) -> QWidget:
        return {"Hours": self.hours, "Consumable": self.consumable, "Task": self.task}[label]

    def _ok(self):
        kind = self.type.currentData()
        kw = {}
        if kind in ("usage", "correction"):
            if not self.hours.value():
                QMessageBox.warning(self, "Log entry", "Enter the hours.")
                return
            kw["hours"] = self.hours.value()
        if kind in ("consumable_change", "correction"):
            if not self.consumable.currentText():
                QMessageBox.warning(self, "Log entry", "This instrument has no consumables.")
                return
            kw["consumable"] = self.consumable.currentText()
        if kind in logbook.MAINTENANCE_TYPES and self.task.currentText().strip():
            kw["task"] = self.task.currentText().strip()
        if kind == "fault":
            if not self.text.text().strip():
                QMessageBox.warning(self, "Log entry", "Describe the fault.")
                return
            kw["blocking"] = self.blocking.isChecked()
            if self.has_back.isChecked():
                q = self.back.date()
                kw["back"] = date(q.year(), q.month(), q.day())
        try:
            self.entry = self.svc.log(self.instrument.currentData(), kind, self.text.text().strip(),
                                      user=self.user, **kw)
        except Exception as exc:
            QMessageBox.warning(self, "Log entry", str(exc))
            return
        self.accept()


class FaultDialog(QDialog):
    def __init__(self, svc: FacilityService, fault: logbook.Fault, user: str, parent=None):
        super().__init__(parent)
        self.svc, self.fault, self.user = svc, fault, user
        self.setWindowTitle("Update fault")
        inst = svc.instruments.get(fault.instrument)
        self.status = QComboBox()
        self.status.addItems(logbook.FAULT_STATES)
        self.status.setCurrentText(fault.status)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Progress, or the resolution when resolving")
        self.blocking = QCheckBox("Blocks bookings")
        self.blocking.setChecked(fault.blocking)
        self.back = QDateEdit(QDate(fault.back.year, fault.back.month, fault.back.day)
                              if fault.back else QDate.currentDate().addDays(3))
        self.back.setCalendarPopup(True)
        self.has_back = QCheckBox("Expected back")
        self.has_back.setChecked(fault.back is not None)
        form = QFormLayout(self)
        hist = "<br>".join(f"{e.time.astimezone(svc.tz):%d %b %H:%M} — {html.escape(e.type)}"
                           f"{' (' + e.status + ')' if e.status else ''}: {html.escape(e.text)}"
                           for e in fault.history)
        lab = QLabel(f"<b>{html.escape(inst.name if inst else fault.instrument)}</b>: "
                     f"{html.escape(fault.text)}<br><small>{hist}</small>")
        lab.setWordWrap(True)
        form.addRow(lab)
        form.addRow("Status", self.status)
        form.addRow("Note", self.text)
        form.addRow("", self.blocking)
        row = QHBoxLayout()
        row.addWidget(self.has_back)
        row.addWidget(self.back, 1)
        form.addRow("", row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _ok(self):
        back = None
        if self.has_back.isChecked():
            q = self.back.date()
            back = date(q.year(), q.month(), q.day())
        if self.status.currentText() == "resolved" and not self.text.text().strip():
            QMessageBox.warning(self, "Update fault", "Add resolution notes.")
            return
        self.svc.update_fault(self.fault, self.status.currentText(), self.text.text().strip(),
                              blocking=self.blocking.isChecked(), back=back, user=self.user)
        self.accept()


class InstrumentCard(QFrame):
    def __init__(self, dash: "Dashboard", inst_id: str):
        super().__init__()
        svc = dash.svc
        inst = svc.instruments[inst_id]
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(300)
        self.setStyleSheet(f"InstrumentCard {{ border-left: 5px solid {inst.colour}; "
                           "border-radius: 4px; }")
        lay = QVBoxLayout(self)
        lay.setSpacing(4)
        title = QLabel(f"<b>{html.escape(inst.name)}</b>")
        lay.addWidget(title)
        st = logbook.instrument_status(inst, svc.cfg.logs)
        status = QLabel(html.escape(st.text))
        status.setWordWrap(True)
        status.setStyleSheet(f"color: {'#1a7f37' if st.available else '#cf222e'}; font-weight: 600;")
        lay.addWidget(status)
        now = datetime.now(timezone.utc)
        for c in logbook.counters(inst, svc.cfg.logs, svc.bookings(inst_id), now):
            bar = QProgressBar()
            bar.setRange(0, int(c.limit_hours))
            bar.setValue(int(min(c.used_hours, c.limit_hours)))
            bar.setFormat(f"{c.consumable}: {c.used_hours:.0f} / {c.limit_hours:.0f} h")
            colour = {"ok": "#2da44e", "warn": "#d4a72c", "limit": "#cf222e"}[c.level]
            bar.setStyleSheet(f"QProgressBar::chunk {{ background: {colour}; }}")
            bar.setTextVisible(True)
            lay.addWidget(bar)
        today = date.today()
        due = [d for d in logbook.maintenance_due(inst, svc.cfg.logs, today, svc.tz)
               if d.days_left(today) <= 30]
        for d in due:
            left = d.days_left(today)
            when = "overdue" if left < 0 else "due today" if left == 0 else f"due in {left} d"
            if d.last is None:
                when = "never logged"
            lab = QLabel(f"⏱ {html.escape(d.task)} — {when}")
            lab.setStyleSheet("color: #8c959f;" if d.last is None else
                              "color: #9a6700;" if left > 0 else "color: #cf222e;")
            lay.addWidget(lab)
        for f in logbook.open_faults(svc.cfg.logs, inst_id):
            row = QHBoxLayout()
            lab = QLabel(f"⚠ {html.escape(f.text)} <i>({f.status}{', blocking' if f.blocking else ''})"
                         "</i>")
            lab.setWordWrap(True)
            row.addWidget(lab, 1)
            b = QPushButton("Update…")
            b.clicked.connect(lambda _=False, fault=f: dash.update_fault(fault))
            row.addWidget(b)
            lay.addLayout(row)
        row = QHBoxLayout()
        for text, kind in (("Log…", "note"), ("Fault…", "fault")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, k=kind: dash.quick_log(inst_id, k))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)


class Dashboard(QScrollArea):
    changed = pyqtSignal()

    def __init__(self, svc: FacilityService, user_fn, parent=None):
        super().__init__(parent)
        self.svc = svc
        self.user_fn = user_fn
        self.instruments: list[str] = []
        self.setWidgetResizable(True)
        self.setMinimumWidth(340)
        self._cols = 0

    def set_instruments(self, ids: list[str]) -> None:
        self.instruments = ids
        if self.isVisible():
            self.rebuild()

    def showEvent(self, e):
        super().showEvent(e)
        self.rebuild()

    def rebuild(self) -> None:
        # a fresh container replaces the old one at once (deleteLater would
        # leave the old cards painted underneath until the next event loop)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        ids = [i for i in (self.instruments or sorted(self.svc.instruments))
               if i in self.svc.instruments]
        self._cols = max(1, self.viewport().width() // 330)
        for n, iid in enumerate(ids):
            grid.addWidget(InstrumentCard(self, iid), n // self._cols, n % self._cols)
        old = self.takeWidget()
        self.setWidget(inner)
        if old is not None:
            old.deleteLater()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if max(1, self.viewport().width() // 330) != self._cols:
            self.rebuild()

    def quick_log(self, instrument: str | None, kind: str = "note") -> None:
        dlg = QuickLogDialog(self.svc, instrument, self.user_fn(), self, kind)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.rebuild()
            self.changed.emit()

    def update_fault(self, fault: logbook.Fault) -> None:
        dlg = FaultDialog(self.svc, fault, self.user_fn(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.rebuild()
            self.changed.emit()

