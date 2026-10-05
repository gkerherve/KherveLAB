"""Data index UI: search, folders, manual links, open in KherveFitting.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                             QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from ..core.facility import FacilityService
from ..local.dataindex import DataIndex, IndexedFile

RESCAN_MS = 10 * 60 * 1000


class ScanWorker(QThread):
    """Scans with its own SQLite connection: connections are per thread."""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, db_path: Path, tz, parent=None):
        super().__init__(parent)
        self.db_path, self.tz = db_path, tz

    def run(self):
        try:
            idx = DataIndex(self.db_path)
            rep = idx.scan(self.tz)
            idx.close()
            self.done.emit(rep)
        except Exception as exc:
            self.failed.emit(str(exc))


class FoldersDialog(QDialog):
    def __init__(self, svc: FacilityService, index: DataIndex, parent=None):
        super().__init__(parent)
        self.svc, self.index = svc, index
        self.setWindowTitle("Watched data folders")
        self.resize(720, 360)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Folder", "Instrument"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        add, rem = QPushButton("Add folder…"), QPushButton("Remove")
        add.clicked.connect(self._add)
        rem.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.accept)
        row.addWidget(bb)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Folders are scanned recursively. Files are matched to bookings on "
                             "the folder's instrument by acquisition time."))
        lay.addWidget(self.table)
        lay.addLayout(row)
        self.fill()

    def fill(self):
        rows = self.index.folders()
        self.table.setRowCount(len(rows))
        for r, (path, inst) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(path))
            i = self.svc.instruments.get(inst)
            self.table.setItem(r, 1, QTableWidgetItem(i.name if i else inst))

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "Data folder (an instrument PC share, say)")
        if not d:
            return
        names = [f"{i.name} ({i.id})" for i in sorted(self.svc.instruments.values(),
                                                         key=lambda i: i.name)]
        choice, ok = QInputDialog.getItem(self, "Instrument", "Data in this folder comes from:",
                                          names, 0, False)
        if ok:
            iid = choice.rsplit("(", 1)[1].rstrip(")")
            self.index.add_folder(Path(d), iid)
            self.fill()

    def _remove(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.index.remove_folder(self.table.item(rows[0].row(), 0).text())
            self.fill()


class LinkDialog(QDialog):
    """Manual correction of a file's booking and sample."""

    def __init__(self, svc: FacilityService, f: IndexedFile, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Link {f.filename}")
        tz = svc.tz
        self.booking = QComboBox()
        self.booking.addItem("— no booking —", "")
        near = sorted(svc.bookings(f.instrument),
                      key=lambda b: abs((b.start - f.acquired).total_seconds()) if f.acquired else 0)
        for b in near[:40]:
            s = b.start.astimezone(tz)
            self.booking.addItem(f"{s:%Y-%m-%d %H:%M} {b.instrument} {b.user}", svc.path_of(b))
        self.booking.setCurrentIndex(max(0, self.booking.findData(f.booking)))
        self.sample = QComboBox()
        self.sample.setEditable(True)
        self.sample.addItem("", "")
        for s in sorted(svc.samples.values(), key=lambda s: s.id, reverse=True):
            self.sample.addItem(f"{s.id}  {s.name}", s.id)
        self.sample.setCurrentIndex(max(0, self.sample.findData(f.sample)))
        form = QFormLayout(self)
        form.addRow("Booking", self.booking)
        form.addRow("Sample", self.sample)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self) -> tuple[str, str]:
        sample = self.sample.currentData()
        if sample is None:
            sample = self.sample.currentText().split()[0] if self.sample.currentText() else ""
        return self.booking.currentData() or "", sample or ""


