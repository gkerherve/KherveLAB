"""Open files in KherveFitting (and other suite apps).

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path


def default_kfitting_command() -> str:
    if sys.platform == "darwin":
        return "open -a KherveFitting {files}"
    if sys.platform.startswith("win"):
        return r'"C:\Program Files\KherveFitting\KherveFitting.exe" {files}'
    return "KherveFitting {files}"


def command_for(template: str, files: list[Path]) -> list[str]:
    """`{files}` expands to every path as a separate argument, so several
    files open together as one workbook where the app supports it."""
    parts = shlex.split(template, posix=not sys.platform.startswith("win"))
    out: list[str] = []
    for p in parts:
        if p == "{files}":
            out += [str(f) for f in files]
        else:
            out.append(p)
    if "{files}" not in parts:
        out += [str(f) for f in files]
    return out


def open_files(template: str, files: list[Path]) -> subprocess.Popen:
    return subprocess.Popen(command_for(template, files))
