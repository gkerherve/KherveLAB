"""Booking rules, approval and costs. No web code here, so it tests directly.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from werkzeug.security import check_password_hash, generate_password_hash

from . import db

FMT = "%Y-%m-%dT%H:%M"
APPROVAL_LABELS = {
    "auto": "Approved automatically",
    "trained": "Automatic for trained users, otherwise the administrator approves",
    "manual": "The administrator approves every booking",
}


EXAMPLES = [
    # name, description, approval, colour, open, close, weekends, max minutes
    ("XPS", "X-ray photoelectron spectroscopy", "trained", "#1f6feb", "00:00", "24:00", 1, 1440),
    ("NAP-XPS", "Near ambient pressure XPS, UPS and LEED", "manual", "#8250df", "08:00", "20:00",
     0, 600),
    ("TGA / DSC", "Thermogravimetric analysis and calorimetry", "trained", "#cf222e", "00:00",
     "24:00", 1, 4320),
    ("Dilatometer", "Thermal expansion and sintering", "trained", "#e16f24", "00:00", "24:00", 1,
     4320),
    ("BET", "Gas sorption surface area and porosity", "auto", "#9a6700", "00:00", "24:00", 1,
     4320),
    ("Glovebox", "Argon glovebox for air-sensitive samples", "auto", "#57606a", "08:00", "20:00",
     0, 480),
]


ROLE_LABELS = {"user": "User", "superuser": "Super user (books for others)",
               "admin": "Lab manager"}


def acts_for_others(u) -> bool:
    """Lab managers and super users can book and manage bookings for others."""
    return u is not None and u["role"] in ("admin", "superuser")


class BookingError(ValueError):
    pass


def tz(conn) -> ZoneInfo:
    return ZoneInfo(db.setting(conn, "timezone"))


def now_local(conn) -> datetime:
    return datetime.now(tz(conn)).replace(tzinfo=None, second=0, microsecond=0)


def parse(text: str) -> datetime:
    """Accepts '2026-10-07T09:00', '2026-10-07 09:00' and ISO strings with
    seconds or an offset (FullCalendar sends those); returns lab-local naive."""
    t = text.strip().replace(" ", "T")
    dt = datetime.fromisoformat(t)
    return dt.replace(tzinfo=None, second=0, microsecond=0)


def fmt(dt: datetime) -> str:
    return dt.strftime(FMT)


def fmt_duration(minutes: float) -> str:
    """30 min, 4.5 h, 24 h, 1 h 15 min."""
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    if m == 0:
        return f"{h} h"
    if m in (15, 30, 45):
        return f"{minutes / 60:g} h"
    return f"{h} h {m} min"


def minutes_of(hhmm: str) -> int:
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


def hours(conn, start: datetime, end: datetime) -> float:
    """Real elapsed hours: a booking across a clock change is billed for
    the time that actually passed."""
    z = tz(conn)
    s = start.replace(tzinfo=z).astimezone(timezone.utc)
    e = end.replace(tzinfo=z).astimezone(timezone.utc)
    return max(0.0, (e - s).total_seconds() / 3600)


# -- users ---------------------------------------------------------------------------

def create_user(conn, username: str, password: str, full_name: str, email: str = "",
                group_name: str = "", category: str = "", role: str = "user",
                status: str | None = None) -> int:
    username = username.strip()
    if not username or not full_name.strip():
        raise ValueError("username and full name are required")
    if len(password) < 8:
        raise ValueError("the password must have at least 8 characters")
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise ValueError(f"the username {username!r} is taken")
    if status is None:
        status = "pending" if db.setting(conn, "account_approval") == "1" else "active"
    cats = db.categories(conn)
    if category not in cats:
        category = cats[0] if cats else ""
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, full_name, email, group_name, category, "
        "role, status, created) VALUES (?,?,?,?,?,?,?,?,?)",
        (username, generate_password_hash(password), full_name.strip(), email.strip(),
         group_name.strip(), category, role, status, fmt(now_local(conn))))
    return cur.lastrowid


def authenticate(conn, username: str, password: str) -> sqlite3.Row | None:
    u = conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if u and check_password_hash(u["password_hash"], password):
        return u
    return None


def set_password(conn, user_id: int, password: str) -> None:
    if len(password) < 8:
        raise ValueError("the password must have at least 8 characters")
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(password), user_id))


def is_authorised(conn, user_id: int, instrument_id: int) -> bool:
    return conn.execute("SELECT 1 FROM authorised WHERE user_id=? AND instrument_id=?",
                        (user_id, instrument_id)).fetchone() is not None


# -- instruments and rates -----------------------------------------------------------------

def add_instrument(conn, name, description, approval, colour, open_t, close_t, weekends,
                    max_minutes) -> int:
    cur = conn.execute(
        "INSERT INTO instruments (name, description, colour, approval, open_time, close_time, "
        "weekends, max_minutes) VALUES (?,?,?,?,?,?,?,?)",
        (name, description, colour, approval, open_t, close_t, weekends, max_minutes))
    return cur.lastrowid


def instrument_bookings(conn, instrument_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM bookings WHERE instrument_id=?",
                        (instrument_id,)).fetchone()[0]


def retire_instrument(conn, instrument_id: int) -> None:
    """Out of service for good: no longer bookable or shown, but its bookings
    and their charges stay for reports."""
    conn.execute("UPDATE instruments SET active=0 WHERE id=?", (instrument_id,))


def remove_instrument(conn, instrument_id: int, with_bookings: bool = False) -> None:
    """Delete an instrument (its sessions, rates, training and reports go
    with it). Bookings hold past charges, so they are only deleted when
    asked; otherwise an instrument with bookings is refused."""
    n = instrument_bookings(conn, instrument_id)
    if n and not with_bookings:
        raise ValueError(f"the instrument has {n} booking(s); retire it to keep them, "
                         "or delete it with its bookings")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM bookings WHERE instrument_id=?", (instrument_id,))
        conn.execute("DELETE FROM instruments WHERE id=?", (instrument_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def rate_for(conn, instrument_id: int, category: str) -> float:
    r = conn.execute("SELECT rate FROM rates WHERE instrument_id=? AND category=?",
                     (instrument_id, category)).fetchone()
    return float(r["rate"]) if r else 0.0


def rates(conn, instrument_id: int) -> dict[str, float]:
    return {c: rate_for(conn, instrument_id, c) for c in db.categories(conn)}


# -- bookings ---------------------------------------------------------------------------

# -- reported problems and out-of-order periods ---------------------------------------

ISSUE_LABELS = {"problem": "Problem (still usable, take care)",
                "down": "Out of order (cannot be used)"}


def report_issue(conn, instrument_id: int, kind: str, start: datetime,
                 end: datetime | None, note: str, user_id: int | None) -> int:
    if kind not in ISSUE_LABELS:
        raise ValueError(kind)
    if end is not None and end <= start:
        raise ValueError("the end must be after the start")
    return conn.execute(
        "INSERT INTO issues (instrument_id, kind, start, end, note, reported_by, created) "
        "VALUES (?,?,?,?,?,?,?)",
        (instrument_id, kind, fmt(start), fmt(end) if end else None, note.strip(), user_id,
         fmt(now_local(conn)))).lastrowid


def resolve_issue(conn, issue_id: int, when: datetime | None = None) -> None:
    """Fixed: the issue ends now (or at `when`)."""
    when = when or now_local(conn)
    row = conn.execute("SELECT start FROM issues WHERE id=?", (issue_id,)).fetchone()
    if row is not None and parse(row["start"]) > when:
        conn.execute("DELETE FROM issues WHERE id=?", (issue_id,))   # never began
    else:
        conn.execute("UPDATE issues SET end=? WHERE id=?", (fmt(when), issue_id))


def issues(conn, instrument_id: int, start: datetime, end: datetime) -> list[sqlite3.Row]:
    """Issues overlapping [start, end); an open-ended issue runs on until fixed."""
    return conn.execute(
        "SELECT * FROM issues WHERE instrument_id=? AND start < ? AND (end IS NULL OR end > ?) "
        "ORDER BY start", (instrument_id, fmt(end), fmt(start))).fetchall()


def current_issue(conn, instrument_id: int) -> sqlite3.Row | None:
    now = now_local(conn)
    rows = issues(conn, instrument_id, now, now + timedelta(minutes=1))
    return next((r for r in rows if r["kind"] == "down"), rows[0] if rows else None)


def issue_state(conn, instrument_id: int, start: datetime, end: datetime) -> str:
    """'down', 'problem' or 'ok' for a stretch of time (worst wins)."""
    kinds = {r["kind"] for r in issues(conn, instrument_id, start, end)}
    return "down" if "down" in kinds else "problem" if "problem" in kinds else "ok"


def slots(conn, inst, start: datetime, end: datetime) -> list[tuple[datetime, datetime, str]]:
    """Every bookable slot overlapping [start, end) as (start, end, label):
    session occurrences, or each free-time period cut into its slots (a
    one-booking period is a single slot). The calendar draws these, so an
    empty calendar still shows what can be booked."""
    if inst["booking_mode"] == "sessions":
        return [(o.start, o.end, o.name) for o in occurrences(conn, inst["id"], start, end)]
    out = []
    for p in periods(inst, start, end):
        if p.slot is None:
            out.append((p.start, p.end, p.label))
            continue
        t = p.start
        while t < p.end and t < end:
            t1 = min(p.end, t + timedelta(minutes=p.slot))
            if t1 > start:
                out.append((t, t1, p.label))
            t = t1
    return out


# -- daytime, evening and weekend periods (free-time instruments) ----------------------

PERIOD_MODES = {
    "closed": "Closed",
    "block": "One booking for the whole period",
    "daytime": "Same slots as the daytime",
    "own": "Its own slot length",
}
PERIOD_NAMES = {"day": "Daytime", "evening": "Evening", "weekend": "Weekend"}


@dataclass(frozen=True)
class Period:
    kind: str                 # day, evening, weekend
    start: datetime
    end: datetime
    slot: int | None          # minutes; None = book the whole period as one booking

    @property
    def label(self) -> str:
        return PERIOD_NAMES[self.kind]


def _span(day, start_hhmm: str, end_hhmm: str) -> tuple[datetime, datetime]:
    """A daily window; an end at or before the start is the next day."""
    base = datetime(day.year, day.month, day.day)
    s, e = minutes_of(start_hhmm), minutes_of(end_hhmm)
    if e <= s:
        e += 1440
    return base + timedelta(minutes=s), base + timedelta(minutes=e)


def _slot_for(inst, kind: str) -> int | None:
    mode = inst[f"{kind}_mode"]
    if mode == "block":
        return None
    if mode == "own":
        return inst[f"{kind}_slot"]
    return inst["slot_minutes"]


def periods(inst, start: datetime, end: datetime) -> list[Period]:
    """The bookable windows of a free-time instrument overlapping [start, end):
    the daytime on weekdays, then the evening (weekdays) and the weekend
    (Saturday and Sunday), each in its own way. A window that would overlap
    an earlier one is trimmed, so they never double up."""
    out: list[Period] = []
    day = start.date() - timedelta(days=2)       # a whole weekend can start two days back
    while day <= end.date():
        weekday = day.weekday() < 5
        if weekday:
            s0, s1 = _span(day, inst["open_time"], inst["close_time"])
            out.append(Period("day", s0, s1, inst["slot_minutes"]))
            if inst["evening_mode"] != "closed":
                s0, s1 = _span(day, inst["evening_start"], inst["evening_end"])
                out.append(Period("evening", s0, s1, _slot_for(inst, "evening")))
        elif inst["weekend_mode"] != "closed":
            if inst["weekend_span"] == "whole":
                if day.weekday() == 5:               # Saturday start -> Monday end
                    s0 = _span(day, inst["weekend_start"], inst["weekend_start"])[0]
                    monday = day + timedelta(days=2)
                    s1 = datetime(monday.year, monday.month, monday.day) + timedelta(
                        minutes=minutes_of(inst["weekend_end"]))
                    out.append(Period("weekend", s0, s1, _slot_for(inst, "weekend")))
            else:
                s0, s1 = _span(day, inst["weekend_start"], inst["weekend_end"])
                out.append(Period("weekend", s0, s1, _slot_for(inst, "weekend")))
        day += timedelta(days=1)
    out.sort(key=lambda p: p.start)
    trimmed: list[Period] = []
    for p in out:
        if trimmed and p.start < trimmed[-1].end:
            p = Period(p.kind, trimmed[-1].end, p.end, p.slot)
        if p.end > p.start:
            trimmed.append(p)
    return [p for p in trimmed if p.start < end and p.end > start]


def _on_grid(p: Period, t: datetime) -> bool:
    if t in (p.start, p.end):
        return True
    if p.slot is None:
        return False
    return int((t - p.start).total_seconds() // 60) % p.slot == 0


def period_errors(inst, start: datetime, end: datetime) -> list[str]:
    """A free-time booking must lie in bookable periods with no gap, start and
    end on a slot of the period it falls in, and take a one-booking period
    whole. Shortest and longest apply to daytime bookings; evenings and
    weekends are governed by their own slots or blocks."""
    ps = periods(inst, start, end)
    first = next((p for p in ps if p.start <= start < p.end), None)
    if first is None:
        if start.weekday() >= 5 and inst["weekend_mode"] == "closed":
            return [f"{inst['name']} cannot be booked at weekends"]
        return [f"{inst['name']} is closed at {start:%a %H:%M} ({describe_periods(inst)})"]
    chain = [first]
    while chain[-1].end < end:
        nxt = next((p for p in ps if p.start == chain[-1].end), None)
        if nxt is None:
            return [f"{inst['name']} is closed from {chain[-1].end:%a %H:%M}, so the booking "
                    f"must end by then ({describe_periods(inst)})"]
        chain.append(nxt)
    last = chain[-1]
    errors = []
    minutes = (end - start).total_seconds() / 60
    slots = {p.slot for p in chain}
    # a run carrying on through slotted periods of one slot length (e.g. a 9 h
    # run from 18:00 to 03:00) only needs to be a whole number of slots
    whole_run = len(chain) > 1 and len(slots) == 1 and None not in slots and \
        minutes % first.slot == 0
    for p, t, what in ((first, start, "start"), (last, end, "end")):
        if what == "end" and whole_run:
            continue
        if not _on_grid(p, t):
            if p.slot is None:
                errors.append(f"the {p.label.lower()} ({p.start:%H:%M}–{p.end:%H:%M}) is booked "
                              "as one block")
            else:
                errors.append(f"the {what} must fall on a {fmt_duration(p.slot)} slot of the "
                              f"{p.label.lower()}, counted from {p.start:%H:%M}")
    if all(p.kind == "day" for p in chain):
        if minutes < inst["min_minutes"]:
            errors.append(f"the shortest booking is {fmt_duration(inst['min_minutes'])}")
        if minutes > inst["max_minutes"]:
            errors.append(f"the longest booking is {fmt_duration(inst['max_minutes'])}")
    return errors


def snap_start(inst, t: datetime) -> datetime:
    """Where a click at t starts a booking: the slot it is in, or the whole
    block. Outside every period the time is returned unchanged."""
    p = next((p for p in periods(inst, t, t + timedelta(minutes=1)) if p.start <= t < p.end),
             None)
    if p is None:
        return t
    if p.slot is None:
        return p.start
    steps = int((t - p.start).total_seconds() // 60) // p.slot
    return p.start + timedelta(minutes=steps * p.slot)


def snap_end(inst, start: datetime, t: datetime) -> datetime:
    """The end for a drag from start to t: the slot boundary nearest t (at
    least one slot), or the end of the block t is in."""
    t = max(t, start + timedelta(minutes=1))
    p = next((p for p in periods(inst, t - timedelta(minutes=1), t)
              if p.start < t <= p.end), None)
    if p is None:
        return t
    if p.slot is None:
        return p.end
    origin = max(p.start, start) if p.start <= start < p.end else p.start
    steps = max(1, round((t - origin).total_seconds() / 60 / p.slot))
    return min(p.end, origin + timedelta(minutes=steps * p.slot))


def describe_periods(inst) -> str:
    def window(s, e):
        return f"{s}–{e}" + (" (next day)" if minutes_of(e) <= minutes_of(s) else "")
    around = inst["open_time"] == "00:00" and inst["close_time"] in ("24:00", "23:59")
    parts = [("Weekdays around the clock" if around else
              f"Daytime {window(inst['open_time'], inst['close_time'])}")
             + f" in {fmt_duration(inst['slot_minutes'])} slots"]
    for kind in ("evening", "weekend"):
        mode = inst[f"{kind}_mode"]
        if mode == "closed":
            parts.append(f"{PERIOD_NAMES[kind]} closed")
            continue
        how = ("as one booking" if mode == "block" else
               f"in {fmt_duration(_slot_for(inst, kind))} slots")
        if kind == "weekend" and inst["weekend_span"] == "whole":
            parts.append(f"Weekend Sat {inst['weekend_start']} → Mon {inst['weekend_end']} {how}")
            continue
        parts.append(f"{PERIOD_NAMES[kind]} {window(inst[kind + '_start'], inst[kind + '_end'])}"
                     + (" each day" if kind == "weekend" else "") + f" {how}")
    return " · ".join(parts)


# -- sessions -------------------------------------------------------------------------

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class Occurrence:
    session_id: int
    name: str
    start: datetime
    end: datetime


def sessions(conn, instrument_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM sessions WHERE instrument_id=? ORDER BY sort, start_time",
                        (instrument_id,)).fetchall()


def session_hours(start_time: str, end_time: str) -> float:
    """Length on the clock face; an end at or before the start is the next day."""
    s, e = minutes_of(start_time), minutes_of(end_time)
    return ((e - s) % 1440 or 1440) / 60


def days_label(days: str) -> str:
    if days == "0123456":
        return "every day"
    if days == "01234":
        return "weekdays"
    if days == "56":
        return "weekends"
    return ", ".join(DAY_NAMES[int(d)] for d in days)


def occurrences(conn, instrument_id: int, start: datetime, end: datetime) -> list[Occurrence]:
    """Session occurrences overlapping [start, end), in time order. A session
    belongs to the day it starts on, so an evening run that starts Friday is
    a Friday session even though it ends on Saturday."""
    out = []
    rows = sessions(conn, instrument_id)
    day = start.date() - timedelta(days=1)          # yesterday's evening may run into today
    while day <= end.date():
        for s in rows:
            if str(day.weekday()) not in s["days"]:
                continue
            s0 = datetime.combine(day, datetime.min.time()) + timedelta(
                minutes=minutes_of(s["start_time"]))
            s1 = s0 + timedelta(hours=session_hours(s["start_time"], s["end_time"]))
            if s0 < end and s1 > start:
                out.append(Occurrence(s["id"], s["name"], s0, s1))
        day += timedelta(days=1)
    return sorted(out, key=lambda o: o.start)


def matching_session(conn, instrument_id: int, start: datetime, end: datetime) -> Occurrence | None:
    for o in occurrences(conn, instrument_id, start, end):
        if o.start == start and o.end == end:
            return o
    return None


def session_price(conn, session_id: int, category: str) -> float | None:
    r = conn.execute("SELECT price FROM session_prices WHERE session_id=? AND category=?",
                     (session_id, category)).fetchone()
    return float(r["price"]) if r else None


# ready-made layouts the lab manager can start from and then edit
SESSION_TEMPLATES = {
    "Two day sessions and an evening run": [
        ("Morning", "08:00", "12:30", "01234"),
        ("Afternoon", "12:30", "17:00", "01234"),
        ("Evening and overnight", "17:00", "08:00", "01234"),
    ],
    "Full day and overnight": [
        ("Day", "08:00", "17:00", "01234"),
        ("Overnight", "17:00", "08:00", "01234"),
    ],
    "24-hour runs, every day": [
        ("24 h run", "08:00", "08:00", "0123456"),
    ],
    "Half days": [
        ("Morning", "09:00", "13:00", "01234"),
        ("Afternoon", "13:00", "17:00", "01234"),
    ],
}


@dataclass
class SessionSpec:
    name: str
    start_time: str
    end_time: str
    days: str
    prices: dict[str, float | None]        # category -> fixed price, None = hourly rate
    id: int | None = None


def save_sessions(conn, instrument_id: int, specs: list[SessionSpec]) -> None:
    """Replace an instrument's sessions. Kept ids stay (bookings point at
    them); removed sessions are deleted and their bookings keep their price."""
    for sp in specs:
        if not sp.name.strip():
            raise ValueError("every session needs a name")
        if not set(sp.days) <= set("0123456") or not sp.days:
            raise ValueError(f"session {sp.name!r} must run on at least one day")
        for t in (sp.start_time, sp.end_time):
            h, m = t.split(":")
            if not (0 <= int(h) < 24 and 0 <= int(m) < 60):
                raise ValueError(f"session {sp.name!r}: {t} is not a time")
    keep = {sp.id for sp in specs if sp.id}
    for r in sessions(conn, instrument_id):
        if r["id"] not in keep:
            conn.execute("DELETE FROM sessions WHERE id=?", (r["id"],))
    for n, sp in enumerate(specs):
        days = "".join(sorted(set(sp.days)))
        if sp.id:
            conn.execute("UPDATE sessions SET name=?, start_time=?, end_time=?, days=?, sort=? "
                         "WHERE id=? AND instrument_id=?",
                         (sp.name.strip(), sp.start_time, sp.end_time, days, n, sp.id,
                          instrument_id))
            sid = sp.id
        else:
            sid = conn.execute("INSERT INTO sessions (instrument_id, name, start_time, end_time, "
                               "days, sort) VALUES (?,?,?,?,?,?)",
                               (instrument_id, sp.name.strip(), sp.start_time, sp.end_time, days,
                                n)).lastrowid
        conn.execute("DELETE FROM session_prices WHERE session_id=?", (sid,))
        for cat, price in sp.prices.items():
            if price is not None:
                conn.execute("INSERT INTO session_prices (session_id, category, price) "
                             "VALUES (?,?,?)", (sid, cat, max(0.0, float(price))))


def check(conn, inst: sqlite3.Row, user: sqlite3.Row, start: datetime, end: datetime,
          exclude: int | None = None, now: datetime | None = None) -> list[str]:
    """Every reason the slot cannot be booked; empty when it can. The
    administrator is held only to the hard rules (no clash, end after start)."""
    errors = []
    if end <= start:
        return ["the end must be after the start"]
    admin = user["role"] == "admin"
    now = now or now_local(conn)
    minutes = None
    if not inst["active"]:
        errors.append(f"{inst['name']} is not available for booking")
    if not admin:
        for r in issues(conn, inst["id"], start, end):
            if r["kind"] == "down":
                until = f" until {r['end'].replace('T', ' ')}" if r["end"] else " until fixed"
                errors.append(f"{inst['name']} is out of order{until}"
                              + (f": {r['note']}" if r["note"] else ""))
                break
    if not admin:
        if start < now:
            errors.append("the start is in the past")
        if start > now + timedelta(days=inst["max_days_ahead"]):
            errors.append(f"bookings open {inst['max_days_ahead']} days ahead")
        if inst["booking_mode"] == "sessions":
            if matching_session(conn, inst["id"], start, end) is None:
                errors.append(f"{inst['name']} is booked by session; choose one of its sessions")
            minutes = None
        else:
            minutes = (end - start).total_seconds() / 60
    if not admin and minutes is not None:
        errors += period_errors(inst, start, end)
    clash = conn.execute(
        "SELECT b.start, b.end, u.full_name FROM bookings b JOIN users u ON u.id=b.user_id "
        "WHERE b.instrument_id=? AND b.status IN ('pending','approved') AND b.start < ? "
        "AND b.end > ? AND b.id != ?",
        (inst["id"], fmt(end), fmt(start), exclude or 0)).fetchone()
    if clash:
        errors.append(f"the slot overlaps a booking from {clash['start'][11:]} to "
                      f"{clash['end'][11:]} on {clash['start'][:10]}")
    return errors


@dataclass
class Created:
    id: int
    status: str


def book(conn, instrument_id: int, user_id: int, start: datetime, end: datetime,
         purpose: str = "", now: datetime | None = None, actor_id: int | None = None) -> Created:
    """Create a booking; it is approved at once or waits, depending on the
    instrument's approval mode. BEGIN IMMEDIATE serialises the clash check
    and the insert, so two people cannot take the same slot at once."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        inst = conn.execute("SELECT * FROM instruments WHERE id=?", (instrument_id,)).fetchone()
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if inst is None or user is None:
            raise BookingError("unknown instrument or user")
        if user["status"] != "active":
            raise BookingError("your account is not active yet")
        actor = user
        if actor_id and actor_id != user_id:
            actor = conn.execute("SELECT * FROM users WHERE id=?", (actor_id,)).fetchone()
            if not acts_for_others(actor):
                raise BookingError("only the lab manager or a super user can book for "
                                   "someone else")
        # the manager is held to the hard rules only; a super user books exactly
        # as the person would (their rules, their approval)
        rules_for = actor if actor["role"] == "admin" else user
        errors = check(conn, inst, rules_for, start, end, now=now)
        if errors:
            raise BookingError("; ".join(errors))
        if actor["role"] == "admin" or inst["approval"] == "auto" or (
                inst["approval"] == "trained" and is_authorised(conn, user_id, instrument_id)):
            status = "approved"
        else:
            status = "pending"
        rate = rate_for(conn, instrument_id, user["category"])
        session_id, price = _session_and_price(conn, inst, start, end, user["category"])
        stamp = fmt(now_local(conn))
        cur = conn.execute(
            "INSERT INTO bookings (instrument_id, user_id, start, end, purpose, status, rate, "
            "created, decided_by, decided_at, session_id, price) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (instrument_id, user_id, fmt(start), fmt(end), purpose.strip(), status, rate, stamp,
             actor["id"] if actor is not user else None, stamp if status == "approved" else None,
             session_id, price))
        conn.execute("COMMIT")
        return Created(cur.lastrowid, status)
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _session_and_price(conn, inst, start: datetime, end: datetime,
                       category: str) -> tuple[int | None, float | None]:
    """The session a booking fills, and its fixed price for the user's category
    (None: charged by the hour)."""
    if inst["booking_mode"] != "sessions":
        return None, None
    occ = matching_session(conn, inst["id"], start, end)
    if occ is None:          # the lab manager blocking free-form time
        return None, None
    return occ.session_id, session_price(conn, occ.session_id, category)


