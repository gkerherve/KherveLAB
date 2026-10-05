"""Report dialog: pick a period, preview, export .xlsx or a KherveTeX project.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PyQt6.QtCore import QDate, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog,
                             QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                             QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core.facility import FacilityService
from ..core.reports import Report, build_report, training_rows
from ..reports_export import export_khervetex, export_xlsx


def _q(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _d(q: QDate) -> date:
    return date(q.year(), q.month(), q.day())


class ReportDialog(QDialog):
    def __init__(self, svc: FacilityService, store=None, visible: list[str] | None = None,
                 parent=None):
        super().__init__(parent)
        self.svc, self.store, self.visible = svc, store, visible or []
        self.report: Report | None = None
        self.setWindowTitle("Facility report")
        self.resize(980, 620)
        today = date.today()
        self.preset = QComboBox()
        self.preset.addItems(["Last 12 months", f"Calendar year {today.year - 1}",
                              f"Calendar year {today.year} to date",
                              f"Academic year {today.year - 1}/{str(today.year)[2:]}"
                              if today.month >= 10 else
                              f"Academic year {today.year - 2}/{str(today.year - 1)[2:]}",
                              "Custom"])
        self.start = QDateEdit(_q(date(today.year - 1, today.month, 1)))
        self.end = QDateEdit(_q(today))
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("d MMM yyyy")
        self.only_visible = QCheckBox("Only the instruments ticked in the sidebar")
        self.only_visible.setChecked(False)
        self.use_local = QCheckBox("Use names and groups from this computer's training records")
        self.use_local.setChecked(store is not None)
        self.use_local.setEnabled(store is not None)
        form = QFormLayout()
        form.addRow("Period", self.preset)
        rng = QHBoxLayout()
        rng.addWidget(self.start)
        rng.addWidget(QLabel("to (exclusive)"))
        rng.addWidget(self.end)
        form.addRow("", rng)
        form.addRow("", self.only_visible)
        form.addRow("", self.use_local)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Instrument", "Booked h", "Training h", "Bookable h",
                                              "Utilisation", "Downtime h", "Faults"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        row = QHBoxLayout()
        for text, slot in (("Export spreadsheet (.xlsx)…", self._xlsx),
                           ("Export KherveTeX report (.tex)…", self._tex)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.summary)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)
        self.preset.currentIndexChanged.connect(self._preset)
        for sig in (self.start.dateChanged, self.end.dateChanged):
            sig.connect(self._custom)
        for sig in (self.only_visible.toggled, self.use_local.toggled):
            sig.connect(self.compute)
        self._preset(0)

    def _preset(self, i: int):
        today = date.today()
        ranges = {
            0: (date(today.year - 1, today.month, today.day if today.day <= 28 else 28), today),
            1: (date(today.year - 1, 1, 1), date(today.year, 1, 1)),
            2: (date(today.year, 1, 1), today),
            3: ((date(today.year - 1, 10, 1), date(today.year, 10, 1)) if today.month >= 10
                else (date(today.year - 2, 10, 1), date(today.year - 1, 10, 1))),
        }
        if i in ranges:
            s, e = ranges[i]
            for w, d in ((self.start, s), (self.end, e)):
                w.blockSignals(True)
                w.setDate(_q(d))
                w.blockSignals(False)
            self.compute()

    def _custom(self, *_):
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(self.preset.count() - 1)
        self.preset.blockSignals(False)
        self.compute()

    def compute(self, *_):
        start, end = _d(self.start.date()), _d(self.end.date())
        if end <= start:
            self.summary.setText("The end date must be after the start date.")
            return
        names, groups, training = {}, {}, []
        if self.use_local.isChecked() and self.store is not None:
            for p in self.store.people():
                names[p.user_id] = p.name
                if p.group:
                    groups[p.user_id] = p.group
            training = training_rows(self.store, self.svc.users, start, end)
        insts = self.visible if self.only_visible.isChecked() and self.visible else None
        self.report = rep = build_report(self.svc.cfg, start, end, names, groups, training, insts)
        util = rep.total_booked / rep.total_bookable if rep.total_bookable else 0
        self.summary.setText(
            f"<b>{rep.total_booked:,.1f} h</b> booked of {rep.total_bookable:,.0f} bookable h "
            f"(<b>{util:.1%}</b>), {len(rep.downtime)} fault(s), "
            f"{sum(d.hours for d in rep.downtime):,.1f} h blocking downtime, "
            f"{len(rep.by_group)} group(s), {len(rep.training)} training record(s).")
        rows = rep.active() or rep.instruments
        self.table.setRowCount(len(rows))
        for r, i in enumerate(rows):
            for c, v in enumerate((i.name, f"{i.booked:.1f}", f"{i.training:.1f}",
                                   f"{i.bookable:.0f}", f"{i.utilisation:.1%}",
                                   f"{i.downtime:.1f}", str(i.faults))):
                self.table.setItem(r, c, QTableWidgetItem(v))

    def _stem(self) -> str:
        r = self.report
        return f"facility-report-{r.start:%Y%m%d}-{r.end:%Y%m%d}"

    def _xlsx(self):
        if self.report is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export spreadsheet", f"{self._stem()}.xlsx",
                                              "Excel workbook (*.xlsx)")
        if path:
            export_xlsx(self.report, Path(path))
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _tex(self):
        if self.report is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Folder for the KherveTeX project")
        if not folder:
            return
        target = Path(folder) / self._stem()
        path = export_khervetex(self.report, target, self._stem())
        QMessageBox.information(self, "KherveTeX report",
                                f"Wrote {path}.\nOpen it in KherveTeX and compile; the charts are "
                                "pgfplots inside the .tex, so nothing else is needed.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
