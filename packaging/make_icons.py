"""Render the KherveLAB mark into a Windows .ico and a macOS .icns.

The mark is drawn at runtime by ``khervelab.gui.icon.app_icon_pixmap`` (no
image files ship with the app), so the packaging icons are rendered from
the same code instead of being committed:

    python packaging/make_icons.py            # -> build/KherveLAB.ico + .icns

Both containers are written by hand around PNG payloads — the .ico
directory format and the .icns chunk format are a few bytes each — so the
build needs neither Pillow nor ``iconutil`` and runs on either OS. The
specs call ``build_icons()`` themselves when the files are missing.

Copyright (C) 2026 Gwilherm Kerherve
Licensed under the GNU General Public License v3.0 or later (see LICENSE).
"""

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
#: icns chunk type -> pixel size (PNG payloads, macOS 10.7+)
ICNS_CHUNKS = (("icp4", 16), ("icp5", 32), ("ic11", 32), ("icp6", 64),
               ("ic12", 64), ("ic07", 128), ("ic08", 256), ("ic13", 256),
               ("ic09", 512), ("ic14", 512), ("ic10", 1024))


def _png_bytes(size):
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from khervelab.gui.icon import app_icon_pixmap
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    app_icon_pixmap(size).save(buf, "PNG")
    buf.close()
    return bytes(data)


def _ensure_qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from PyQt6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


def write_ico(path, sizes=ICO_SIZES):
    pngs = [(s, _png_bytes(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, png in pngs:
        dim = 0 if size >= 256 else size          # 0 means 256 in an .ico
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    Path(path).write_bytes(header + entries + blobs)
    return path


def write_icns(path):
    cache, body = {}, b""
    for kind, size in ICNS_CHUNKS:
        png = cache.setdefault(size, _png_bytes(size))
        body += kind.encode("ascii") + struct.pack(">I", len(png) + 8) + png
    Path(path).write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
    return path


def build_icons(out_dir=BUILD):
    """Write KherveLAB.ico and KherveLAB.icns; returns their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    app = _ensure_qt()  # noqa: F841 — keeps the QGuiApplication alive
    ico = write_ico(out_dir / "KherveLAB.ico")
    icns = write_icns(out_dir / "KherveLAB.icns")
    return ico, icns


if __name__ == "__main__":
    for p in build_icons(Path(sys.argv[1]) if len(sys.argv) > 1 else BUILD):
        print(p)