def book_range(conn, instrument_id: int, user_id: int, start: datetime, end: datetime,
               purpose: str = "", actor_id: int | None = None) -> tuple[list[Created], list[str]]:
    """Book every free session of a session-booked instrument that overlaps
    the range (one booking per session), or the range itself otherwise.
    Returns what was booked and why anything was not."""
    inst = conn.execute("SELECT * FROM instruments WHERE id=?", (instrument_id,)).fetchone()
    if inst is None:
        raise BookingError("unknown instrument")
    if inst["booking_mode"] != "sessions":
        return [book(conn, instrument_id, user_id, start, end, purpose, actor_id=actor_id)], []
    made, problems = [], []
    occs = occurrences(conn, instrument_id, start, end)
    if not occs:
        raise BookingError(f"no {inst['name']} session falls in that time")
    for o in occs:
        try:
            made.append(book(conn, instrument_id, user_id, o.start, o.end, purpose,
                             actor_id=actor_id))
        except BookingError as exc:
            problems.append(f"{o.name} {o.start:%a %d %b %H:%M}: {exc}")
    if not made:
        raise BookingError("; ".join(problems))
    return made, problems


def normalise_range(conn, inst, start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Where a dragged range lands, the same in the app and on the web: free
    time snaps to the slots of its periods (a one-booking period is taken
    whole) and is stretched to the shortest daytime booking if needed.
    Session instruments keep the range; book_range picks their sessions."""
    if inst["booking_mode"] != "free":
        return start, end
    start = snap_start(inst, start)
    end = snap_end(inst, start, end)
    shortest = start + timedelta(minutes=inst["min_minutes"])
    if end < shortest and period_errors(inst, start, end):
        end = snap_end(inst, start, shortest)
    return start, end


def instant_for(conn, inst, user, actor=None) -> bool:
    actor = actor or user
    return (actor["role"] == "admin" or inst["approval"] == "auto" or
            (inst["approval"] == "trained" and is_authorised(conn, user["id"], inst["id"])))


def quote(conn, inst, user, start: datetime, end: datetime, actor=None) -> dict:
    """What booking [start, end) would mean: the item(s) it makes, each one's
    cost and rule problems, the total, and whether it is approved at once.
    The web booking window shows exactly this; the app's dialog uses the
    same rules."""
    actor = actor or user
    rules_for = actor if actor["role"] == "admin" else user   # as book() does
    cur = db.setting(conn, "currency")
    rate = rate_for(conn, inst["id"], user["category"])
    items = []
    if inst["booking_mode"] == "sessions":
        for o in occurrences(conn, inst["id"], start, end):
            price = session_price(conn, o.session_id, user["category"])
            cost = price if price is not None else hours(conn, o.start, o.end) * rate
            items.append({"start": fmt(o.start), "end": fmt(o.end), "label": o.name,
                          "cost": round(cost, 2),
                          "errors": check(conn, inst, rules_for, o.start, o.end)})
    else:
        s, e = normalise_range(conn, inst, start, end)
        items.append({"start": fmt(s), "end": fmt(e), "label": "",
                      "cost": round(hours(conn, s, e) * rate, 2),
                      "errors": check(conn, inst, rules_for, s, e)})
    ok = [i for i in items if not i["errors"]]
    return {"items": items, "bookable": bool(ok), "currency": cur,
            "total": round(sum(i["cost"] for i in ok), 2),
            "instant": instant_for(conn, inst, user, actor)}


def decide(conn, booking_id: int, admin_id: int, approve: bool, note: str = "") -> None:
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if b is None or b["status"] != "pending":
        raise BookingError("this booking is no longer waiting for a decision")
    conn.execute("UPDATE bookings SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?",
                 ("approved" if approve else "rejected", admin_id, fmt(now_local(conn)),
                  note.strip(), booking_id))


def cancel(conn, booking_id: int, user: sqlite3.Row, note: str = "") -> None:
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if b is None:
        raise BookingError("no such booking")
    if user["role"] != "admin":
        if b["user_id"] != user["id"] and not acts_for_others(user):
            raise BookingError("you can only cancel your own bookings")
        if parse(b["start"]) <= now_local(conn):
            raise BookingError("a booking that has started can only be cancelled by the "
                               "lab manager")
    if b["status"] not in ("pending", "approved"):
        raise BookingError("this booking is already closed")
    conn.execute("UPDATE bookings SET status='cancelled', decided_by=?, decided_at=?, note=? "
                 "WHERE id=?", (user["id"], fmt(now_local(conn)), note.strip(), booking_id))


def reschedule(conn, booking_id: int, user: sqlite3.Row, start: datetime, end: datetime,
               instrument_id: int | None = None, purpose: str | None = None) -> str:
    """Move or resize a booking. A user's change to a booking that needed
    approval goes back to the manager; the manager's own changes stand.
    Returns the new status."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        b = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if b is None or b["status"] not in ("pending", "approved"):
            raise BookingError("this booking can no longer be changed")
        admin = user["role"] == "admin"
        if not admin:
            if b["user_id"] != user["id"] and not acts_for_others(user):
                raise BookingError("you can only change your own bookings")
            if parse(b["start"]) <= now_local(conn):
                raise BookingError("a booking that has started can only be changed by the "
                                   "lab manager")
        iid = instrument_id or b["instrument_id"]
        inst = conn.execute("SELECT * FROM instruments WHERE id=?", (iid,)).fetchone()
        owner = conn.execute("SELECT * FROM users WHERE id=?", (b["user_id"],)).fetchone()
        errors = check(conn, inst, user if admin else owner, start, end, exclude=booking_id)
        if errors:
            raise BookingError("; ".join(errors))
        if admin:
            status = b["status"]
        elif inst["approval"] == "auto" or (
                inst["approval"] == "trained" and is_authorised(conn, owner["id"], iid)):
            status = "approved"
        else:
            status = "pending"
        rate = rate_for(conn, iid, owner["category"]) if iid != b["instrument_id"] else b["rate"]
        session_id, price = _session_and_price(conn, inst, start, end, owner["category"])
        conn.execute("UPDATE bookings SET instrument_id=?, start=?, end=?, status=?, rate=?, "
                     "purpose=?, session_id=?, price=? WHERE id=?",
                     (iid, fmt(start), fmt(end), status, rate,
                      b["purpose"] if purpose is None else purpose.strip(), session_id, price,
                      booking_id))
        conn.execute("COMMIT")
        return status
    except Exception:
        conn.execute("ROLLBACK")
        raise


def reassign(conn, booking_id: int, actor, new_user_id: int) -> None:
    """Give a booking to someone else (the lab manager or a super user). It is
    re-priced at the new owner's category, as if they had booked it then."""
    if not acts_for_others(actor):
        raise BookingError("only the lab manager or a super user can change who a booking "
                           "is for")
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if b is None or b["status"] not in ("pending", "approved"):
        raise BookingError("this booking can no longer be changed")
    if actor["role"] != "admin" and parse(b["start"]) <= now_local(conn):
        raise BookingError("a booking that has started can only be changed by the lab manager")
    new = conn.execute("SELECT * FROM users WHERE id=?", (new_user_id,)).fetchone()
    if new is None or new["status"] != "active":
        raise BookingError("choose an active user")
    inst = conn.execute("SELECT * FROM instruments WHERE id=?", (b["instrument_id"],)).fetchone()
    rate = rate_for(conn, inst["id"], new["category"])
    price = session_price(conn, b["session_id"], new["category"]) if b["session_id"] else None
    conn.execute("UPDATE bookings SET user_id=?, rate=?, price=? WHERE id=?",
                 (new_user_id, rate, price, booking_id))


def cost(conn, b) -> float:
    """A session's fixed price, or the hours actually elapsed at the hourly rate."""
    if b["price"] is not None:
        return round(b["price"], 2)
    return round(hours(conn, parse(b["start"]), parse(b["end"])) * b["rate"], 2)


def price_label(conn, b) -> str:
    cur = db.setting(conn, "currency")
    if b["price"] is not None:
        return f"{cur}{b['price']:,.2f} per session"
    return f"{cur}{b['rate']:,.2f}/h"
