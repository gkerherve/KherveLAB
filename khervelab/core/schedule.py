"""Booking rules engine: pure functions over dataclasses.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Only hard clashes block outright; everything a manager may reasonably
work around is a warning they can override, and the override is
recorded in the booking file rather than forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from .models import DAYS, Booking, Instrument, User

BLOCK = "block"
WARN = "warn"

# Rules a manager may override (recorded in the booking's `override:` list)
OVERRIDABLE = ("concurrent_limit", "permission")


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str  # BLOCK or WARN
    message: str
    clash: Booking | None = None

    @property
    def blocking(self) -> bool:
        return self.severity == BLOCK


def snap(dt: datetime, granularity_minutes: int, tz: ZoneInfo) -> datetime:
    """Round to the nearest slot boundary in local wall-clock time."""
    local = dt.astimezone(tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes = local.hour * 60 + local.minute + local.second / 60
    slots = round(minutes / granularity_minutes)
    wall = midnight.replace(tzinfo=None) + timedelta(minutes=slots * granularity_minutes)
    return wall.replace(tzinfo=tz)


def snap_booking(b: Booking, inst: Instrument, tz: ZoneInfo) -> Booking:
    g = inst.slot_granularity_minutes
    start, end = snap(b.start, g, tz), snap(b.end, g, tz)
    if end <= start:
        end = start + timedelta(minutes=g)
    return b.with_times(start, end)


def _day_segments(start: datetime, end: datetime, tz: ZoneInfo):
    """Split [start, end) at local midnights: yields (day_key, seg_start, seg_end|None)."""
    s = start.astimezone(tz)
    e = end.astimezone(tz)
    while True:
        next_midnight = (s.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
                         + timedelta(days=1)).replace(tzinfo=tz)
        day = DAYS[s.weekday()]
        if e <= next_midnight:
            seg_end = None if e == next_midnight else e.timetz().replace(tzinfo=None)
            yield day, s.time(), seg_end
            return
        yield day, s.time(), None
        s = next_midnight


def within_bookable_hours(b: Booking, inst: Instrument, tz: ZoneInfo) -> bool:
    if not inst.bookable_hours:
        return True
    segments = list(_day_segments(b.start, b.end, tz))
    for i, (day, seg_start, seg_end) in enumerate(segments):
        ranges = inst.bookable_hours.get(day, ())
        if not any(r.contains(seg_start, seg_end) for r in ranges):
            return False
        # a booking may only run over midnight when that day is open to 24:00
        # and the next opens at 00:00
        if i > 0 and seg_start != time(0, 0):
            return False
    return True


def on_granularity(dt: datetime, g: int, tz: ZoneInfo) -> bool:
    local = dt.astimezone(tz)
    return local.second == 0 and local.microsecond == 0 and (local.hour * 60 + local.minute) % g == 0


def check(
    b: Booking,
    inst: Instrument,
    existing: Iterable[Booking],
    tz: ZoneInfo,
    now: datetime,
    user: User | None = None,
    ignore: Booking | None = None,
    downtime: Iterable[tuple[datetime, datetime | None, str]] = (),
) -> list[Violation]:
    """Every rule the booking breaks. `ignore` is the booking being edited,
    so moving a booking never clashes with its own old position."""
    out: list[Violation] = []
    others = [o for o in existing if o != ignore]

    if b.instrument != inst.id:
        out.append(Violation("instrument", BLOCK, f"booking is for {b.instrument}, not {inst.id}"))
    if b.end <= b.start:
        out.append(Violation("duration", BLOCK, "end must be after start"))
        return out

    if not within_bookable_hours(b, inst, tz):
        out.append(Violation("bookable_hours", BLOCK,
                             f"{inst.name} cannot be booked at that time; see its bookable hours"))

    blocked_kind = b.kind in ("maintenance", "blocked")
    if not blocked_kind:
        if b.minutes < inst.min_booking_minutes:
            out.append(Violation("min_duration", BLOCK,
                                 f"shortest booking is {_hm(inst.min_booking_minutes)}"))
        if b.minutes > inst.max_booking_minutes:
            out.append(Violation("max_duration", BLOCK,
                                 f"longest booking is {_hm(inst.max_booking_minutes)}"))
        if b.start - now > timedelta(days=inst.max_advance_days):
            out.append(Violation("advance_window", BLOCK,
                                 f"bookings open {inst.max_advance_days} days ahead"))

    g = inst.slot_granularity_minutes
    if not (on_granularity(b.start, g, tz) and on_granularity(b.end, g, tz)):
        out.append(Violation("granularity", WARN, f"times snap to {g}-minute slots"))

    if b.kind not in ("maintenance", "blocked"):
        for start, end, reason in downtime:
            if b.end > start and (end is None or b.start < end):
                until = f" until {end.astimezone(tz):%d %b}" if end else ""
                out.append(Violation("fault", BLOCK,
                                     f"{inst.name} is down{until}: {reason}"))

    for o in others:
        if b.overlaps(o):
            out.append(Violation("overlap", BLOCK, f"clashes with {o.label(tz)}", clash=o))

    if not blocked_kind:
        future_mine = [o for o in others
                       if o.user == b.user and o.end > now and o.kind == "measurement"
                       and o.instrument == inst.id]
        if b.kind == "measurement" and len(future_mine) >= inst.max_concurrent_per_user \
                and "concurrent_limit" not in b.override:
            out.append(Violation("concurrent_limit", WARN,
                                 f"{b.user} already holds {len(future_mine)} upcoming bookings "
                                 f"(limit {inst.max_concurrent_per_user})"))
        if inst.requires_permission and b.kind == "measurement" and user is not None \
                and not user.manager and inst.id not in user.permissions \
                and "permission" not in b.override:
            out.append(Violation("permission", WARN,
                                 f"{user.display} is not trained on {inst.name}"))
    return out


def blocking(violations: Iterable[Violation]) -> list[Violation]:
    return [v for v in violations if v.blocking]


def _hm(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


def pick_winner(a: Booking, b: Booking) -> Booking:
    """Default for a clash brought in by a pull: the earlier-created booking."""
    far = datetime.max.replace(tzinfo=ZoneInfo("UTC"))
    return a if (a.created or far) <= (b.created or far) else b


def expand_recurring(b: Booking, every_days: int, count: int, tz: ZoneInfo) -> list[Booking]:
    """Repeat in local wall-clock time, so a weekly 09:00 slot stays at 09:00
    across a daylight-saving change. Recurrences are written as files, never
    stored as a rule."""
    s = b.start.astimezone(tz).replace(tzinfo=None)
    e = b.end.astimezone(tz).replace(tzinfo=None)
    out = []
    for i in range(count):
        d = timedelta(days=every_days * i)
        out.append(b.with_times((s + d).replace(tzinfo=tz), (e + d).replace(tzinfo=tz)))
    return out
