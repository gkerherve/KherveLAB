"""Sample registry UI: browser, sample page, labels.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import html
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
                             QTableWidgetItem, QTextBrowser, QVBoxLayout)

from ..core.facility import FacilityService
from ..core.samples import Sample, children, lineage
from ..local.labels import LAYOUTS, make_labels


class SampleDialog(QDialog):
    def __init__(self, svc: FacilityService, sample: Sample | None = None, parent_id: str = "",
                 parent=None, names: dict[str, str] | None = None):
        super().__init__(parent)
        self.svc, self.sample = svc, sample
        self.setWindowTitle(f"Edit {sample.id}" if sample else "New sample")
        self.setMinimumWidth(460)
        src = sample or (svc.samples.get(parent_id) if parent_id else None)
        self.name = QLineEdit(sample.name if sample else "")
        self.composition = QLineEdit(src.composition if src else "")
        self.preparation = QPlainTextEdit(sample.preparation if sample else "")
        self.preparation.setFixedHeight(70)
        self.owner = QComboBox()
        self.owner.addItem("—", "")
        for u in sorted(svc.users.values(), key=lambda u: u.id):
            label = (names or {}).get(u.id) or u.display
            self.owner.addItem(f"{label} ({u.id})", u.id)
        self.owner.setCurrentIndex(max(0, self.owner.findData(src.owner if src else "")))
        self.parent_id = QLineEdit(sample.parent if sample else parent_id)
        self.parent_id.setPlaceholderText("derived from (sample id), optional")
        self.parent_id.setCompleter(QCompleter(sorted(svc.samples)))
        self.notes = QPlainTextEdit(sample.notes if sample else "")
        self.notes.setFixedHeight(60)
        form = QFormLayout(self)
        if sample:
            form.addRow("Id", QLabel(f"<b>{sample.id}</b>"))
        else:
            form.addRow("Id", QLabel("<i>generated on save</i>"))
        form.addRow("Name", self.name)
        form.addRow("Composition", self.composition)
        form.addRow("Preparation", self.preparation)
        form.addRow("Owner", self.owner)
        form.addRow("Parent", self.parent_id)
        form.addRow("Notes", self.notes)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.result_sample: Sample | None = None

    def _save(self):
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Sample", "Give the sample a name.")
            return
        parent = self.parent_id.text().strip().upper()
        if parent and parent not in self.svc.samples:
            QMessageBox.warning(self, "Sample", f"Unknown parent sample {parent}.")
            return
        try:
            if self.sample is None:
                self.result_sample = self.svc.add_sample(
                    name, self.composition.text().strip(), self.preparation.toPlainText().strip(),
                    self.owner.currentData(), parent, self.notes.toPlainText().strip())
            else:
                if parent and self.sample.id in {s.id for s in lineage(self.svc.samples, parent)}:
                    QMessageBox.warning(self, "Sample", "A sample cannot descend from itself.")
                    return
                self.result_sample = replace(
                    self.sample, name=name, composition=self.composition.text().strip(),
                    preparation=self.preparation.toPlainText().strip(),
                    owner=self.owner.currentData(), parent=parent,
                    notes=self.notes.toPlainText().strip())
                self.svc.update_sample(self.result_sample)
        except Exception as exc:  # PersonalDataError, RepoError
            QMessageBox.warning(self, "Sample", str(exc))
            return
        self.accept()


class LabelsDialog(QDialog):
    def __init__(self, samples: list[Sample], parent=None):
        super().__init__(parent)
        self.samples = samples
        self.setWindowTitle(f"Print labels ({len(samples)} sample(s))")
        self.layout_box = QComboBox()
        for key, lay in LAYOUTS.items():
            self.layout_box.addItem(lay.name, key)
        self.layout_box.setCurrentIndex(self.layout_box.findData("avery-l7160"))
        self.skip = QSpinBox()
        self.skip.setRange(0, 64)
        self.skip.setToolTip("Labels already used on the first sheet")
        self.copies = QSpinBox()
        self.copies.setRange(1, 20)
        form = QFormLayout(self)
        form.addRow("Sheet", self.layout_box)
        form.addRow("Skip used labels", self.skip)
        form.addRow("Copies of each", self.copies)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _save(self):
        default = f"labels-{self.samples[0].id}.pdf" if len(self.samples) == 1 else "labels.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Save labels", default, "PDF (*.pdf)")
        if not path:
            return
        make_labels(self.samples, Path(path), self.layout_box.currentData(), self.skip.value(),
                    self.copies.value())
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self.accept()


class SamplePage(QDialog):
    """Everything known about one sample: lineage, bookings, measurements."""

    def __init__(self, svc: FacilityService, sid: str, index=None, open_files=None, parent=None,
                 names: dict[str, str] | None = None):
        super().__init__(parent)
        self.svc, self.sid, self.index, self.open_files = svc, sid, index, open_files
        self.names = names or {}
        self.setWindowTitle(sid)
        self.resize(900, 640)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link)
        self.files = QTableWidget(0, 5)
        self.files.setHorizontalHeaderLabels(["Measured", "File", "Regions", "Sample", "Kind"])
        self.files.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.files.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.files.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files.doubleClicked.connect(self._open_selected)
        row = QHBoxLayout()
        for text, slot in (("Edit…", self._edit), ("Derive new sample…", self._derive),
                           ("Print label…", self._label),
                           ("Open in KherveFitting", self._open_selected)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 3)
        lay.addWidget(QLabel("<b>Measurements</b> (this sample and everything derived from it)"))
        lay.addWidget(self.files, 2)
        lay.addLayout(row)
        self.render()

    def _who(self, uid: str) -> str:
        u = self.svc.users.get(uid)
        return self.names.get(uid) or (u.display if u else uid)

    def _descendants(self, sid: str) -> list[str]:
        out, todo = [], [sid]
        while todo:
            for c in children(self.svc.samples, todo.pop()):
                out.append(c.id)
                todo.append(c.id)
        return out

    def render(self):
        s = self.svc.samples.get(self.sid)
        if s is None:
            self.view.setHtml(f"<h2>{html.escape(self.sid)}</h2><p>Unknown sample.</p>")
            return
        e = html.escape
        chain = lineage(self.svc.samples, s.id)
        lin = " ← ".join(f"<a href='sample:{x.id}'>{x.id}</a> {e(x.name)}" for x in chain)
        kids = children(self.svc.samples, s.id)
        tz = self.svc.tz
        bookings = self.svc.bookings_for_sample(s.id)
        brows = "".join(
            f"<li>{b.start.astimezone(tz):%d %b %Y %H:%M}–{b.end.astimezone(tz):%H:%M} · "
            f"{e(self.svc.instruments[b.instrument].name if b.instrument in self.svc.instruments else b.instrument)}"
            f" · {e(self._who(b.user))}</li>" for b in bookings)
        self.view.setHtml(
            f"<h2 style='margin:0'>{s.id} — {e(s.name)}</h2>"
            f"<p><b>Composition:</b> {e(s.composition) or '—'}<br>"
            f"<b>Owner:</b> {e(self._who(s.owner)) if s.owner else '—'}<br>"
            f"<b>Created:</b> {s.created or '—'}<br>"
            f"<b>Preparation:</b> {e(s.preparation) or '—'}</p>"
            + (f"<p><b>Notes:</b> {e(s.notes)}</p>" if s.notes else "")
            + f"<p><b>Lineage:</b> {lin}</p>"
            + (("<p><b>Derived samples:</b> " + ", ".join(
                f"<a href='sample:{k.id}'>{k.id}</a> {e(k.name)}" for k in kids) + "</p>")
               if kids else "")
            + (f"<p><b>Bookings</b></p><ul>{brows}</ul>" if brows else "<p>No bookings yet.</p>"))
        rows = self.index.for_sample(s.id, self._descendants(s.id)) if self.index else []
        self.rows = rows
        self.files.setRowCount(len(rows))
        for r, f in enumerate(rows):
            vals = (f.acquired.astimezone(tz).strftime("%Y-%m-%d %H:%M") if f.acquired else "",
                    f.filename, f.region_summary(), f.sample,
                    "result (derived)" if f.source == "derived" else f.kind)
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setToolTip(f.path)
                self.files.setItem(r, c, it)
        self.files.resizeColumnsToContents()

    def _link(self, url: QUrl):
        target = url.toString()
        if target.startswith("sample:"):
            self.sid = target.split(":", 1)[1]
            self.setWindowTitle(self.sid)
            self.render()

    def _edit(self):
        s = self.svc.samples.get(self.sid)
        if s and SampleDialog(self.svc, s, parent=self, names=self.names).exec():
            self.render()

    def _derive(self):
        dlg = SampleDialog(self.svc, parent_id=self.sid, parent=self, names=self.names)
        if dlg.exec() and dlg.result_sample:
            self.sid = dlg.result_sample.id
            self.render()

    def _label(self):
        s = self.svc.samples.get(self.sid)
        if s:
            LabelsDialog([s], self).exec()

    def _open_selected(self, *_):
        rows = sorted({i.row() for i in self.files.selectionModel().selectedRows()})
        if not rows or self.open_files is None:
            return
        self.open_files([Path(self.rows[r].path) for r in rows])


class SamplesBrowser(QDialog):
    def __init__(self, svc: FacilityService, index=None, open_files=None, parent=None,
                 names: dict[str, str] | None = None):
        super().__init__(parent)
        self.svc, self.index, self.open_files, self.names = svc, index, open_files, names or {}
        self.setWindowTitle("Samples")
        self.resize(900, 560)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by id, name, composition…")
        self.filter.textChanged.connect(self.fill)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Id", "Name", "Composition", "Parent", "Created"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._open)
        row = QHBoxLayout()
        for text, slot in (("New sample…", self._new), ("Open", self._open),
                           ("Print labels for selection…", self._labels)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(self.filter)
        lay.addWidget(self.table)
        lay.addLayout(row)
        self.fill()

    def fill(self, *_):
        t = self.filter.text().lower().strip()
        items = [s for s in sorted(self.svc.samples.values(), key=lambda s: s.id, reverse=True)
                 if not t or t in f"{s.id} {s.name} {s.composition}".lower()]
        self.table.setRowCount(len(items))
        for r, s in enumerate(items):
            for c, v in enumerate((s.id, s.name, s.composition, s.parent,
                                   s.created.isoformat() if s.created else "")):
                self.table.setItem(r, c, QTableWidgetItem(v))

    def _selected(self) -> list[Sample]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.svc.samples[self.table.item(r, 0).text()] for r in rows]

    def _new(self):
        dlg = SampleDialog(self.svc, parent=self, names=self.names)
        if dlg.exec() and dlg.result_sample:
            self.fill()
            SamplePage(self.svc, dlg.result_sample.id, self.index, self.open_files, self,
                       self.names).exec()

    def _open(self, *_):
        sel = self._selected()
        if sel:
            SamplePage(self.svc, sel[0].id, self.index, self.open_files, self, self.names).exec()
            self.fill()

    def _labels(self):
        sel = self._selected()
        if not sel:
            QMessageBox.information(self, "Labels", "Select one or more samples.")
            return
        LabelsDialog(sel, self).exec()

