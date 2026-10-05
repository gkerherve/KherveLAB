"""First-run setup and conflict resolution dialogs.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                             QRadioButton, QVBoxLayout, QWidget)

from ..core.facility import FacilityService
from ..core.models import Booking
from ..core.schedule import pick_winner
from ..core.repo import FacilityRepo, FileConflict, RepoError
from ..core.yamlio import ConfigError

DEFAULT_PATH = Path.home() / "KherveLAB" / "facility"


def _path_row(edit: QLineEdit, parent: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    btn = QPushButton("Browse…")

    def browse():
        d = QFileDialog.getExistingDirectory(parent, "Choose folder", edit.text() or str(Path.home()))
        if d:
            edit.setText(d)
    btn.clicked.connect(browse)
    row.addWidget(btn)
    return row


class SetupDialog(QDialog):
    """Create a new facility repository, open a working copy, or clone one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to KherveLAB")
        self.setMinimumWidth(560)
        self.repo: FacilityRepo | None = None

        intro = QLabel(
            "<b>KherveLAB keeps its shared state in a Git repository.</b><br>"
            "Bookings, instruments and anonymous user ids are plain YAML files; "
            "every change is one commit.")
        intro.setWordWrap(True)

        self.mode = QButtonGroup(self)
        self.r_new = QRadioButton("Create a new facility (starts with the Department of "
                                  "Materials instrument catalogue)")
        self.r_open = QRadioButton("Open an existing working copy")
        self.r_clone = QRadioButton("Clone a facility repository from a URL")
        for i, r in enumerate((self.r_new, self.r_open, self.r_clone)):
            self.mode.addButton(r, i)
        self.r_new.setChecked(True)

        self.name = QLineEdit("Department of Materials — shared instruments")
        self.new_path = QLineEdit(str(DEFAULT_PATH))
        self.remote = QLineEdit()
        self.remote.setPlaceholderText("optional, e.g. https://github.com/<you>/<facility>.git")
        new_box = QGroupBox()
        f1 = QFormLayout(new_box)
        f1.addRow("Facility name", self.name)
        f1.addRow("Folder", _path_row(self.new_path, self))
        f1.addRow("Remote URL", self.remote)

        self.open_path = QLineEdit()
        open_box = QGroupBox()
        f2 = QFormLayout(open_box)
        f2.addRow("Working copy", _path_row(self.open_path, self))

        self.clone_url = QLineEdit()
        self.clone_url.setPlaceholderText("https://github.com/<you>/<facility>.git")
        self.clone_path = QLineEdit(str(DEFAULT_PATH))
        clone_box = QGroupBox()
        f3 = QFormLayout(clone_box)
        f3.addRow("Repository URL", self.clone_url)
        f3.addRow("Folder", _path_row(self.clone_path, self))

        self.boxes = (new_box, open_box, clone_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._go)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        for r, box in zip((self.r_new, self.r_open, self.r_clone), self.boxes):
            lay.addWidget(r)
            lay.addWidget(box)
        lay.addWidget(buttons)
        self.mode.idToggled.connect(self._sync_boxes)
        self._sync_boxes()

    def _sync_boxes(self, *_):
        for i, box in enumerate(self.boxes):
            box.setVisible(self.mode.checkedId() == i)
        self.adjustSize()

    def _go(self):
        try:
            mode = self.mode.checkedId()
            if mode == 0:
                self.repo = FacilityRepo.create(Path(self.new_path.text()).expanduser(),
                                                name=self.name.text().strip() or None,
                                                remote_url=self.remote.text().strip() or None)
            elif mode == 1:
                self.repo = FacilityRepo(Path(self.open_path.text()).expanduser())
            else:
                self.repo = FacilityRepo.clone(self.clone_url.text().strip(),
                                               Path(self.clone_path.text()).expanduser())
            FacilityService(self.repo)  # validates every file before we accept
        except (RepoError, ConfigError, OSError) as exc:
            QMessageBox.warning(self, "KherveLAB", str(exc))
            self.repo = None
            return
        self.accept()


def _describe(svc: FacilityService, b: Booking | None) -> str:
    if b is None:
        return "<i>deleted</i>"
    u = svc.users.get(b.user)
    who = u.display if u else b.user
    s, e = b.start.astimezone(svc.tz), b.end.astimezone(svc.tz)
    created = b.created.astimezone(svc.tz).strftime("%d %b %H:%M:%S") if b.created else "unknown"
    return (f"<b>{who}</b> ({b.user})<br>{s:%a %d %b %H:%M} – {e:%H:%M}<br>"
            f"{b.kind}<br><span style='color:#57606a'>created {created}</span>")


class ConflictDialog(QDialog):
    """Both versions side by side; the earlier-created one is the default."""

    def __init__(self, svc: FacilityService, conflicts: list[FileConflict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Booking conflict")
        self.choices: dict[str, str] = {}
        self._ours: dict[str, QRadioButton] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Someone else changed the same slot. Choose which version to keep."))
        for c in conflicts:
            ours = theirs = None
            try:
                ours = svc.parse_conflict_side(c.ours, c.path)
                theirs = svc.parse_conflict_side(c.theirs, c.path)
            except ConfigError:
                pass
            box = QGroupBox(c.path)
            row = QHBoxLayout(box)
            grp = QButtonGroup(self)
            r_ours = QRadioButton("Keep mine")
            r_theirs = QRadioButton("Keep theirs")
            grp.addButton(r_ours)
            grp.addButton(r_theirs)
            for r, b in ((r_ours, ours), (r_theirs, theirs)):
                col = QVBoxLayout()
                col.addWidget(r)
                lab = QLabel(_describe(svc, b))
                col.addWidget(lab)
                row.addLayout(col)
            default_theirs = (ours is None or (theirs is not None and theirs.created and ours.created
                                               and theirs.created < ours.created))
            (r_theirs if default_theirs else r_ours).setChecked(True)
            self._ours[c.path] = r_ours
            lay.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _ok(self):
        for path, r_ours in self._ours.items():
            self.choices[path] = "ours" if r_ours.isChecked() else "theirs"
        self.accept()


class ClashDialog(QDialog):
    """Two different slot files that overlap after a clean merge."""

    def __init__(self, svc: FacilityService, a: Booking, b: Booking, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Overlapping bookings")
        inst = svc.instruments.get(a.instrument)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Two bookings overlap on <b>{inst.name if inst else a.instrument}"
                             "</b>. Keep one; the other is cancelled."))
        row = QHBoxLayout()
        self.ra, self.rb = QRadioButton("Keep this"), QRadioButton("Keep this")
        for r, x in ((self.ra, a), (self.rb, b)):
            col = QVBoxLayout()
            col.addWidget(r)
            col.addWidget(QLabel(_describe(svc, x)))
            row.addLayout(col)
        lay.addLayout(row)
        (self.ra if pick_winner(a, b) is a else self.rb).setChecked(True)
        self.a, self.b = a, b
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @property
    def loser(self) -> Booking:
        return self.b if self.ra.isChecked() else self.a
