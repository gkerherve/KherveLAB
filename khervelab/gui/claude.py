"""Manage ▸ Connect to Claude: let Claude run the lab through MCP.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
                             QPlainTextEdit, QPushButton, QVBoxLayout)

from .. import mcp_hosts


class ClaudeDialog(QDialog):
    def __init__(self, conn, me, data_dir: Path, parent=None):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.setWindowTitle("Connect to Claude")
        self.resize(760, 520)
        intro = QLabel(
            "Claude (Desktop or Code) can run the lab for you as a lab manager: read "
            "screenshots of PPMS pages and enter the users, instruments, prices, training and "
            "past bookings they show, import PPMS export files, book, approve and report.<br>"
            "It uses the same rules as the app and cannot delete anything; every change it "
            "makes is listed in <b>mcp-log.jsonl</b> in the lab's data folder. Claude asks "
            "you before each change unless you allow a tool permanently.")
        intro.setWordWrap(True)
        self.who = QComboBox()
        for u in conn.execute("SELECT username, full_name FROM users WHERE role='admin' "
                              "AND status='active' ORDER BY full_name"):
            self.who.addItem(f"{u['full_name']} ({u['username']})", u["username"])
        self.who.setCurrentIndex(max(0, self.who.findData(me["username"])))
        form = QFormLayout()
        form.addRow("Claude acts as", self.who)
        self.status = QLabel()
        form.addRow("Claude Desktop", self.status)
        mono = QFont("Menlo, Consolas, monospace")
        self.json = QPlainTextEdit(readOnly=True)
        self.json.setFont(mono)
        self.cli = QPlainTextEdit(readOnly=True)
        self.cli.setFont(mono)
        self.cli.setMaximumHeight(70)
        add = QPushButton("Add to Claude Desktop")
        add.clicked.connect(self._install)
        copy_cli = QPushButton("Copy Claude Code command")
        copy_cli.clicked.connect(lambda: QGuiApplication.clipboard().setText(
            self.cli.toPlainText()))
        copy_json = QPushButton("Copy JSON")
        copy_json.clicked.connect(lambda: QGuiApplication.clipboard().setText(
            self.json.toPlainText()))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        for b in (add, copy_cli, copy_json):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(QLabel("For Claude Code, run this once in a terminal:"))
        lay.addWidget(self.cli)
        lay.addWidget(QLabel("For other MCP clients, add this to their configuration:"))
        lay.addWidget(self.json, 1)
        lay.addLayout(row)
        self.who.currentIndexChanged.connect(self._show)
        self._show()

    def _show(self, *_):
        u = self.who.currentData() or ""
        self.json.setPlainText(mcp_hosts.snippet(self.data_dir, u))
        self.cli.setPlainText(mcp_hosts.cli_command(self.data_dir, u))
        self.status.setText("connected — restart Claude Desktop after changes"
                            if mcp_hosts.installed() else "not set up yet")

    def _install(self):
        if not self.who.currentData():
            QMessageBox.warning(self, "Connect to Claude", "The lab has no active manager.")
            return
        try:
            backup = mcp_hosts.install_desktop(self.data_dir, self.who.currentData())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Connect to Claude", str(exc))
            return
        self._show()
        QMessageBox.information(
            self, "Connect to Claude",
            f"Added KherveLAB to {mcp_hosts.desktop_config_path()}."
            + (f"\nThe previous file is kept as {backup.name}." if backup else "")
            + "\n\nQuit and reopen Claude Desktop to use it.")
