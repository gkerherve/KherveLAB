# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for KherveLAB on WINDOWS.
#
#   .venv\Scripts\pip install pyinstaller
#   .venv\Scripts\pyinstaller KherveLAB.spec --noconfirm
#     -> dist\KherveLAB\KherveLAB.exe   (one-folder build)
#
# One-folder rather than one-file: it starts instantly (nothing to unpack
# to %TEMP% on every launch) and the MCP host can re-run the same exe with
# --mcp-server without waiting for an extraction. Zip dist\KherveLAB or
# wrap it in an installer to distribute it.
#
# The macOS build is KherveLABMAC.spec; both share packaging/spec_common.py.
#
# Copyright (C) 2026 Gwilherm Kerherve. GPL-3.0-or-later.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH) / "packaging"))
import spec_common as common  # noqa: E402

FULL_VERSION, SHORT_VERSION = common.stamp_version()
ICO, _ICNS = common.icons()
datas, binaries, hiddenimports = common.analysis_inputs()
print(f"Building {common.APP_NAME} v{FULL_VERSION} for Windows")


def _version_resource():
    """File > Properties > Details: product name, version, copyright.
    Windows-only module; None elsewhere (a dry run of this spec on a Mac)."""
    if sys.platform != "win32":
        return None
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo)
    nums = tuple(int(n) for n in SHORT_VERSION.split(".")) + (0,)
    nums = (nums + (0, 0, 0, 0))[:4]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Gwilherm Kerherve"),
                StringStruct("FileDescription",
                             "KherveLAB - instrument booking for one lab"),
                StringStruct("FileVersion", FULL_VERSION),
                StringStruct("InternalName", "KherveLAB"),
                StringStruct("LegalCopyright",
                             "Copyright (C) 2026 Gwilherm Kerherve. GPL-3.0-or-later."),
                StringStruct("OriginalFilename", "KherveLAB.exe"),
                StringStruct("ProductName", "KherveLAB"),
                StringStruct("ProductVersion", FULL_VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ])


a = Analysis(
    [common.ENTRY],
    pathex=[str(common.ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=common.EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KherveLAB",
    icon=ICO,
    version=_version_resource(),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX breaks Qt DLLs on some machines
    console=False,              # GUI app - no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# KherveLAB.exe is windowed, so `KherveLAB.exe --serve` cannot print the
# address to hand out. KherveLAB-server.exe is the same code as a console
# program that always serves; it shares the _internal folder.
sa = Analysis(
    [common.SERVER_ENTRY],
    pathex=[str(common.ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=common.EXCLUDES + ["PyQt6"],
    noarchive=False,
)
spyz = PYZ(sa.pure)
server_exe = EXE(
    spyz,
    sa.scripts,
    [],
    exclude_binaries=True,
    name="KherveLAB-server",
    icon=ICO,
    version=_version_resource(),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    server_exe,
    sa.binaries,
    sa.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="KherveLAB",
)
