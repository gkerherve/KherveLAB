"""Start the desktop app: setup on first run, log in, then the schedule.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog

from .. import __app_name__, db
from . import theme
from .admin import NetworkServer
from .dialogs import LoginDialog, SetupDialog
from .main_window import MainWindow


def run(data_dir: Path) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setOrganizationName("Kherve")
    settings = QSettings("Kherve", __app_name__)
    theme.apply(app, settings.value("dark", False, type=bool))
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(data_dir / "lab.db")
    db.init(conn)
    server = NetworkServer(data_dir)

    user = None
    if not conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        setup = SetupDialog(conn)
        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0
        user = conn.execute("SELECT * FROM users WHERE id=?", (setup.user_id,)).fetchone()

    state = {"window": None}

    def sign_in(u=None):
        if u is None:
            login = LoginDialog(conn)
            if login.exec() != QDialog.DialogCode.Accepted:
                server.stop()
                app.quit()
                return
            u = login.user
        win = MainWindow(conn, u, data_dir, settings, server)
        win.logoutRequested.connect(lambda: (win.hide(), win.deleteLater(), sign_in()))
        state["window"] = win
        win.show()

    sign_in(user)
    if state["window"] is None:
        return 0
    code = app.exec()
    server.stop()
    return code
