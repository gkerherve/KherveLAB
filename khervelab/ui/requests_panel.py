"""Approval queue for booking requests arriving as GitHub issues.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import webbrowser

from PyQt6.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                             QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.facility import BookingRejected, FacilityService
from ..core.github import GitHubClient, GitHubError, get_token, set_token
from ..core.requests import (BookingRequest, RequestQueue, ensure_issue_template,
                             suggest_alternative)

POLL_MS = 5 * 60 * 1000


class PollWorker(QThread):
    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, queue: RequestQueue, parent=None):
        super().__init__(parent)
        self.queue = queue

    def run(self):
        try:
            self.done.emit(self.queue.poll())
        except GitHubError as exc:
            self.failed.emit(str(exc))


class TokenDialog(QDialog):
    def __init__(self, repo: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GitHub token")
        self.edit = QLineEdit(get_token(repo))
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        lay = QVBoxLayout(self)
        lab = QLabel(f"A fine-grained personal access token for <b>{repo}</b> with "
                     "<i>Issues: read and write</i>. It is kept in the system keychain.")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        form = QFormLayout()
        form.addRow("Token", self.edit)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class RequestsPanel(QWidget):
    countChanged = pyqtSignal(int)
    bookingsChanged = pyqtSignal()

    def __init__(self, svc: FacilityService, parent=None):
        super().__init__(parent)
        self.svc = svc
        self.queue: RequestQueue | None = None
        self.worker: PollWorker | None = None

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "From", "Instrument", "Requested slot", "Check"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.doubleClicked.connect(self._open_issue)

        self.b_approve = QPushButton("Approve")
        self.b_decline = QPushButton("Decline…")
        self.b_propose = QPushButton("Propose another time")
        self.b_open = QPushButton("Open on GitHub")
        self.b_poll = QPushButton("Check now")
        self.b_approve.clicked.connect(self.approve)
        self.b_decline.clicked.connect(self.decline)
        self.b_propose.clicked.connect(self.propose)
        self.b_open.clicked.connect(self._open_issue)
        self.b_poll.clicked.connect(self.poll)
        row = QHBoxLayout()
        for b in (self.b_approve, self.b_decline, self.b_propose, self.b_open):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.b_poll)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.info)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)

        self.timer = QTimer(self, interval=POLL_MS, timeout=self.poll)
        self.timer.start()
        self.set_service(svc)

    # -- setup ------------------------------------------------------------
    def set_service(self, svc: FacilityService) -> None:
        self.svc = svc
        repo = svc.cfg.facility.github
        if not repo:
            self.queue = None
            self.info.setText("Booking requests are off: set <code>github: owner/repo</code> in "
                              "<code>facility.yaml</code> to enable the <i>Request a slot</i> "
                              "button and this queue.")
            self._fill([])
            return
        try:
            ensure_issue_template(svc)
        except Exception:
            pass
        self.queue = RequestQueue(svc, GitHubClient(repo, get_token(repo)))
        self.info.setText(f"Requests from <b>{repo}</b> issues labelled "
                          "<code>booking-request</code>.")
        QTimer.singleShot(500, self.poll)

    def edit_token(self) -> None:
        repo = self.svc.cfg.facility.github
        if not repo:
            QMessageBox.information(self, "GitHub token", "Set github: owner/repo in facility.yaml "
                                    "first.")
            return
        dlg = TokenDialog(repo, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                set_token(repo, dlg.edit.text().strip())
            except Exception as exc:
                QMessageBox.warning(self, "GitHub token", f"Could not store the token: {exc}")
                return
            self.set_service(self.svc)

    # -- polling ----------------------------------------------------------
    def poll(self) -> None:
        if self.queue is None or (self.worker and self.worker.isRunning()):
            return
        self.info.setText("Checking GitHub for requests…")
        self.worker = PollWorker(self.queue, self)
        self.worker.done.connect(self._polled)
        self.worker.failed.connect(lambda m: self.info.setText(f"<span style='color:#cf222e'>{m}"
                                                               "</span> — will retry."))
        self.worker.start()

    def _polled(self, items: list[BookingRequest]) -> None:
        self.queue.recheck()  # rules run against the live calendar, on this thread
        self.info.setText(f"{len(self.queue.items)} open request(s) on "
                          f"<b>{self.svc.cfg.facility.github}</b>.")
        self._fill(self.queue.items)

    def _fill(self, items: list[BookingRequest]) -> None:
        self.table.setRowCount(len(items))
        tz = self.svc.tz
        for row, r in enumerate(items):
            inst = self.svc.instruments.get(r.instrument)
            who = r.issue.author + ("" if r.user else "  (not linked)")
            if r.user and r.user in self.svc.users:
                who = f"{self.svc.users[r.user].display} ({r.issue.author})"
            slot = "—"
            if r.start:
                s, e = r.start.astimezone(tz), r.end.astimezone(tz)
                slot = f"{s:%a %d %b %H:%M}–{e:%H:%M}"
            if r.errors:
                check, colour = "; ".join(r.errors), "#cf222e"
            elif any(v.blocking for v in r.violations):
                check, colour = "; ".join(v.message for v in r.violations), "#cf222e"
            elif r.violations:
                check, colour = "; ".join(v.message for v in r.violations), "#9a6700"
            else:
                check, colour = "OK — free and within the rules", "#1a7f37"
            for col, text in enumerate((f"#{r.number}", who, inst.name if inst else r.instrument,
                                        slot, check)):
                it = QTableWidgetItem(text)
                it.setData(Qt.ItemDataRole.UserRole, r.number)
                if col == 4:
                    it.setForeground(QColor(colour))
                it.setToolTip(r.issue.title)
                self.table.setItem(row, col, it)
        self.table.resizeColumnsToContents()
        self.countChanged.emit(len(items))

    # -- actions ----------------------------------------------------------
    def _selected(self) -> BookingRequest | None:
        if self.queue is None:
            return None
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Requests", "Select a request first.")
            return None
        n = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((r for r in self.queue.items if r.number == n), None)

    def _open_issue(self, *_):
        r = self._selected()
        if r:
            webbrowser.open(r.issue.url)

    def _ensure_user(self, r: BookingRequest) -> bool:
        if r.user:
            return True
        text, ok = QInputDialog.getText(
            self, "Unknown GitHub user",
            f"{r.issue.author} has no KherveLAB user yet.\nDisplay string for the calendar "
            "(initials or group, never a full name):")
        if not ok or not text.strip():
            return False
        self.queue.link_user(r, text.strip())
        return True

    def approve(self):
        r = self._selected()
        if r is None:
            return
        if not r.parsed:
            QMessageBox.warning(self, "Approve", "This request could not be read: "
                                + "; ".join(r.errors) + ".\nAsk for a corrected issue.")
            return
        if not self._ensure_user(r):
            return
        try:
            self.queue.approve(r)
        except BookingRejected as exc:
            if any(v.blocking for v in exc.violations):
                QMessageBox.warning(self, "Approve", "\n".join(v.message for v in exc.violations))
                return
            if QMessageBox.question(self, "Override?", "\n".join(v.message for v in exc.violations)
                                    + "\n\nApprove anyway?") != QMessageBox.StandardButton.Yes:
                return
            try:
                self.queue.approve(r, override=True)
            except (BookingRejected, GitHubError) as exc2:
                QMessageBox.warning(self, "Approve", str(exc2))
                return
        except GitHubError as exc:
            QMessageBox.warning(self, "Approve", f"Booked, but GitHub could not be updated: {exc}")
        self._fill(self.queue.items)
        self.bookingsChanged.emit()

    def decline(self):
        r = self._selected()
        if r is None:
            return
        reason, ok = QInputDialog.getText(self, "Decline request", "Reason (posted on the issue):")
        if not ok:
            return
        try:
            self.queue.decline(r, reason.strip())
        except GitHubError as exc:
            QMessageBox.warning(self, "Decline", str(exc))
        self._fill(self.queue.items)

    def propose(self):
        r = self._selected()
        if r is None:
            return
        alt = suggest_alternative(self.svc, r)
        if alt is None:
            QMessageBox.information(self, "Propose", "No free slot of that length in the next "
                                    "two weeks.")
            return
        s, e = alt[0].astimezone(self.svc.tz), alt[1].astimezone(self.svc.tz)
        if QMessageBox.question(self, "Propose another time",
                                f"Propose {s:%a %d %b %H:%M}–{e:%H:%M}?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self.queue.propose(r, *alt)
        except GitHubError as exc:
            QMessageBox.warning(self, "Propose", str(exc))
