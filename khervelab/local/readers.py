"""Header readers for instrument data files, used by the data index.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

KherveFitting's importers are bound to its PySide6 window and load whole
spectra into its grid; importing them here would pull PySide6 into a PyQt6
process. The index needs headers only, so these readers parse just what
it stores (ISO 14976 VAMAS block headers; KherveFitting workbook sheets)
and never load the ordinate values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_EXTENSIONS = {".vms", ".xlsx", ".xls", ".spe", ".avg", ".vgd", ".sdp", ".mrs", ".npl",
                   ".itx", ".ibw", ".h5", ".hdf5", ".csv", ".txt", ".dat", ".xy", ".brml",
                   ".raw", ".spc", ".pro", ".kal"}
TECHNIQUE_BY_EXT = {".vms": "XPS", ".spe": "XPS", ".avg": "XPS", ".vgd": "XPS", ".sdp": "XPS",
                    ".mrs": "XPS", ".npl": "XPS", ".brml": "XRD", ".raw": "XRD", ".xy": "XRD"}


@dataclass
class Region:
    name: str                       # e.g. "Co 2p"
    pass_energy: float | None = None
    dwell: float | None = None      # s per point
    scans: int | None = None
    start: float | None = None
    step: float | None = None
    points: int | None = None
    source: str = ""
    acquired: datetime | None = None
    sample_label: str = ""
    source_energy: float | None = None
    abscissa: str = ""
    x: list[float] | None = None      # filled only when data is requested
    y: list[float] | None = None

    def binding_energy(self) -> list[float] | None:
        """Binding-energy axis for a kinetic-energy scan (BE = hν − KE)."""
        if self.x is None:
            return None
        if self.abscissa.lower().startswith("binding") or not self.source_energy:
            return list(self.x)
        return [self.source_energy - v for v in self.x]


@dataclass
class FileInfo:
    technique: str = ""
    operator: str = ""
    acquired: datetime | None = None
    regions: list[Region] = field(default_factory=list)
    sample_label: str = ""
    kind: str = "raw"               # raw | result
    notes: str = ""
    error: str = ""


def norm_region(name: str) -> str:
    """'Co2p', 'Co 2p', 'co 2p3/2' -> 'Co 2p' (element, then level)."""
    n = name.strip()
    m = re.match(r"^([A-Z][a-z]?)\s*(\d[spdf])(?:\s*\d/\d)?\b", n)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.match(r"^([A-Za-z][a-z]?)\s*(\d[spdf])", n)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2).lower()}"
    return n


# -- VAMAS (ISO 14976) -----------------------------------------------------------

_MAP_MODES = ("MAP", "MAPDP", "MAPSV", "MAPSVDP")
_SPUTTER_MODES = ("MAPDP", "MAPSVDP", "SDP", "SDPSV")


class VamasError(ValueError):
    pass


class _Lines:
    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.i = 0

    def next(self) -> str:
        if self.i >= len(self.lines):
            raise VamasError("unexpected end of file")
        v = self.lines[self.i].rstrip("\r")
        self.i += 1
        return v

    def int(self) -> int:
        return int(float(self.next()))

    def float(self) -> float | None:
        v = float(self.next())
        return None if v >= 1e37 else v

    def skip(self, n: int) -> None:
        self.i += n


def parse_vamas(path: Path, tz=timezone.utc, with_data: bool = False) -> FileInfo:
    """Block headers of a VAMAS file (inclusion list must be empty, which
    is what CasaXPS, Avantage and KherveFitting write)."""
    t = _Lines(Path(path).read_text(encoding="latin-1"))
    if not t.next().startswith("VAMAS Surface Chemical Analysis Standard Data Transfer Format"):
        raise VamasError("not a VAMAS file")
    t.next()                       # institution
    t.next()                       # instrument model
    operator = t.next().strip()
    t.next()                       # experiment id
    t.skip(t.int())                # comment lines
    exp_mode = t.next().strip()
    scan_mode = t.next().strip()
    if exp_mode in ("MAP", "MAPDP", "NORM", "SDP"):
        t.int()                    # number of spectral regions
    if exp_mode in ("MAP", "MAPDP"):
        t.skip(3)                  # analysis positions, discrete x and y coordinates
    n_exp_vars = t.int()
    t.skip(2 * n_exp_vars)
    n_incl = t.int()
    if n_incl != 0:
        raise VamasError("block inclusion lists are not supported")
    t.skip(t.int())                # manually entered items
    n_future_exp = t.int()
    n_future_block = t.int()
    t.skip(n_future_exp)
    n_blocks = t.int()
    info = FileInfo(technique="", operator="" if operator == "Not Specified" else operator)
    for _ in range(n_blocks):
        block_id = t.next().strip()
        sample_id = t.next().strip()
        y, mo, d, h, mi, s = (t.int() for _ in range(6))
        off = t.float()
        try:
            acq = datetime(y, mo, d, h, mi, min(s, 59),
                           tzinfo=timezone(timedelta(hours=off)) if off is not None else tz)
        except ValueError:
            acq = None
        t.skip(t.int())            # block comments
        technique = t.next().strip()
        if exp_mode in _MAP_MODES:
            t.skip(2)              # x, y coordinates
        t.skip(n_exp_vars)
        source = t.next().strip()
        if exp_mode in _SPUTTER_MODES or technique in ("FABMS", "FABMS energy spec", "ISS",
                                                      "SIMS", "SIMS energy spec", "SNMS",
                                                      "SNMS energy spec"):
            t.skip(3)              # sputtering ion: species, charge, energy... (per spec)
        source_energy = t.float()
        t.float()                  # source strength
        t.skip(2)                  # beam width x, y
        if exp_mode in _MAP_MODES:
            t.skip(2)              # field of view
        if exp_mode in ("MAPSV", "MAPSVDP", "SEM"):
            t.skip(6)              # linescan coordinates
        t.skip(2)                  # polar and azimuth angle of incidence
        t.next()                   # analyser mode
        pass_energy = t.float()
        if technique == "AES diff":
            t.skip(1)
        t.skip(5)                  # magnification, work function, bias, analysis width x, y
        t.skip(2)                  # take-off polar, azimuth
        species = t.next().strip()
        transition = t.next().strip()
        t.next()                   # charge of detected particle
        start = step = None
        abscissa = ""
        if scan_mode == "REGULAR":
            abscissa = t.next().strip()
            t.next()               # units
            start = t.float()
            step = t.float()
        else:
            raise VamasError(f"scan mode {scan_mode} not supported")
        n_corr = t.int()
        t.skip(2 * n_corr)
        t.next()                   # signal mode
        dwell = t.float()
        scans = t.int()
        t.float()                  # signal time correction
        if technique in ("AES diff", "AES dir", "EDX", "ELS", "UPS", "XPS", "XRF") and \
                exp_mode in _SPUTTER_MODES:
            t.skip(7)              # sputtering source details
        t.skip(3)                  # sample normal tilt, azimuth, rotation
        n_add = t.int()
        t.skip(3 * n_add)
        t.skip(n_future_block)
        n_values = t.int()
        t.skip(2 * n_corr)         # min, max per corresponding variable
        xs = ys = None
        if with_data:
            values = [float(t.next()) for _ in range(n_values)]
            ys = values[0::max(1, n_corr)]
            xs = [start + i * step for i in range(len(ys))] if start is not None and step else None
        else:
            t.skip(n_values)
        blank = ("", "None", "Not Specified")
        if species not in blank:
            name = norm_region(species if transition in blank else f"{species} {transition}")
        else:
            name = norm_region(block_id)
        if sample_id in blank:
            sample_id = ""
        info.regions.append(Region(name, pass_energy, dwell, scans, start, step,
                                   n_values // max(1, n_corr), source, acq, sample_id,
                                   source_energy, abscissa, xs, ys))
        info.technique = info.technique or technique
    acquired = [r.acquired for r in info.regions if r.acquired]
    info.acquired = min(acquired) if acquired else None
    labels = {r.sample_label for r in info.regions if r.sample_label}
    info.sample_label = ", ".join(sorted(labels))
    return info


# -- KherveFitting workbooks ------------------------------------------------------

_NON_REGION_SHEETS = {"experimental description", "results table", "summary", "sheet",
                      "sheet1", "notes", "info"}


def parse_kfitting_xlsx(path: Path) -> FileInfo:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        names = wb.sheetnames
        regions = [Region(norm_region(n)) for n in names
                   if n.strip().lower() not in _NON_REGION_SHEETS
                   and re.match(r"^[A-Z][a-z]?\s*\d[spdf]|^(Survey|Wide|VB|Valence)", n.strip(),
                                re.I)]
        has_results = any(n.strip().lower() == "results table" for n in names)
        notes = ""
        if "Experimental description" in names:
            ws = wb["Experimental description"]
            cells = []
            for row in ws.iter_rows(min_row=1, max_row=20, max_col=4, values_only=True):
                cells += [str(c) for c in row if c not in (None, "")]
            notes = " ".join(cells)[:500]
    finally:
        wb.close()
    if not regions:
        raise ValueError("no core-level sheets")
    return FileInfo(technique="XPS", regions=regions, kind="result" if has_results else "raw",
                    notes=notes)


def read_header(path: Path, tz=timezone.utc) -> FileInfo:
    ext = path.suffix.lower()
    try:
        if ext == ".vms":
            return parse_vamas(path, tz)
        if ext == ".xlsx":
            return parse_kfitting_xlsx(path)
    except Exception as exc:  # unreadable header: still index the file by name and date
        return FileInfo(technique=TECHNIQUE_BY_EXT.get(ext, ""), error=str(exc)[:200])
    return FileInfo(technique=TECHNIQUE_BY_EXT.get(ext, ""))
