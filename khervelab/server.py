"""Run KherveLAB on the lab computer: `python KherveLAB.py`.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import argparse
import socket
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .web import create_app

DEFAULT_DATA = Path.home() / "KherveLAB-data"


def lan_address() -> str:
    """This computer's address on the local network (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="KherveLAB: instrument booking for one lab")
    p.add_argument("--data", type=Path, default=DEFAULT_DATA,
                   help=f"folder holding lab.db (default {DEFAULT_DATA})")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0",
                   help="0.0.0.0 lets other computers connect; 127.0.0.1 keeps it on this one")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    args = p.parse_args(argv)

    app = create_app(args.data)
    local = f"http://localhost:{args.port}"
    print(f"KherveLAB {__version__}")
    print(f"  data:          {args.data / 'lab.db'}")
    print(f"  on this PC:    {local}")
    if args.host == "0.0.0.0":
        print(f"  on the network: http://{lan_address()}:{args.port}   (give this address to users)")
    print("  stop with Ctrl+C")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(local)).start()
    from waitress import serve
    serve(app, host=args.host, port=args.port, threads=8)


if __name__ == "__main__":
    main()
