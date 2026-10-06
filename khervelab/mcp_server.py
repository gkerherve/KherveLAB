"""MCP (Model Context Protocol) stdio server: Claude runs the lab as its manager.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Claude Desktop or Claude Code launches this as a subprocess and speaks
JSON-RPC 2.0 over stdin/stdout, one message per line. Unlike KherveTeX it
needs no bridge into a running window: the lab lives in lab.db, which the
app and the web pages already share (WAL), so the app shows Claude's
changes at its next refresh. It imports no Qt and no extra package:

    python -m khervelab.mcp_server --data ~/KherveLAB-data --as manager
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from . import __version__, db, mcp_tools

SERVER_NAME = "khervelab"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_DATA = Path.home() / "KherveLAB-data"

INSTRUCTIONS = """\
KherveLAB books the instruments of one research lab. These tools act as the
lab manager on the lab's own database.

- Call lab_overview first: it names the instruments, the rate categories and
  the currency that every other tool expects.
- Users and instruments can be given by id, username/email or exact name.
- Times are lab-local, YYYY-MM-DDTHH:MM.
- Moving data from PPMS: with export files, use preview_ppms_import and then
  import_ppms. From screenshots of PPMS pages, read the values off the image
  and use add_users, add_instrument/update_instrument, set_rates, set_training
  and record_past_bookings (amount = what PPMS charged). Prefer one call with
  a list over many single calls.
- Before a change of more than a handful of records, show the person what you
  read from the screenshot and what you are about to create, and wait for
  their go-ahead. Never guess a value you cannot read; ask instead.
- Nothing can be deleted from here: retire instruments (active=0), disable
  accounts, cancel bookings. Each change is logged in mcp-log.jsonl.
"""


def acting_user(conn, username: str | None):
    if username:
        u = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if u is None or u["role"] != "admin" or u["status"] != "active":
            raise SystemExit(f"KherveLAB MCP: {username!r} is not an active lab manager")
        return u
    u = conn.execute("SELECT * FROM users WHERE role='admin' AND status='active' "
                     "ORDER BY id LIMIT 1").fetchone()
    if u is None:
        raise SystemExit("KherveLAB MCP: the lab has no manager yet; set it up in the app")
    return u


class Server:
    def __init__(self, data: Path, username: str | None = None):
        path = data / "lab.db"
        if not path.exists():
            raise SystemExit(f"KherveLAB MCP: no lab at {path}")
        self.conn = db.connect(path)
        db.init(self.conn)
        self.username = acting_user(self.conn, username)["username"]
        self.log = data / "mcp-log.jsonl"

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:                       # a notification: nothing to answer
            return None
        try:
            result = self._dispatch(method, msg.get("params") or {})
        except _RpcError as exc:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": exc.code, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _dispatch(self, method, params):
        if method == "initialize":
            asked = params.get("protocolVersion")
            return {"protocolVersion": asked if asked in SUPPORTED_PROTOCOLS
                    else SUPPORTED_PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "title": "KherveLAB",
                                   "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": mcp_tools.catalogue()}
        if method == "tools/call":
            name = params.get("name", "")
            if name not in mcp_tools.BY_NAME:
                raise _RpcError(-32602, f"unknown tool {name!r}")
            # Re-read the manager each call: the account may have been
            # changed in the app since the server started.
            try:
                me = acting_user(self.conn, self.username)
            except SystemExit as exc:
                return _text(str(exc), error=True)
            try:
                out = mcp_tools.call(self.conn, me, name, params.get("arguments") or {},
                                     self.log)
            except mcp_tools.ToolError as exc:
                return _text(str(exc), error=True)
            except Exception:                  # a bug must not kill the session
                traceback.print_exc(file=sys.stderr)
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                return _text("KherveLAB hit an internal error; nothing was changed. "
                             + traceback.format_exc(limit=1).strip().splitlines()[-1],
                             error=True)
            return _text(json.dumps(out, indent=1, default=str, ensure_ascii=False))
        raise _RpcError(-32601, f"method not found: {method}")


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _text(s: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": s}], "isError": error}


def serve(server: Server, stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "parse error"}}
        else:
            batch = msg if isinstance(msg, list) else [msg]
            replies = [r for r in (server.handle(m) for m in batch if isinstance(m, dict)) if r]
            if not replies:
                continue
            reply = replies if isinstance(msg, list) else replies[0]
        stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        stdout.flush()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="KherveLAB MCP server (stdio)")
    p.add_argument("--data", type=Path, default=DEFAULT_DATA, help="folder holding lab.db")
    p.add_argument("--as", dest="username", help="the lab manager Claude acts as "
                   "(default: the first manager)")
    a = p.parse_args(argv)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    serve(Server(a.data.expanduser(), a.username))


if __name__ == "__main__":
    main()
