"""Data index: every measured file, searchable, linked to bookings and samples.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Lives in ``~/.khervelab/index.db`` (SQLite + FTS5), never in the shared
repository: paths and operators on instrument PCs are local facts.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from ..core.samples import SAMPLE_IN_TEXT
from .readers import DATA_EXTENSIONS, read_header

MATCH_BEFORE = timedelta(minutes=15)
MATCH_AFTER = timedelta(minutes=60)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (path TEXT PRIMARY KEY, instrument TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, folder TEXT, instrument TEXT,
    filename TEXT, ext TEXT, size INTEGER, mtime REAL, acquired TEXT, technique TEXT,
    operator TEXT, sample_label TEXT, kind TEXT, notes TEXT, error TEXT,
    booking TEXT DEFAULT '', sample TEXT DEFAULT '', manual INTEGER DEFAULT 0, source TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS files_acquired ON files(acquired);
CREATE INDEX IF NOT EXISTS files_sample ON files(sample);
CREATE TABLE IF NOT EXISTS regions (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE, name TEXT,
    pass_energy REAL, dwell REAL, scans INTEGER, points INTEGER, source TEXT);
CREATE INDEX IF NOT EXISTS regions_file ON regions(file_id);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    filename, path, regions, notes, sample, tokenize = "unicode61 tokenchars '-'");
"""


@dataclass
class IndexedFile:
    id: int
    path: str
    instrument: str
    filename: str
    acquired: datetime | None
    technique: str
    operator: str
    kind: str
    booking: str
    sample: str
    manual: bool
    regions: list[tuple[str, float | None, float | None, int | None]] = field(default_factory=list)
    error: str = ""
    source: str = ""

    def region_summary(self) -> str:
        out = []
        for name, pe, dwell, scans in self.regions:
            bits = [name]
            if pe:
                bits.append(f"PE {pe:g}")
            if scans:
                bits.append(f"×{scans}")
            out.append(" ".join(bits))
        return ", ".join(out)


@dataclass
class ScanReport:
    seen: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    errors: int = 0


@dataclass
class Query:
    text: list[str] = field(default_factory=list)
    pass_energy: float | None = None
    since: date | None = None
    before: date | None = None
    instrument: str = ""
    sample: str = ""
    unbooked: bool = False
    kind: str = ""


_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]


def _parse_when(text: str, today: date) -> date | None:
    t = text.strip().lower()
    try:
        return date.fromisoformat(t)
    except ValueError:
        pass
    if re.fullmatch(r"\d{4}", t):
        return date(int(t), 1, 1)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", t)
    if m:
        return date(int(m[1]), int(m[2]), 1)
    if t in ("yesterday",):
        return today - timedelta(days=1)
    m = re.fullmatch(r"last (week|month|year)", t)
    if m:
        return today - timedelta(days={"week": 7, "month": 31, "year": 365}[m[1]])
    m = re.fullmatch(r"([a-z]+)(?:\s+(\d{4}))?", t)
    if m:
        month = next((i for i, name in enumerate(_MONTHS, 1) if name.startswith(m[1][:3])
                      and len(m[1]) >= 3), None)
        if month:
            year = int(m[2]) if m[2] else (today.year if month <= today.month else today.year - 1)
            return date(year, month, 1)
    return None


def parse_query(text: str, instruments: Iterable[str] = (), today: date | None = None) -> Query:
    """'Co 2p, 20 eV pass energy, since March' -> structured filters + text."""
    today = today or date.today()
    inst_ids = {i.lower() for i in instruments}
    q = Query()
    for part in re.split(r",|;|\band\b", text, flags=re.I):
        p = part.strip()
        low = p.lower()
        if not p:
            continue
        m = (re.fullmatch(r"(\d+(?:\.\d+)?)\s*ev\s*(?:pass energy|pass|pe)", low)
             or re.fullmatch(r"(?:pass energy|pe)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*(?:ev)?", low))
        if m:
            q.pass_energy = float(m.group(1))
            continue
        m = re.fullmatch(r"(?:since|after|from)\s+(.+)", low)
        if m and _parse_when(m.group(1), today):
            q.since = _parse_when(m.group(1), today)
            continue
        m = re.fullmatch(r"(?:before|until)\s+(.+)", low)
        if m and _parse_when(m.group(1), today):
            q.before = _parse_when(m.group(1), today)
            continue
        m = re.fullmatch(r"(?:on|instrument)\s+(\S+)", low)
        if m and m.group(1) in inst_ids:
            q.instrument = m.group(1)
            continue
        if low in inst_ids:
            q.instrument = low
            continue
        if SAMPLE_IN_TEXT.fullmatch(p.upper()):
            q.sample = p.upper()
            continue
        if low in ("unbooked", "no booking", "not booked"):
            q.unbooked = True
            continue
        if low in ("results", "fits", "fitted", "analysis"):
            q.kind = "result"
            continue
        q.text.append(p)
    return q


