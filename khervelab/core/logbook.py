"""Instrument logbook: usage, faults, repairs, calibrations, consumables.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

One YAML file per instrument per month (``logs/<inst>/<YYYY-MM>.yaml``),
appended to, so files stay small and diffs readable. Derived state (fault
status, consumable hours, maintenance due dates) is always recomputed from
the entries; nothing derived is stored.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Booking, Instrument
from .yamlio import ConfigError, dump_yaml, load_yaml

ENTRY_TYPES = ("usage", "fault", "fault_update", "repair", "calibration", "consumable_change",
               "bake_out", "maintenance", "correction", "note")
FAULT_STATES = ("open", "in_progress", "resolved")
COUNTED_KINDS = ("measurement", "training")


@dataclass(frozen=True)
class LogEntry:
    id: str
    instrument: str
    type: str
    time: datetime
    user: str = ""
    text: str = ""
    hours: float = 0.0             # usage / correction
    consumable: str = ""           # consumable_change / correction
    task: str = ""                 # maintenance task completed
    fault: str = ""                # fault id (fault, fault_update, repair)
    status: str = ""               # fault state after this entry
    blocking: bool = False
    back: date | None = None       # expected back in service

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "type": self.type,
                             "time": self.time.isoformat(timespec="minutes")}
        for k in ("user", "text", "consumable", "task", "fault", "status"):
            v = getattr(self, k)
            if v:
                d[k] = v
        if self.hours:
            d["hours"] = round(self.hours, 3)
        if self.blocking:
            d["blocking"] = True
        if self.back:
            d["back"] = self.back.isoformat()
        return d


def new_id() -> str:
    return secrets.token_hex(4)


def log_path(instrument: str, when: datetime, tz) -> str:
    t = when.astimezone(tz)
    return f"logs/{instrument}/{t:%Y-%m}.yaml"


def _entry(d: dict, instrument: str, path: Path, i: int) -> LogEntry:
    key = f"entries[{i}]"
    if not isinstance(d, dict):
        raise ConfigError(path, "expected a mapping", key)
    typ = d.get("type")
    if typ not in ENTRY_TYPES:
        raise ConfigError(path, f"type must be one of {', '.join(ENTRY_TYPES)}", f"{key}.type")
    try:
        t = d["time"]
        t = datetime.fromisoformat(t.isoformat() if isinstance(t, datetime) else str(t))
    except (KeyError, ValueError):
        raise ConfigError(path, "missing or invalid time", f"{key}.time") from None
    if t.tzinfo is None:
        raise ConfigError(path, "time must carry a UTC offset", f"{key}.time")
    status = str(d.get("status", "") or "")
    if status and status not in FAULT_STATES:
        raise ConfigError(path, f"status must be one of {', '.join(FAULT_STATES)}", f"{key}.status")
    back = d.get("back")
    try:
        back = date.fromisoformat(str(back)) if back else None
    except ValueError:
        raise ConfigError(path, "back must be a date", f"{key}.back") from None
    try:
        hours = float(d.get("hours", 0) or 0)
    except (TypeError, ValueError):
        raise ConfigError(path, "hours must be a number", f"{key}.hours") from None
    return LogEntry(id=str(d.get("id") or f"{path.stem}-{i}"), instrument=instrument, type=typ,
                    time=t, user=str(d.get("user", "") or ""), text=str(d.get("text", "") or ""),
                    hours=hours, consumable=str(d.get("consumable", "") or ""),
                    task=str(d.get("task", "") or ""), fault=str(d.get("fault", "") or ""),
                    status=status, blocking=bool(d.get("blocking", False)), back=back)


def load_logs(root: Path) -> list[LogEntry]:
    out: list[LogEntry] = []
    for p in sorted((root / "logs").glob("*/*.yaml")):
        data = load_yaml(p) or {}
        if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
            raise ConfigError(p, "expected 'entries:' with a list", "entries")
        for i, d in enumerate(data.get("entries") or []):
            out.append(_entry(d, p.parent.name, p, i))
    return sorted(out, key=lambda e: e.time)


def month_file_text(existing: str | None, entry: LogEntry) -> str:
    from .yamlio import loads_yaml
    data = loads_yaml(existing) if existing else None
    if not isinstance(data, dict):
        data = {"instrument": entry.instrument, "entries": []}
    data.setdefault("entries", [])
    data["entries"].append(entry.to_dict())
    return dump_yaml(data)


# -- derived state ---------------------------------------------------------------

@dataclass
class Fault:
    id: str
    instrument: str
    opened: datetime
    text: str
    status: str = "open"
    blocking: bool = False
    back: date | None = None
    resolution: str = ""
    history: list[LogEntry] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.status != "resolved"


def faults(entries: Iterable[LogEntry], instrument: str | None = None) -> list[Fault]:
    out: dict[str, Fault] = {}
    for e in entries:
        if instrument and e.instrument != instrument:
            continue
        if e.type == "fault":
            fid = e.fault or e.id
            out[fid] = Fault(fid, e.instrument, e.time, e.text, e.status or "open", e.blocking,
                             e.back, history=[e])
        elif e.type in ("fault_update", "repair") and e.fault in out:
            f = out[e.fault]
            f.history.append(e)
            if e.status:
                f.status = e.status
            if e.back:
                f.back = e.back
            if e.type == "fault_update" and e.blocking != f.blocking and e.status != "resolved":
                f.blocking = e.blocking
            if e.status == "resolved" and e.text:
                f.resolution = e.text
    return sorted(out.values(), key=lambda f: f.opened)


def open_faults(entries: Iterable[LogEntry], instrument: str | None = None) -> list[Fault]:
    return [f for f in faults(entries, instrument) if f.is_open]


def downtime(entries: Iterable[LogEntry], instrument: str, tz) -> list[tuple[datetime, datetime | None, str]]:
    """Windows in which new bookings are blocked: open blocking faults, from
    when they were reported to the expected back date (or indefinitely)."""
    out = []
    for f in open_faults(entries, instrument):
        if f.blocking:
            end = (datetime(f.back.year, f.back.month, f.back.day, tzinfo=tz)
                   if f.back else None)
            out.append((f.opened, end, f.text))
    return out


@dataclass
class Counter:
    consumable: str
    used_hours: float
    limit_hours: float
    warn_at: float
    since: datetime | None

    @property
    def level(self) -> str:
        if self.used_hours >= self.limit_hours:
            return "limit"
        if self.used_hours >= self.warn_at:
            return "warn"
        return "ok"


def _hours(b: Booking, upto: datetime) -> float:
    end = min(b.end, upto)
    if end <= b.start:
        return 0.0
    return (end.astimezone(timezone.utc) - b.start.astimezone(timezone.utc)).total_seconds() / 3600


def counters(inst: Instrument, entries: list[LogEntry], bookings: Iterable[Booking],
             now: datetime) -> list[Counter]:
    """Hours on each consumable since its last change: booked time already
    elapsed, plus logged usage, plus manual corrections."""
    mine = [e for e in entries if e.instrument == inst.id]
    books = [b for b in bookings if b.instrument == inst.id and b.kind in COUNTED_KINDS]
    out = []
    for c in inst.consumables:
        since = None
        logged = 0.0
        for e in mine:  # in logged order, so an entry in the same minute as a change counts after it
            if e.type == "consumable_change" and e.consumable == c.name:
                since, logged = e.time, 0.0
            elif e.type == "usage" or (e.type == "correction" and e.consumable == c.name):
                logged += e.hours
        used = logged + sum(_hours(b, now) - (_hours(b, since) if since else 0.0) for b in books)
        out.append(Counter(c.name, max(0.0, used), c.limit_hours, c.warn_at, since))
    return out


@dataclass
class Due:
    task: str
    interval_days: int
    last: datetime | None
    due: date

    def days_left(self, today: date) -> int:
        return (self.due - today).days


MAINTENANCE_TYPES = ("maintenance", "calibration", "bake_out", "repair")


def maintenance_due(inst: Instrument, entries: list[LogEntry], today: date, tz) -> list[Due]:
    out = []
    for m in inst.maintenance:
        done = [e.time for e in entries if e.instrument == inst.id
                and e.type in MAINTENANCE_TYPES and e.task == m.task]
        last = max(done) if done else None
        due = (last.astimezone(tz).date() + timedelta(days=m.interval_days)) if last else today
        out.append(Due(m.task, m.interval_days, last, due))
    return sorted(out, key=lambda d: d.due)


@dataclass(frozen=True)
class Status:
    available: bool
    text: str


def instrument_status(inst: Instrument, entries: list[LogEntry]) -> Status:
    """"available" or "down, ion gun replacement, back Thursday 9 Oct"."""
    fs = open_faults(entries, inst.id)
    blocking = [f for f in fs if f.blocking]
    if blocking:
        f = blocking[-1]
        back = f", back {f.back:%A} {f.back.day} {f.back:%b}" if f.back else ""
        return Status(False, f"down, {f.text.strip().rstrip('.')}{back}")
    if fs:
        return Status(True, f"available (known issue: {fs[-1].text.strip().rstrip('.')})")
    return Status(True, "available")


def commit_message(e: LogEntry) -> str:
    words = {"fault": "fault", "fault_update": f"fault {e.status or 'update'}",
             "consumable_change": f"change {e.consumable}", "bake_out": "bake-out"}
    what = words.get(e.type, e.type)
    detail = e.task or (e.text.splitlines()[0][:60] if e.text else "")
    hours = f" {e.hours:g} h" if e.hours else ""
    return f"log {e.instrument} {what}{hours}" + (f": {detail}" if detail else "")
