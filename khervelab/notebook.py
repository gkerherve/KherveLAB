"""KherveLAB from a notebook kernel (KherveBook, Jupyter, a plain script).

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

"Find my data, then analyse it" as one gesture::

    from khervelab import notebook as lab
    hits = lab.find("Co 2p, 20 eV pass energy, since March")
    spectra = lab.spectra(hits[0]["path"])        # {"Co 2p": Region with x, y}
    lab.sample("S-2026-0012")                      # lineage, bookings, files

No Qt is imported. The facility working copy and the local data folder
are the ones the desktop app last used (``~/.khervelab/config.json``);
pass ``facility=`` / ``home=`` to override.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .core.config import load_config
from .core.reports import Report, build_report
from .core.samples import children, lineage
from .local.dataindex import DataIndex
from .local.readers import FileInfo, parse_vamas, read_header
from .local.store import default_home

CONFIG_NAME = "config.json"


def remember(facility: Path, home: Path | None = None) -> None:
    """Called by the desktop app so notebooks find the same facility."""
    home = Path(home or default_home())
    home.mkdir(parents=True, exist_ok=True)
    (home / CONFIG_NAME).write_text(json.dumps({"facility": str(facility)}), encoding="utf-8")


def _home(home: Path | str | None) -> Path:
    return Path(home) if home else default_home()


def _facility(facility: Path | str | None, home: Path | str | None) -> Path:
    if facility:
        return Path(facility)
    cfg = _home(home) / CONFIG_NAME
    if not cfg.exists():
        raise FileNotFoundError("No facility configured: open it once in KherveLAB, or pass "
                                "facility=<working copy>")
    return Path(json.loads(cfg.read_text(encoding="utf-8"))["facility"])


def _frame(rows: list[dict]) -> Any:
    try:
        import pandas as pd
        return pd.DataFrame(rows)
    except ImportError:
        return rows


def find(query: str = "", home: Path | str | None = None, facility: Path | str | None = None,
         limit: int = 500, as_frame: bool = True) -> Any:
    """Search the data index; a DataFrame when pandas is installed."""
    insts: list[str] = []
    try:
        insts = list(load_config(_facility(facility, home)).instruments)
    except FileNotFoundError:
        pass
    idx = DataIndex(_home(home) / "index.db")
    try:
        rows = [{"measured": f.acquired, "instrument": f.instrument, "file": f.filename,
                 "regions": f.region_summary(), "sample": f.sample,
                 "booked": bool(f.booking), "kind": f.kind, "path": f.path}
                for f in idx.search(query, insts, limit)]
    finally:
        idx.close()
    return _frame(rows) if as_frame else rows


def spectra(path: Path | str) -> dict[str, Any]:
    """Every region of a VAMAS file with its x/y data (binding energy via
    ``region.binding_energy()``); other formats return their header only."""
    p = Path(path)
    info: FileInfo = parse_vamas(p, with_data=True) if p.suffix.lower() == ".vms" \
        else read_header(p)
    out: dict[str, Any] = {}
    for r in info.regions:
        key, n = r.name, 2
        while key in out:
            key, n = f"{r.name} ({n})", n + 1
        out[key] = r
    return out


def sample(sid: str, home: Path | str | None = None,
           facility: Path | str | None = None) -> dict[str, Any]:
    cfg = load_config(_facility(facility, home))
    if sid not in cfg.samples:
        raise KeyError(sid)
    s = cfg.samples[sid]
    todo, desc = [sid], []
    while todo:
        for c in children(cfg.samples, todo.pop()):
            desc.append(c.id)
            todo.append(c.id)
    idx = DataIndex(_home(home) / "index.db")
    try:
        files = [f.path for f in idx.for_sample(sid, desc)]
    finally:
        idx.close()
    return {"sample": s, "lineage": [x.id for x in lineage(cfg.samples, sid)],
            "derived": desc,
            "bookings": [b for b in cfg.bookings.values() if sid in b.samples],
            "files": files}


def report(start: date, end: date, facility: Path | str | None = None,
           home: Path | str | None = None) -> Report:
    return build_report(load_config(_facility(facility, home)), start, end)