class DataPanel(QWidget):
    sampleRequested = pyqtSignal(str)

    def __init__(self, svc: FacilityService, index: DataIndex, open_files, parent=None):
        super().__init__(parent)
        self.svc, self.index, self.open_files = svc, index, open_files
        self.worker: ScanWorker | None = None
        self.rows: list[IndexedFile] = []
        self.query = QLineEdit()
        self.query.setPlaceholderText("e.g.  Co 2p, 20 eV pass energy, since March   ·   "
                                      "unbooked   ·   S-2026-0012   ·   results")
        self.query.returnPressed.connect(self.search)
        go = QPushButton("Search")
        go.clicked.connect(self.search)
        top = QHBoxLayout()
        top.addWidget(self.query, 1)
        top.addWidget(go)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Measured", "Instrument", "File", "Regions",
                                              "Sample", "Booking", "Path"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._open)
        self.status = QLabel()
        row = QHBoxLayout()
        for text, slot in (("Open in KherveFitting", self._open), ("Open sample", self._sample),
                           ("Link…", self._link), ("Unbooked files", self._unbooked),
                           ("Folders…", self._folders), ("Rescan", self.rescan)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.status)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)

        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _: self.debounce.start())
        self.debounce = QTimer(self, singleShot=True, interval=5000, timeout=self.rescan)
        self.timer = QTimer(self, interval=RESCAN_MS, timeout=self.rescan)
        self.timer.start()
        self._watch()
        self._update_status()

    def _watch(self):
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        dirs = [p for p, _ in self.index.folders() if Path(p).is_dir()]
        if dirs:
            self.watcher.addPaths(dirs)

    def _update_status(self, extra: str = ""):
        self.status.setText(f"{self.index.count()} files indexed in {len(self.index.folders())} "
                            f"folder(s){extra}")

    def rescan(self):
        if self.worker and self.worker.isRunning():
            return
        if not self.index.folders():
            self._update_status()
            return
        self.status.setText("Indexing…")
        self.worker = ScanWorker(self.index.path, self.svc.tz, self)
        self.worker.done.connect(self._scanned)
        self.worker.failed.connect(lambda m: self.status.setText(f"Indexing failed: {m}"))
        self.worker.start()

    def _scanned(self, rep):
        self.index.match(self.svc)
        self._update_status(f" · +{rep.added} new, {rep.updated} changed, {rep.removed} gone")
        if self.query.text().strip():
            self.search()

    def search(self):
        q = self.query.text().strip()
        self.show_rows(self.index.search(q, self.svc.instruments) if q
                       else self.index.search("", limit=300))

    def show_rows(self, rows: list[IndexedFile]):
        self.rows = rows
        tz = self.svc.tz
        self.table.setRowCount(len(rows))
        for r, f in enumerate(rows):
            inst = self.svc.instruments.get(f.instrument)
            vals = (f.acquired.astimezone(tz).strftime("%Y-%m-%d %H:%M") if f.acquired else "",
                    inst.name if inst else f.instrument, f.filename, f.region_summary(),
                    f.sample + (" ✎" if f.manual else ""),
                    "yes" if f.booking else ("— unbooked" if f.kind == "raw" else ""), f.path)
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 5 and not f.booking and f.kind == "raw":
                    it.setForeground(Qt.GlobalColor.darkRed)
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()
        self._update_status(f" · {len(rows)} shown")

    def _selected(self) -> list[IndexedFile]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.rows[r] for r in rows]

    def _open(self, *_):
        sel = self._selected()
        if sel:
            self.open_files([Path(f.path) for f in sel])

    def _sample(self):
        sel = [f for f in self._selected() if f.sample]
        if sel:
            self.sampleRequested.emit(sel[0].sample)

    def _link(self):
        sel = self._selected()
        if not sel:
            return
        dlg = LinkDialog(self.svc, sel[0], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            booking, sample = dlg.values()
            for f in sel:
                self.index.set_link(f.id, booking, sample)
            self.search()

    def _unbooked(self):
        self.query.setText("unbooked")
        self.search()

    def _folders(self):
        FoldersDialog(self.svc, self.index, self).exec()
        self._watch()
        self.rescan()

