"""Application entry point.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    from PyQt6.QtCore import QSettings, QTimer
    from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

    from . import __app_name__
    from .core.repo import FacilityRepo, RepoError
    from .core.yamlio import ConfigError
    from .ui import theme
    from .ui.dialogs import SetupDialog
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setOrganizationName("Kherve")
    settings = QSettings("Kherve", __app_name__)
    theme.apply(app, settings.value("dark", False, type=bool))

    store, store_error = None, ""
    try:
        from .local.store import LocalStore
        store = LocalStore()
    except Exception as exc:  # no keyring backend, unreadable database...
        store_error = str(exc)

    index = None
    try:
        from .local.dataindex import DataIndex
        from .local.store import default_home
        index = DataIndex(default_home() / "index.db")
    except Exception:
        index = None

    repo = None
    path = sys.argv[1] if len(sys.argv) > 1 else settings.value("repo_path", "")
    if path:
        try:
            repo = FacilityRepo(Path(path).expanduser())
        except RepoError:
            repo = None
    while True:
        if repo is None:
            dlg = SetupDialog()
            if dlg.exec() != QDialog.DialogCode.Accepted or dlg.repo is None:
                return 0
            repo = dlg.repo
        try:
            win = MainWindow(repo, settings, store, store_error, index)
        except ConfigError as exc:
            QMessageBox.warning(None, __app_name__, f"The facility repository has an invalid "
                                                    f"file:\n\n{exc}")
            repo = None
            continue
        break
    settings.setValue("repo_path", str(repo.path))
    win.show()
    QTimer.singleShot(800, win.startup_checks)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
