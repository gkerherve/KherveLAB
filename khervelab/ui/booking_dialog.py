"""Create or edit one booking, with the rules engine checked live.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QDateTime, Qt, QTimeZone
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDateTimeEdit, QDialog,
                             QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core import schedule
from ..core.facility import FacilityService
from ..core.models import BOOKING_KINDS, Booking, User


def to_qdt(dt: datetime, tzname: str) -> QDateTime:
    return QDateTime.fromSecsSinceEpoch(int(dt.timestamp()), QTimeZone(tzname.encode()))


def from_qdt(q: QDateTime, tz) -> datetime:
    return datetime.fromtimestamp(q.toSecsSinceEpoch(), tz)


class BookingDialog(QDialog):
    def __init__(self, svc: FacilityService, booking: Booking, editing: Booking | None = None,
                 parent: QWidget | None = None, names: dict[str, str] | None = None):
        super().__init__(parent)
        self.svc = svc
        self.names = names or {}
        self.editing = editing
        self.result_booking: Booking | None = None
        self.setWindowTitle("Edit booking" if editing else "New booking")
        self.setMinimumWidth(460)
        tzname = svc.cfg.facility.timezone

        form = QFormLayout()
        self.instrument = QComboBox()
        for inst in sorted(svc.instruments.values(), key=lambda i: (i.facility, i.name)):
            self.instrument.addItem(inst.name, inst.id)
        self.instrument.setCurrentIndex(max(0, self.instrument.findData(booking.instrument)))
        form.addRow("Instrument", self.instrument)

        self.user = QComboBox()
        self._fill_users(booking.user)
        new_user = QPushButton("New…")
        new_user.setToolTip("Add an anonymous user id; names stay out of the repository")
        new_user.clicked.connect(self._new_user)
        row = QHBoxLayout()
        row.addWidget(self.user, 1)
        row.addWidget(new_user)
        form.addRow("User", row)

        self.start = QDateTimeEdit(to_qdt(booking.start, tzname))
        self.end = QDateTimeEdit(to_qdt(booking.end, tzname))
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("ddd d MMM yyyy  HH:mm")
            w.setTimeZone(QTimeZone(tzname.encode()))
        form.addRow("Start", self.start)
        form.addRow("End", self.end)

        self.kind = QComboBox()
        self.kind.addItems(BOOKING_KINDS)
        self.kind.setCurrentText(booking.kind)
        form.addRow("Kind", self.kind)

        self.samples = QLineEdit(", ".join(booking.samples))
        self.samples.setPlaceholderText("sample ids, comma separated")
        completer = QCompleter(sorted(svc.samples), self.samples)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.samples.setCompleter(completer)
        form.addRow("Samples", self.samples)
        self.notes = QPlainTextEdit(booking.notes)
        self.notes.setPlaceholderText("No names or emails: this file is shared")
        self.notes.setFixedHeight(70)
        form.addRow("Notes", self.notes)

        self.repeat = QComboBox()
        self.repeat.addItem("Does not repeat", 0)
        self.repeat.addItem("Every day", 1)
        self.repeat.addItem("Every week", 7)
        self.repeat.addItem("Every 2 weeks", 14)
        self.repeat_count = QSpinBox()
        self.repeat_count.setRange(2, 52)
        self.repeat_count.setValue(4)
        self.repeat_count.setSuffix(" times")
        rrow = QHBoxLayout()
        rrow.addWidget(self.repeat, 1)
        rrow.addWidget(self.repeat_count)
        if editing is None:
            form.addRow("Repeat", rrow)
        else:
            self.repeat.hide()
            self.repeat_count.hide()

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.override = QCheckBox("Override warnings (recorded in the booking)")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.info)
        lay.addWidget(self.override)
        lay.addWidget(self.buttons)

        for sig in (self.instrument.currentIndexChanged, self.user.currentIndexChanged,
                    self.start.dateTimeChanged, self.end.dateTimeChanged,
                    self.kind.currentIndexChanged, self.override.toggled,
                    self.repeat.currentIndexChanged):
            sig.connect(self._revalidate)
        self.repeat_count.setEnabled(False)
        self.repeat.currentIndexChanged.connect(
            lambda: self.repeat_count.setEnabled(bool(self.repeat.currentData())))
        self._revalidate()

    def _fill_users(self, select: str) -> None:
        self.user.blockSignals(True)
        self.user.clear()
        def label(u):
            return self.names.get(u.id) or u.display
        for u in sorted(self.svc.users.values(), key=lambda u: label(u).lower()):
            self.user.addItem(f"{label(u)}  ({u.id})", u.id)
        if select and self.user.findData(select) < 0:
            self.user.addItem(f"{select} (unknown)", select)
        self.user.setCurrentIndex(max(0, self.user.findData(select)))
        self.user.blockSignals(False)

    def _new_user(self) -> None:
        text, ok = QInputDialog.getText(
            self, "New user", "Display string shown on the calendar\n"
            "(initials or group, never a full name or email):")
        if not ok or not text.strip():
            return
        uid = self.svc.next_user_id()
        try:
            self.svc.save_user(User(uid, text.strip()))
        except Exception as exc:  # e.g. PersonalDataError
            self.info.setText(f"<span style='color:#cf222e'>{exc}</span>")
            return
        self._fill_users(uid)
        self._revalidate()

    def booking(self) -> Booking:
        tz = self.svc.tz
        samples = tuple(s.strip() for s in self.samples.text().split(",") if s.strip())
        b = Booking(
            instrument=self.instrument.currentData(),
            start=from_qdt(self.start.dateTime(), tz),
            end=from_qdt(self.end.dateTime(), tz),
            user=self.user.currentData() or "",
            kind=self.kind.currentText(),
            samples=samples,
            notes=self.notes.toPlainText().strip(),
            created=self.editing.created if self.editing else None,
            override=self.editing.override if self.editing else (),
        )
        return b

    def _revalidate(self) -> None:
        b = self.booking()
        if b.end <= b.start:
            self._show([schedule.Violation("duration", schedule.BLOCK, "end must be after start")])
            return
        inst = self.svc.instruments[b.instrument]
        snapped = schedule.snap_booking(b, inst, self.svc.tz)
        vs = [v for v in self.svc.check(snapped, ignore=self.editing) if v.rule != "granularity"]
        self._show(vs, snapped != b and f"Times snap to {inst.slot_granularity_minutes}-minute "
                                         "slots.")

    def _show(self, vs: list[schedule.Violation], note: str | bool = False) -> None:
        lines = []
        for v in vs:
            colour = "#cf222e" if v.blocking else "#9a6700"
            tag = "Blocked" if v.blocking else "Warning"
            lines.append(f"<span style='color:{colour}'><b>{tag}:</b> {v.message}</span>")
        if note:
            lines.append(f"<span style='color:#57606a'>{note}</span>")
        if not vs:
            lines.insert(0, "<span style='color:#1a7f37'>All booking rules pass.</span>")
        self.info.setText("<br>".join(lines))
        blocked = any(v.blocking for v in vs)
        warned = any(not v.blocking for v in vs)
        self.override.setVisible(warned)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(not blocked and (not warned or self.override.isChecked()))

    def _accept(self) -> None:
        self.result_booking = self.booking()
        self.accept()

    @property
    def repeat_every(self) -> int:
        return int(self.repeat.currentData() or 0) if self.editing is None else 0

    @property
    def wants_override(self) -> bool:
        return self.override.isVisible() and self.override.isChecked()

