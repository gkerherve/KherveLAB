"""Facility reports: usage, utilisation, downtime, training.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Pure functions over the loaded facility; the exporters in
``khervelab.reports_export`` turn a Report into .xlsx and a KherveTeX project.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from . import logbook
from .config import FacilityConfig
from .models import DAYS, Instrument

COUNTED = ("measurement", "training")


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _overlap_hours(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> float:
    s, e = max(_utc(a0), _utc(b0)), min(_utc(a1), _utc(b1))
    return max(0.0, (e - s).total_seconds() / 3600)


def bookable_hours(inst: Instrument, start: date, end: date, tz) -> float:
    """Hours the instrument could be booked between start and end (exclusive),
    in real elapsed time (a DST day is 23 or 25 hours long)."""
    if not inst.bookable_hours:
        s = datetime(start.year, start.month, start.day, tzinfo=tz)
        e = datetime(end.year, end.month, end.day, tzinfo=tz)
        return (_utc(e) - _utc(s)).total_seconds() / 3600
    total = 0.0
    d = start
    while d < end:
        for r in inst.bookable_hours.get(DAYS[d.weekday()], ()):
            s = datetime.combine(d, r.start, tzinfo=tz)
            e = (datetime.combine(d + timedelta(days=1), time(0), tzinfo=tz) if r.end is None
                 else datetime.combine(d, r.end, tzinfo=tz))
            total += (_utc(e) - _utc(s)).total_seconds() / 3600
        d += timedelta(days=1)
    return total


@dataclass
class UsageRow:
    instrument: str
    user: str
    who: str
    group: str
    kind: str
    start: datetime
    end: datetime
    hours: float              # within the report period


@dataclass
class DowntimeRow:
    instrument: str
    fault: str
    opened: datetime
    closed: datetime | None
    blocking: bool
    hours: float              # within the report period
    resolution: str = ""


@dataclass
class InstrumentSummary:
    id: str
    name: str
    facility: str
    booked: float = 0.0
    training: float = 0.0
    maintenance: float = 0.0
    bookable: float = 0.0
    downtime: float = 0.0
    faults: int = 0
    bookings: int = 0
    users: int = 0

    @property
    def utilisation(self) -> float:
        return (self.booked + self.training) / self.bookable if self.bookable else 0.0


@dataclass
class TrainingRow:
    who: str
    group: str
    instrument: str
    trained: date | None
    status: str


@dataclass
class Report:
    facility: str
    start: date
    end: date                 # exclusive
    instruments: list[InstrumentSummary] = field(default_factory=list)
    usage: list[UsageRow] = field(default_factory=list)
    downtime: list[DowntimeRow] = field(default_factory=list)
    by_group: dict[str, float] = field(default_factory=dict)
    by_user: dict[str, float] = field(default_factory=dict)
    monthly: dict[str, dict[str, float]] = field(default_factory=dict)   # month -> inst -> h
    training: list[TrainingRow] = field(default_factory=list)

    @property
    def total_booked(self) -> float:
        return sum(i.booked + i.training for i in self.instruments)

    @property
    def total_bookable(self) -> float:
        return sum(i.bookable for i in self.instruments)

    def active(self) -> list[InstrumentSummary]:
        return [i for i in self.instruments if i.booked or i.training or i.downtime or i.faults]


def _months(start: date, end: date) -> list[str]:
    out, d = [], date(start.year, start.month, 1)
    while d < end:
        out.append(f"{d:%Y-%m}")
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def build_report(cfg: FacilityConfig, start: date, end: date,
                 names: dict[str, str] | None = None, groups: dict[str, str] | None = None,
                 training: list[TrainingRow] | None = None,
                 instruments: list[str] | None = None,
                 now: datetime | None = None) -> Report:
    """`names`/`groups` map anonymous ids to people and groups; they come
    from the local encrypted store and are absent on any other machine, in
    which case the display strings stand in."""
    tz = cfg.facility.tz
    names, groups = names or {}, groups or {}
    p0 = datetime(start.year, start.month, start.day, tzinfo=tz)
    p1 = datetime(end.year, end.month, end.day, tzinfo=tz)
    ids = instruments or sorted(cfg.instruments)
    rep = Report(cfg.facility.name, start, end, training=training or [])
    months = _months(start, end)
    rep.monthly = {m: defaultdict(float) for m in months}
    summaries = {}
    for iid in ids:
        inst = cfg.instruments[iid]
        summaries[iid] = InstrumentSummary(iid, inst.name, inst.facility,
                                           bookable=bookable_hours(inst, start, end, tz))
    users_per_inst: dict[str, set] = defaultdict(set)
    by_group: dict[str, float] = defaultdict(float)
    by_user: dict[str, float] = defaultdict(float)
    for b in sorted(cfg.bookings.values(), key=lambda b: b.start):
        if b.instrument not in summaries:
            continue
        h = _overlap_hours(b.start, b.end, p0, p1)
        if h <= 0:
            continue
        sm = summaries[b.instrument]
        u = cfg.users.get(b.user)
        who = names.get(b.user) or (u.display if u else b.user)
        group = groups.get(b.user) or (u.display if u else "unknown")
        rep.usage.append(UsageRow(b.instrument, b.user, who, group, b.kind, b.start, b.end, h))
        if b.kind == "measurement":
            sm.booked += h
        elif b.kind == "training":
            sm.training += h
        else:
            sm.maintenance += h
        if b.kind in COUNTED:
            sm.bookings += 1
            users_per_inst[b.instrument].add(b.user)
            by_group[group] += h
            by_user[who] += h
            for m in months:
                y, mo = int(m[:4]), int(m[5:])
                m0 = datetime(y, mo, 1, tzinfo=tz)
                m1 = datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=tz)
                mh = _overlap_hours(b.start, b.end, max(m0, p0), min(m1, p1))
                if mh:
                    rep.monthly[m][b.instrument] += mh
    for iid, sm in summaries.items():
        sm.users = len(users_per_inst[iid])
    for f in logbook.faults(cfg.logs):
        if f.instrument not in summaries:
            continue
        resolved = [e.time for e in f.history if e.status == "resolved"]
        closed = max(resolved) if resolved else None
        if f.opened >= p1 or (closed and closed <= p0):
            continue
        # an open fault has been down until now, not until the end of the period
        open_until = min(_utc(p1), _utc(now or datetime.now(timezone.utc)))
        h = _overlap_hours(f.opened, closed or open_until, p0, p1) if f.blocking else 0.0
        rep.downtime.append(DowntimeRow(f.instrument, f.text, f.opened, closed, f.blocking, h,
                                        f.resolution))
        summaries[f.instrument].faults += 1
        summaries[f.instrument].downtime += h
    rep.instruments = sorted(summaries.values(), key=lambda s: (s.facility, s.name))
    rep.by_group = dict(sorted(by_group.items(), key=lambda kv: -kv[1]))
    rep.by_user = dict(sorted(by_user.items(), key=lambda kv: -kv[1]))
    rep.monthly = {m: dict(v) for m, v in rep.monthly.items()}
    return rep


def training_rows(store, svc_users, start: date, end: date) -> list[TrainingRow]:
    """Training completed in the period, from the local store."""
    out = []
    for p in store.people():
        for t in store.trainings(p.id):
            if t.date_trained and start <= t.date_trained < end:
                out.append(TrainingRow(p.name, p.group, t.instrument, t.date_trained, t.status))
    return sorted(out, key=lambda r: (r.trained or date.min, r.who))


__all__ = ["Report", "build_report", "bookable_hours", "training_rows"]
