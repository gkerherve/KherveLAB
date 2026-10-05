"""People and training records (local, encrypted), expiry dashboard, backups.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

from PyQt6.QtCore import QDate, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                             QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.facility import FacilityService
from ..core.models import User
from ..local.store import STATUSES, LocalStore, Person, Training
from ..local.training import export_person_pdf, new_person, reminder_mailto, sync_permissions

NO_DATE = QDate(2000, 1, 1)


def _qd(d: date | None) -> QDate:
    return QDate(d.year, d.month, d.day) if d else NO_DATE


def _pd(q: QDate) -> date | None:
    return None if q == NO_DATE else date(q.year(), q.month(), q.day())


def _date_edit(d: date | None) -> QDateEdit:
    w = QDateEdit(_qd(d))
    w.setCalendarPopup(True)
    w.setMinimumDate(NO_DATE)
    w.setSpecialValueText("—")
    w.setDisplayFormat("d MMM yyyy")
    return w


def open_bytes(data: bytes, filename: str) -> None:
    """Decrypted evidence opens from a private temp file."""
    d = Path(tempfile.mkdtemp(prefix="khervelab-"))
    p = d / filename
    p.write_bytes(data)
    os.chmod(p, 0o600)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


class TrainingDialog(QDialog):
    def __init__(self, svc: FacilityService, t: Training, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Training record")
        self.t = t
        self.instrument = QComboBox()
        for inst in sorted(svc.instruments.values(), key=lambda i: i.name):
            self.instrument.addItem(inst.name, inst.id)
        self.instrument.setCurrentIndex(max(0, self.instrument.findData(t.instrument)))
        self.status = QComboBox()
        self.status.addItems(STATUSES)
        self.status.setCurrentText(t.status)
        self.trained = _date_edit(t.date_trained or date.today())
        self.assessor = QLineEdit(t.assessor)
        self.expiry = _date_edit(t.expiry)
        self.refresher = QSpinBox()
        self.refresher.setRange(0, 120)
        self.refresher.setSuffix(" months")
        self.refresher.setSpecialValueText("no refresher")
        self.refresher.setValue(t.refresher_months)
        self.notes = QPlainTextEdit(t.notes)
        self.notes.setFixedHeight(80)
        form = QFormLayout(self)
        form.addRow("Instrument", self.instrument)
        form.addRow("Status", self.status)
        form.addRow("Date trained", self.trained)
        form.addRow("Assessor", self.assessor)
        form.addRow("Expires", self.expiry)
        form.addRow("Refresher interval", self.refresher)
        form.addRow("Assessment notes", self.notes)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def result_training(self) -> Training:
        t = self.t
        t.instrument = self.instrument.currentData()
        t.status = self.status.currentText()
        t.date_trained = _pd(self.trained.date())
        t.assessor = self.assessor.text().strip()
        t.expiry = _pd(self.expiry.date())
        t.refresher_months = self.refresher.value()
        t.notes = self.notes.toPlainText().strip()
        return t


class PeopleDialog(QDialog):
    """Person editor: identity (local only) linked to an anonymous repo id."""

    def __init__(self, svc: FacilityService, store: LocalStore, parent=None):
        super().__init__(parent)
        self.svc, self.store = svc, store
        self.changed = False
        self.setWindowTitle("People and training (stored only on this computer, encrypted)")
        self.resize(1050, 640)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load)
        add = QPushButton("Add person…")
        add.clicked.connect(self._add)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.list)
        ll.addWidget(add)

        self.name, self.email = QLineEdit(), QLineEdit()
        self.department, self.group, self.supervisor = QLineEdit(), QLineEdit(), QLineEdit()
        self.uid = QLabel()
        self.display = QLineEdit()
        self.github = QLineEdit()
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Email", self.email)
        form.addRow("Department", self.department)
        form.addRow("Group", self.group)
        form.addRow("Supervisor", self.supervisor)
        sep = QLabel("<b>Shared in the facility repository</b>")
        form.addRow(sep)
        form.addRow("User id", self.uid)
        form.addRow("Calendar display", self.display)
        form.addRow("GitHub username", self.github)
        save = QPushButton("Save person")
        save.clicked.connect(self._save)
        delete = QPushButton("Delete local record…")
        delete.clicked.connect(self._delete)
        pdf = QPushButton("Export record (PDF)…")
        pdf.clicked.connect(self._pdf)
        prow = QHBoxLayout()
        for b in (save, pdf, delete):
            prow.addWidget(b)
        prow.addStretch(1)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Instrument", "Status", "Trained", "Assessor",
                                              "Expires", "Evidence"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._edit_training)
        trow = QHBoxLayout()
        for text, slot in (("Add training…", self._add_training), ("Edit…", self._edit_training),
                           ("Remove", self._remove_training), ("Attach evidence…", self._attach),
                           ("Open evidence", self._open_evidence)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            trow.addWidget(b)
        trow.addStretch(1)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(form)
        rl.addLayout(prow)
        rl.addWidget(QLabel("<b>Training</b> — writing a record updates the user's permission "
                            "list in the repository (a boolean, nothing else)"))
        rl.addWidget(self.table, 1)
        rl.addLayout(trow)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([260, 790])
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill()
        self.current: Person | None = None
        if self.list.count():
            self.list.setCurrentRow(0)

    def _fill(self, select: int | None = None):
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.store.people():
            it = QListWidgetItem(f"{p.name}  ({p.user_id})")
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            self.list.addItem(it)
            if p.id == select:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        if select is not None:
            self._load(self.list.currentItem())

    def _load(self, item, _prev=None):
        if item is None:
            self.current = None
            return
        p = self.store.person(item.data(Qt.ItemDataRole.UserRole))
        self.current = p
        for w, v in ((self.name, p.name), (self.email, p.email), (self.department, p.department),
                     (self.group, p.group), (self.supervisor, p.supervisor)):
            w.setText(v)
        u = self.svc.users.get(p.user_id)
        self.uid.setText(p.user_id + ("" if u else "  (missing from repository)"))
        self.display.setText(u.display if u else "")
        self.github.setText(u.github if u else "")
        self._fill_training()

    def _fill_training(self):
        rows = self.store.trainings(self.current.id) if self.current else []
        self.table.setRowCount(len(rows))
        today = date.today()
        for r, t in enumerate(rows):
            inst = self.svc.instruments.get(t.instrument)
            exp = t.effective_expiry()
            ev = self.store.evidence(t.id)
            vals = [inst.name if inst else t.instrument,
                    t.status + ("" if t.active(today) or t.status != "trained" else " (expired)"),
                    t.date_trained.isoformat() if t.date_trained else "", t.assessor,
                    exp.isoformat() if exp else "", f"{len(ev)} file(s)" if ev else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, t.id)
                self.table.setItem(r, c, it)

    def _add(self):
        name, ok = QInputDialog.getText(self, "Add person", "Full name (kept on this computer):")
        if not ok or not name.strip():
            return
        display, ok = QInputDialog.getText(
            self, "Add person", "Calendar display string (public — initials or group):")
        if not ok or not display.strip():
            return
        try:
            p = new_person(self.svc, self.store, name.strip(), display.strip())
        except Exception as exc:
            QMessageBox.warning(self, "Add person", str(exc))
            return
        self.changed = True
        self._fill(p.id)

    def _save(self):
        p = self.current
        if p is None:
            return
        p.name, p.email = self.name.text().strip(), self.email.text().strip()
        p.department, p.group = self.department.text().strip(), self.group.text().strip()
        p.supervisor = self.supervisor.text().strip()
        self.store.save_person(p)
        u = self.svc.users.get(p.user_id)
        new_u = User(p.user_id, self.display.text().strip() or p.user_id,
                     github=self.github.text().strip(),
                     permissions=u.permissions if u else (), manager=u.manager if u else False)
        if new_u != u:
            try:
                self.svc.save_user(new_u)
            except Exception as exc:  # e.g. an email typed into the display string
                QMessageBox.warning(self, "Save", str(exc))
                return
        self.changed = True
        self._fill(p.id)

    def _delete(self):
        p = self.current
        if p is None:
            return
        if QMessageBox.question(self, "Delete", f"Delete the local record of {p.name}, including "
                                "training and evidence? The anonymous user stays in the "
                                "repository.") != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_person(p.id)
        self.changed = True
        self._fill()

    def _pdf(self):
        p = self.current
        if p is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export training record",
                                              f"Training record - {p.name}.pdf", "PDF (*.pdf)")
        if path:
            export_person_pdf(self.svc, self.store, p, Path(path))
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _selected_training(self) -> Training | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or self.current is None:
            return None
        tid = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((t for t in self.store.trainings(self.current.id) if t.id == tid), None)

    def _after_training(self):
        try:
            if sync_permissions(self.svc, self.store, self.current):
                self.changed = True
        except KeyError as exc:
            QMessageBox.warning(self, "Permissions", str(exc))
        self._fill_training()

    def _add_training(self):
        if self.current is None:
            return
        dlg = TrainingDialog(self.svc, Training(None, self.current.id,
                                                next(iter(self.svc.instruments), "")), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.store.save_training(dlg.result_training())
            self._after_training()

    def _edit_training(self, *_):
        t = self._selected_training()
        if t is None:
            return
        dlg = TrainingDialog(self.svc, t, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.store.save_training(dlg.result_training())
            self._after_training()

    def _remove_training(self):
        t = self._selected_training()
        if t and QMessageBox.question(self, "Remove", "Remove this training record and its "
                                      "evidence?") == QMessageBox.StandardButton.Yes:
            self.store.delete_training(t.id)
            self._after_training()

    def _attach(self):
        t = self._selected_training()
        if t is None:
            QMessageBox.information(self, "Evidence", "Select a training record first.")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach evidence (stored encrypted)")
        for p in paths:
            self.store.add_evidence(t.id, Path(p))
        self._fill_training()

    def _open_evidence(self):
        t = self._selected_training()
        if t is None:
            return
        ev = self.store.evidence(t.id)
        if not ev:
            QMessageBox.information(self, "Evidence", "No evidence attached.")
            return
        names = [e.filename for e in ev]
        name, ok = QInputDialog.getItem(self, "Open evidence", "File:", names, 0, False)
        if ok:
            e = ev[names.index(name)]
            open_bytes(self.store.read_evidence(e), e.filename)


class ExpiryDialog(QDialog):
    def __init__(self, svc: FacilityService, store: LocalStore, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Training expiring in the next 90 days")
        self.resize(720, 420)
        self.lapses = store.lapsing(90)
        table = QTableWidget(len(self.lapses), 4)
        table.setHorizontalHeaderLabels(["Person", "Instrument", "Expires", "Days left"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for r, l in enumerate(self.lapses):
            inst = svc.instruments.get(l.training.instrument)
            for c, v in enumerate((l.person.name, inst.name if inst else l.training.instrument,
                                   f"{l.expiry:%d %b %Y}",
                                   "expired" if l.days_left < 0 else str(l.days_left))):
                table.setItem(r, c, QTableWidgetItem(v))
        mail = QPushButton("Draft reminder email…")
        mail.setEnabled(bool(self.lapses))
        mail.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(reminder_mailto(self.lapses, svc.cfg.facility.name, svc))))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Nobody lapses in the next 90 days." if not self.lapses else
                             f"{len(self.lapses)} training record(s) expire within 90 days."))
        lay.addWidget(table)
        row = QHBoxLayout()
        row.addWidget(mail)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)


def ask_passphrase(parent, title: str, confirm: bool) -> str | None:
    p1, ok = QInputDialog.getText(parent, title, "Backup passphrase (at least 8 characters):",
                                  QLineEdit.EchoMode.Password)
    if not ok:
        return None
    if confirm:
        p2, ok = QInputDialog.getText(parent, title, "Repeat the passphrase:",
                                      QLineEdit.EchoMode.Password)
        if not ok:
            return None
        if p1 != p2:
            QMessageBox.warning(parent, title, "The passphrases differ.")
            return None
    return p1


def restore_dialog(parent, store: LocalStore) -> LocalStore | None:
    """Restore over the current local data, which is moved aside, not deleted."""
    path, _ = QFileDialog.getOpenFileName(parent, "Restore training records", "", "Zip (*.zip)")
    if not path:
        return None
    pw = ask_passphrase(parent, "Restore", confirm=False)
    if pw is None:
        return None
    from datetime import datetime
    home = store.home
    aside = home.parent / f"{home.name}-replaced-{datetime.now():%Y%m%d-%H%M%S}"
    store.close()
    home.rename(aside)
    try:
        restored = LocalStore.restore(Path(path), pw, home)
    except Exception as exc:
        if home.exists():
            import shutil
            shutil.rmtree(home)
        aside.rename(home)
        QMessageBox.warning(parent, "Restore", str(exc))
        return LocalStore(home, store.key)
    QMessageBox.information(parent, "Restore", f"Restored. The previous local data is kept in "
                            f"{aside}.")
    return restored


def backup_dialog(parent, store: LocalStore) -> bool:
    path, _ = QFileDialog.getSaveFileName(parent, "Back up training records",
                                          f"KherveLAB-local-backup-{date.today():%Y%m%d}.zip",
                                          "Zip (*.zip)")
    if not path:
        return False
    pw = ask_passphrase(parent, "Back up", confirm=True)
    if pw is None:
        return False
    try:
        store.backup(Path(path), pw)
    except ValueError as exc:
        QMessageBox.warning(parent, "Back up", str(exc))
        return False
    QMessageBox.information(parent, "Back up", f"Saved {path}.\nKeep it somewhere other than "
                            "this computer, and remember the passphrase: without it the backup "
                            "cannot be opened.")
    return True