def _fts_expr(terms: list[str]) -> str:
    out = []
    for t in terms:
        words = re.findall(r"[\w-]+", t)
        if not words:
            continue
        if len(words) > 1:
            # 'Co 2p' as a phrase, or glued as stored for region names ('Co2p')
            out.append(f'("{" ".join(words)}" OR "{"".join(words)}")')
        else:
            out.append(f'"{words[0]}"*')
    return " AND ".join(out)


class DataIndex:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # -- folders ----------------------------------------------------------
    def add_folder(self, folder: Path, instrument: str) -> None:
        self.db.execute("INSERT INTO folders (path, instrument) VALUES (?, ?) ON CONFLICT(path) "
                        "DO UPDATE SET instrument=excluded.instrument",
                        (str(Path(folder).expanduser().resolve()), instrument))
        self.db.commit()

    def remove_folder(self, folder: str) -> None:
        self.db.execute("DELETE FROM folders WHERE path=?", (folder,))
        for (fid,) in self.db.execute("SELECT id FROM files WHERE folder=?", (folder,)).fetchall():
            self._delete(fid)
        self.db.commit()

    def folders(self) -> list[tuple[str, str]]:
        return self.db.execute("SELECT path, instrument FROM folders ORDER BY path").fetchall()

    # -- scanning ---------------------------------------------------------
    def scan(self, tz=timezone.utc, progress=None) -> ScanReport:
        rep = ScanReport()
        for folder, instrument in self.folders():
            seen_paths = set()
            root = Path(folder)
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    if fn.startswith((".", "~$")) or Path(fn).suffix.lower() not in DATA_EXTENSIONS:
                        continue
                    p = Path(dirpath) / fn
                    seen_paths.add(str(p))
                    rep.seen += 1
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    row = self.db.execute("SELECT id, mtime, size FROM files WHERE path=?",
                                          (str(p),)).fetchone()
                    if row and row[1] == st.st_mtime and row[2] == st.st_size:
                        continue
                    self._index_file(p, folder, instrument, st, row[0] if row else None, tz)
                    if row:
                        rep.updated += 1
                    else:
                        rep.added += 1
                    if progress and (rep.added + rep.updated) % 200 == 0:
                        progress(rep)
            gone = [fid for fid, path in self.db.execute(
                "SELECT id, path FROM files WHERE folder=?", (folder,)).fetchall()
                if path not in seen_paths]
            for fid in gone:
                self._delete(fid)
            rep.removed += len(gone)
            self.db.commit()
        rep.errors = self.db.execute("SELECT COUNT(*) FROM files WHERE error != ''").fetchone()[0]
        return rep

    def _index_file(self, p: Path, folder: str, instrument: str, st, fid: int | None, tz) -> None:
        info = read_header(p, tz)
        acquired = info.acquired or datetime.fromtimestamp(st.st_mtime, timezone.utc)
        sample_label = info.sample_label
        vals = (str(p), folder, instrument, p.name, p.suffix.lower(), st.st_size, st.st_mtime,
                acquired.astimezone(timezone.utc).isoformat(timespec="seconds"), info.technique,
                info.operator, sample_label, info.kind, info.notes, info.error)
        if fid is None:
            cur = self.db.execute(
                "INSERT INTO files (path, folder, instrument, filename, ext, size, mtime, acquired, "
                "technique, operator, sample_label, kind, notes, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals)
            fid = cur.lastrowid
        else:
            self.db.execute(
                "UPDATE files SET path=?, folder=?, instrument=?, filename=?, ext=?, size=?, "
                "mtime=?, acquired=?, technique=?, operator=?, sample_label=?, kind=?, notes=?, "
                "error=? WHERE id=?", (*vals, fid))
            self.db.execute("DELETE FROM regions WHERE file_id=?", (fid,))
            self.db.execute("DELETE FROM fts WHERE rowid=?", (fid,))
        for r in info.regions:
            self.db.execute("INSERT INTO regions (file_id, name, pass_energy, dwell, scans, points, "
                            "source) VALUES (?,?,?,?,?,?,?)",
                            (fid, r.name, r.pass_energy, r.dwell, r.scans, r.points, r.source))
        region_text = " ".join(f"{r.name} {r.name.replace(' ', '')}" for r in info.regions)
        self._fts(fid, p.name, str(p), region_text, info.notes, sample_label)

    def _fts(self, fid, filename, path, regions, notes, sample) -> None:
        self.db.execute("DELETE FROM fts WHERE rowid=?", (fid,))
        self.db.execute("INSERT INTO fts (rowid, filename, path, regions, notes, sample) "
                        "VALUES (?,?,?,?,?,?)", (fid, filename, path, regions, notes, sample))

    def _delete(self, fid: int) -> None:
        self.db.execute("DELETE FROM regions WHERE file_id=?", (fid,))
        self.db.execute("DELETE FROM fts WHERE rowid=?", (fid,))
        self.db.execute("DELETE FROM files WHERE id=?", (fid,))

    # -- matching ---------------------------------------------------------
    def match(self, svc) -> int:
        """Link files to bookings (instrument + time) and from there to
        samples; a sample id written in the file or its path wins. Manual
        links are never overwritten. Returns how many links changed."""
        by_inst: dict[str, list] = {}
        for b in svc.bookings():
            by_inst.setdefault(b.instrument, []).append(b)
        changed = 0
        rows = self.db.execute("SELECT id, instrument, acquired, path, sample_label, booking, "
                               "sample, kind, filename FROM files WHERE manual=0").fetchall()
        stems: dict[str, str] = {}
        for fid, inst, acq, path, label, booking, sample, kind, fn in rows:
            when = datetime.fromisoformat(acq) if acq else None
            new_booking = ""
            if when:
                for b in by_inst.get(inst, ()):
                    if b.start - MATCH_BEFORE <= when <= b.end + MATCH_AFTER:
                        new_booking = svc.path_of(b)
                        break
            new_sample = ""
            explicit = SAMPLE_IN_TEXT.findall(f"{path} {label}")
            explicit = [s for s in explicit if s in svc.samples]
            if explicit:
                new_sample = explicit[-1]
            elif new_booking:
                b = svc.cfg.bookings.get(new_booking)
                if b and len(b.samples) == 1:
                    new_sample = b.samples[0]
            if kind == "raw" and new_sample:
                stems[Path(fn).stem.lower()] = new_sample
            if (new_booking, new_sample) != (booking or "", sample or ""):
                self.db.execute("UPDATE files SET booking=?, sample=? WHERE id=?",
                                (new_booking, new_sample, fid))
                changed += 1
        # analysis results (e.g. a KherveFitting workbook saved from a raw
        # file) inherit the sample of the raw file they share a name with
        for fid, fn, sample in self.db.execute(
                "SELECT id, filename, sample FROM files WHERE manual=0 AND kind='result'").fetchall():
            inherited = stems.get(Path(fn).stem.lower(), "")
            if inherited and not sample:
                self.db.execute("UPDATE files SET sample=?, source='derived' WHERE id=?",
                                (inherited, fid))
                changed += 1
        self.db.commit()
        self._refresh_fts_samples()
        return changed

    def _refresh_fts_samples(self) -> None:
        for fid, sample, label in self.db.execute("SELECT id, sample, sample_label FROM files"):
            self.db.execute("UPDATE fts SET sample=? WHERE rowid=?",
                            (f"{sample} {label}".strip(), fid))
        self.db.commit()

    def set_link(self, fid: int, booking: str, sample: str) -> None:
        self.db.execute("UPDATE files SET booking=?, sample=?, manual=1 WHERE id=?",
                        (booking, sample, fid))
        self.db.commit()
        self._refresh_fts_samples()

    def clear_manual(self, fid: int) -> None:
        self.db.execute("UPDATE files SET manual=0 WHERE id=?", (fid,))
        self.db.commit()

    # -- queries ----------------------------------------------------------
    def _rows(self, where: str, args: list, limit: int) -> list[IndexedFile]:
        rows = self.db.execute(
            "SELECT id, path, instrument, filename, acquired, technique, operator, kind, booking, "
            f"sample, manual, error, source FROM files f {where} "
            "ORDER BY acquired DESC LIMIT ?", (*args, limit)).fetchall()
        out = []
        for r in rows:
            f = IndexedFile(r[0], r[1], r[2] or "", r[3], datetime.fromisoformat(r[4]) if r[4]
                            else None, r[5] or "", r[6] or "", r[7] or "", r[8] or "", r[9] or "",
                            bool(r[10]), error=r[11] or "", source=r[12] or "")
            f.regions = self.db.execute("SELECT name, pass_energy, dwell, scans FROM regions "
                                        "WHERE file_id=?", (f.id,)).fetchall()
            out.append(f)
        return out

    def search(self, q: Query | str, instruments: Iterable[str] = (), limit: int = 500
               ) -> list[IndexedFile]:
        if isinstance(q, str):
            q = parse_query(q, instruments)
        clauses, args = [], []
        expr = _fts_expr(q.text)
        if expr:
            clauses.append("f.id IN (SELECT rowid FROM fts WHERE fts MATCH ?)")
            args.append(expr)
        if q.pass_energy is not None:
            clauses.append("f.id IN (SELECT file_id FROM regions WHERE abs(pass_energy - ?) < 0.01)")
            args.append(q.pass_energy)
        if q.since:
            clauses.append("f.acquired >= ?")
            args.append(q.since.isoformat())
        if q.before:
            clauses.append("f.acquired < ?")
            args.append(q.before.isoformat())
        if q.instrument:
            clauses.append("f.instrument = ?")
            args.append(q.instrument)
        if q.sample:
            clauses.append("f.sample = ?")
            args.append(q.sample)
        if q.unbooked:
            clauses.append("f.booking = '' AND f.kind = 'raw'")
        if q.kind:
            clauses.append("f.kind = ?")
            args.append(q.kind)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._rows(where, args, limit)

    def for_sample(self, sid: str, include_descendants: Iterable[str] = ()) -> list[IndexedFile]:
        ids = [sid, *include_descendants]
        marks = ",".join("?" * len(ids))
        return self._rows(f"WHERE f.sample IN ({marks})", ids, 10_000)

    def unbooked(self, limit: int = 1000) -> list[IndexedFile]:
        return self._rows("WHERE f.booking = '' AND f.kind = 'raw'", [], limit)

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
