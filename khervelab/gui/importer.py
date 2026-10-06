"""Import from PPMS: choose the exported files, check the columns, dry run, import.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                             QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                             QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                             QWidget)

from .. import db, ppms

NOT_IN_FILE = "(not in this file)"


class _FilePage(QWidget):
    """One exported file: what kind it is, which column is which, and a preview."""

    def __init__(self, table: ppms.Table, parent=None):
        super().__init__(parent)
        self.table = table
        self.kind = QComboBox()
        for key in ppms.ORDER:
            self.kind.addItem(ppms.KINDS[key][0], key)
        guess = ppms.guess_kind(table.headers)
        if guess:
            self.kind.setCurrentIndex(ppms.ORDER.index(guess))
        self.form = QFormLayout()
        self.combos: dict[str, QComboBox] = {}
        top = QFormLayout()
        top.addRow("This file holds", self.kind)
        self.note = QLabel()
        self.note.setWordWrap(True)
        top.addRow(self.note)
        cols = QWidget()
        cols.setLayout(self.form)
        preview = QTableWidget(min(len(table.rows), 30), len(table.headers))
        preview.setHorizontalHeaderLabels(table.headers)
        for r, row in enumerate(table.rows[:30]):
            for c, h in enumerate(table.headers):
                v = row.get(h)
                preview.setItem(r, c, QTableWidgetItem("" if v is None else str(v)))
        preview.resizeColumnsToContents()
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(cols)
        split.addWidget(preview)
        split.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        self.kind.currentIndexChanged.connect(self._fields)
        self._fields()

    def _fields(self, *_):
        while self.form.rowCount():
            self.form.removeRow(0)
        self.combos = {}
        kind = self.kind.currentData()
        mapping = ppms.guess_mapping(kind, self.table.headers)
        for f in ppms.KINDS[kind][1]:
            cb = QComboBox()
            cb.addItem(NOT_IN_FILE, None)
            for h in self.table.headers:
                cb.addItem(h, h)
            if mapping.get(f.key):
                cb.setCurrentIndex(cb.findData(mapping[f.key]))
            cb.currentIndexChanged.connect(self._check)
            self.combos[f.key] = cb
            self.form.addRow(f.label + (" *" if f.required else ""), cb)
        self._check()

    def missing(self) -> list[str]:
        kind = self.kind.currentData()
        return [f.label for f in ppms.KINDS[kind][1]
                if f.required and self.combos[f.key].currentData() is None]

    def _check(self, *_):
        miss = self.missing()
        n = len(self.table.rows)
        self.note.setText(f"{n:,} rows. " + (f"<b style='color:#b42318'>Choose the column for: "
                                              f"{', '.join(miss)}.</b>" if miss else
                                              "Check that each field has the right column."))

    def source(self) -> ppms.Source:
        return ppms.Source(self.kind.currentData(), self.table,
                           {k: cb.currentData() for k, cb in self.combos.items()})


class PpmsImportDialog(QDialog):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.imported = False
        self.setWindowTitle("Import from PPMS")
        self.resize(1100, 720)
        intro = QLabel(
            "Export from PPMS whichever of these you have, as CSV or Excel: <b>systems</b>, "
            "<b>users</b>, <b>prices</b>, <b>rights</b> (training), <b>bookings or usage</b> "
            "with the amount charged, and <b>incidents</b>. Add them all here; KherveLAB "
            "recognises each file and its columns, and you can correct anything it got wrong."
            "<br>Bookings keep the amount PPMS charged. Imported people choose their password "
            "the first time they log in, by giving the email address on record. Importing the "
            "same files again skips what is already there.")
        intro.setWordWrap(True)
        add = QPushButton("Add exported files…")
        add.clicked.connect(self._add)
        self.dayfirst = QCheckBox("Dates are day first (03/02/2026 is 3 February)")
        self.dayfirst.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(self.dayfirst)
        row.addStretch(1)
        self.pages = QTabWidget()
        self.pages.setTabsClosable(True)
        self.pages.tabCloseRequested.connect(self.pages.removeTab)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setFont(QFont("Menlo, Consolas, monospace"))
        self.result.setPlaceholderText("Press Check to see what the import would do. Nothing "
                                       "is saved until you press Import.")
        self.result.setMaximumHeight(200)
        check = QPushButton("Check (dry run)")
        check.clicked.connect(lambda: self._run(dry=True))
        self.go = QPushButton("Import")
        self.go.clicked.connect(lambda: self._run(dry=False))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(check)
        bottom.addWidget(self.go)
        bottom.addStretch(1)
        bottom.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(row)
        lay.addWidget(self.pages, 1)
        lay.addWidget(self.result)
        lay.addLayout(bottom)

    def add_file(self, path: str | Path) -> _FilePage:
        page = _FilePage(ppms.read_table(path))
        self.pages.addTab(page, Path(path).name)
        self.pages.setCurrentWidget(page)
        return page

    def _add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "PPMS exports", "",
                                                "CSV or Excel (*.csv *.txt *.xlsx *.xlsm)")
        for p in paths:
            try:
                self.add_file(p)
            except Exception as exc:      # an unreadable file should not close the dialog
                QMessageBox.warning(self, "Import from PPMS", f"Cannot read {p}:\n{exc}")

    def _sources(self) -> list[ppms.Source] | None:
        pages = [self.pages.widget(i) for i in range(self.pages.count())]
        if not pages:
            QMessageBox.information(self, "Import from PPMS", "Add the exported files first.")
            return None
        for i, p in enumerate(pages):
            if p.missing():
                self.pages.setCurrentIndex(i)
                QMessageBox.warning(self, "Import from PPMS",
                                    f"{self.pages.tabText(i)}: choose the column for "
                                    f"{', '.join(p.missing())}.")
                return None
        return [p.source() for p in pages]

    def backup_path(self) -> Path:
        main = next(r for r in self.conn.execute("PRAGMA database_list") if r[1] == "main")[2]
        folder = Path(main).parent if main else Path.home()
        return folder / f"lab-before-ppms-import-{datetime.now():%Y%m%d-%H%M%S}.db"

    def _run(self, dry: bool):
        sources = self._sources()
        if sources is None:
            return
        backup = ""
        if not dry:
            if QMessageBox.question(self, "Import from PPMS",
                                    "Import these files into the lab? A backup of the lab is "
                                    "saved first.") != QMessageBox.StandardButton.Yes:
                return
            backup = str(self.backup_path())
            db.backup(self.conn, backup)
        try:
            s = ppms.run(self.conn, sources, dry_run=dry, dayfirst=self.dayfirst.isChecked())
        except Exception as exc:
            QMessageBox.critical(self, "Import from PPMS", f"The import stopped and nothing was "
                                 f"changed:\n{exc}")
            return
        head = "Dry run, nothing saved. The import would give:" if dry else \
            f"Imported. A backup from just before is at:\n{backup}\n"
        self.result.setPlainText(head + "\n" + s.text())
        if not dry:
            self.imported = True
