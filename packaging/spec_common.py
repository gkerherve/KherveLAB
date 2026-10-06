"""What KherveLAB.spec (Windows) and KherveLABMAC.spec (macOS) share.

Both specs import this, so the module list, the data files and the
excludes cannot drift apart between the two platforms.

Copyright (C) 2026 Gwilherm Kerherve
Licensed under the GNU General Public License v3.0 or later (see LICENSE).
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "KherveLAB"
ENTRY = str(ROOT / "KherveLAB.py")
#: Windows only: the console companion that runs the web pages (--serve)
SERVER_ENTRY = str(ROOT / "KherveLAB-server.py")
VERSION_FILE = ROOT / "khervelab" / "VERSION"

#: Other Qt bindings and dev-only packages. KherveLAB is PyQt6-only; two Qt
#: bindings in one bundle break at start-up. No matplotlib/numpy either:
#: charts are drawn by khervelab/charts.py.
EXCLUDES = [
    "PySide6", "PySide2", "PyQt5", "shiboken6", "shiboken2", "tkinter", "_tkinter",
    "matplotlib", "numpy", "pandas", "scipy", "IPython", "jedi", "notebook",
    "jupyter_client", "pytest", "_pytest", "pytestqt", "setuptools", "pip",
    # PyQt6 modules the app never touches (keep the bundle small)
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.Qt3DCore",
    "PyQt6.QtQuick", "PyQt6.QtQml", "PyQt6.QtMultimedia", "PyQt6.QtBluetooth",
    "PyQt6.QtPositioning", "PyQt6.QtSql", "PyQt6.QtNetwork", "PyQt6.QtPdf",
]


def _base_version() -> str:
    """``__version__`` (<major>.<minor>) read from the source, without
    importing the package (which would read a stale VERSION file)."""
    text = (ROOT / "khervelab" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__ = "([0-9.]+)"', text, re.M).group(1)


def stamp_version():
    """Write khervelab/VERSION from git (a frozen build has no .git) and
    return (full, short): '0.28.N+sha' and '0.28.N'. Refuses the '.0'
    fallback a shallow clone would give."""
    sys.path.insert(0, str(ROOT / "khervelab"))
    from _version import git_version
    base = _base_version()
    full = git_version(base, ROOT)
    if full == f"{base}.0":
        raise SystemExit("could not read the version from git "
                         "(shallow clone? use fetch-depth: 0)")
    VERSION_FILE.write_text(full, encoding="ascii")
    return full, full.split("+")[0]


def icons():
    """(ico, icns) under build/, rendered from the app's own mark."""
    ico = ROOT / "build" / f"{APP_NAME}.ico"
    icns = ROOT / "build" / f"{APP_NAME}.icns"
    if not (ico.is_file() and icns.is_file()):
        sys.path.insert(0, str(ROOT / "packaging"))
        from make_icons import build_icons
        build_icons(ROOT / "build")
    return str(ico), str(icns)


def analysis_inputs():
    """(datas, binaries, hiddenimports) for Analysis()."""
    from PyInstaller.utils.hooks import collect_data_files, collect_submodules

    datas = [
        # Flask finds these next to khervelab/web.py
        (str(ROOT / "khervelab" / "templates"), "khervelab/templates"),
        (str(ROOT / "khervelab" / "static"), "khervelab/static"),
        (str(ROOT / "LICENSE"), "."),
        (str(VERSION_FILE), "khervelab"),
    ]
    # The GUI, the MCP server and the exports are imported lazily (inside
    # functions), so static analysis alone would leave some out.
    hiddenimports = collect_submodules("khervelab") + [
        "waitress", "flask", "jinja2", "werkzeug",
        "openpyxl", "openpyxl.cell._writer", "openpyxl.chart",
        "reportlab.graphics.barcode", "reportlab.pdfbase._fontdata",
    ] + collect_submodules("reportlab.pdfbase") + collect_submodules("waitress")
    # zoneinfo reads the lab's time zone from tzdata where the OS has no
    # database (Windows); bundle it everywhere so every build behaves alike.
    datas += collect_data_files("tzdata")
    hiddenimports += collect_submodules("tzdata")
    return datas, [], hiddenimports
