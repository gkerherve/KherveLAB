"""Put KherveLAB's MCP server into Claude Desktop's config, or print the line
for Claude Code.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

The config file belongs to another application, so: never rewrite a file
we could not parse, back it up first, write atomically, and touch only our
own entry.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from .mcp_server import SERVER_NAME


def checkout_root() -> str:
    return str(Path(__file__).resolve().parent.parent)


def entry(data: Path, username: str) -> dict:
    if getattr(sys, "frozen", False):
        args = ["--mcp-server", "--data", str(data), "--as", username]
        return {"command": sys.executable, "args": args}
    # `-m khervelab.mcp_server` resolves only when the checkout is importable,
    # and hosts choose the working directory, so PYTHONPATH carries it.
    return {"command": sys.executable,
            "args": ["-m", "khervelab.mcp_server", "--data", str(data), "--as", username],
            "env": {"PYTHONPATH": checkout_root()}}


def snippet(data: Path, username: str) -> str:
    return json.dumps({"mcpServers": {SERVER_NAME: entry(data, username)}}, indent=2)


def cli_command(data: Path, username: str) -> str:
    e = entry(data, username)
    q = lambda s: f'"{s}"' if " " in s else s       # noqa: E731
    env = "".join(f'-e {k}={q(v)} ' for k, v in e.get("env", {}).items())
    return (f"claude mcp add --scope user {SERVER_NAME} {env}-- "
            + " ".join(q(x) for x in [e["command"], *e["args"]]))


def desktop_config_path() -> Path:
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / \
            "Claude/claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def install_desktop(data: Path, username: str, path: Path | None = None) -> Path | None:
    """Add or replace our entry. Returns the backup path, if a file existed."""
    path = path or desktop_config_path()
    cfg, backup = {}, None
    if path.exists():
        text = path.read_text(encoding="utf-8")
        try:
            cfg = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON ({exc}); add KherveLAB by hand") from exc
        if not isinstance(cfg, dict):
            raise ValueError(f"{path} is not a JSON object; add KherveLAB by hand")
        backup = path.with_name(path.name + f".{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(path, backup)
    servers = cfg.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"mcpServers in {path} is not an object; add KherveLAB by hand")
    servers[SERVER_NAME] = entry(data, username)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return backup


def installed(path: Path | None = None) -> bool:
    try:
        cfg = json.loads((path or desktop_config_path()).read_text(encoding="utf-8"))
        return SERVER_NAME in cfg.get("mcpServers", {})
    except (OSError, ValueError, AttributeError):
        return False
