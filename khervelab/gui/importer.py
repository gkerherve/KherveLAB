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
from PyQt6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                             QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                             QWidget)

from .. import db, ppms

NOT_IN_FILE = "(not in this file)"


class _FilePage(QWidget):
    """One exported file: what kind it is, which column is which, and a preview."""

    def __init__(self, table: ppms.Table, instruments: list[str] = (), parent=None):
        super().__init__(parent)
        self.table = table
        self.instruments = list(instruments)
        self.system_combos: dict[str, QComboBox] = {}
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
        side = QVBoxLayout(cols)
        side.addLayout(self.form)
        self.sys_box = QGroupBox("PPMS system → KherveLAB instrument")
        self.sys_form = QFormLayout(self.sys_box)
        side.addWidget(self.sys_box)
        side.addStretch(1)
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

    def _systems(self):
        chosen = {n: cb.currentData() for n, cb in self.system_combos.items()}
        while self.sys_form.rowCount():
            self.sys_form.removeRow(0)
        self.system_combos = {}
        names = self.source(with_systems=False).system_names()[:40]
        for name in names:
            cb = QComboBox()
            cb.addItem(f"New instrument “{name}”", None)
            for i in self.instruments:
                cb.addItem(i, i)
            same = next((i for i in self.instruments if ppms._norm(i) == ppms._norm(name)), None)
            pick = chosen.get(name, same)
            if pick:
                cb.setCurrentIndex(cb.findData(pick))
            self.system_combos[name] = cb
            self.sys_form.addRow(name, cb)
        self.sys_box.setVisible(bool(names) and bool(self.instruments))

    def _check(self, *_):
        self._systems()
        miss = self.missing()
        n = len(self.table.rows)
        self.note.setText(f"{n:,} rows. " + (f"<b style='color:#b42318'>Choose the column for: "
                                              f"{', '.join(miss)}.</b>" if miss else
                                              "Check that each field has the right column."))

    def source(self, with_systems: bool = True) -> ppms.Source:
        systems = {n: cb.currentData() for n, cb in self.system_combos.items()
                   if cb.currentData()} if with_systems else {}
        return ppms.Source(self.kind.currentData(), self.table,
                           {k: cb.currentData() for k, cb in self.combos.items()}, systems)


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
            "<br>Say which of your instruments each PPMS system is (e.g. “XPS / Bay 2” → "
            "XPS). Bookings keep the amount PPMS charged, including late cancellations it "
            "charged; a shared session is one booking per person. Imported people choose their password "
            "the first time they log in, by giving the email address on record. Importing the "
            "same files again skips what is already there.")
        intro.setWordWrap(True)
        add = QPushButton("Add exported files…")
        add.clicked.connect(self._add)
        self.dayfirst = QComboBox()
        self.dayfirst.addItem("Date order: read from each file (recommended)", None)
        self.dayfirst.addItem("Dates are day first (03/02/2026 is 3 February)", True)
        self.dayfirst.addItem("Dates are month first (03/02/2026 is 2 March)", False)
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
        names = [r[0] for r in self.conn.execute(
            "SELECT name FROM instruments WHERE active=1 ORDER BY name")]
        page = _FilePage(ppms.read_table(path), names)
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
            try:
                check = ppms.run(self.conn, sources, dry_run=True,
                                 dayfirst=self.dayfirst.currentData())
            except ValueError as exc:
                QMessageBox.warning(self, "Import from PPMS", f"{exc}.\n\nNothing was changed.")
                return
            skipped = check.counts.get("rows skipped", 0)
            ask = "Import these files into the lab? A backup of the lab is saved first."
            if skipped:
                ask = (f"{skipped:,} rows cannot be imported (press Check to see why). "
                       "Import the rest anyway? A backup of the lab is saved first.")
            if QMessageBox.question(self, "Import from PPMS", ask) != \
                    QMessageBox.StandardButton.Yes:
                return
            backup = str(self.backup_path())
            db.backup(self.conn, backup)
        try:
            s = ppms.run(self.conn, sources, dry_run=dry, dayfirst=self.dayfirst.currentData())
        except ValueError as exc:
            QMessageBox.warning(self, "Import from PPMS", f"{exc}.\n\nNothing was changed.")
            return
        except Exception as exc:
            QMessageBox.critical(self, "Import from PPMS", f"The import stopped and nothing was "
                                 f"changed:\n{exc}")
            return
        head = "Dry run, nothing saved. The import would give:" if dry else \
            f"Imported. A backup from just before is at:\n{backup}\n"
        self.result.setPlainText(head + "\n" + s.text())
        if not dry:
            self.imported = True
