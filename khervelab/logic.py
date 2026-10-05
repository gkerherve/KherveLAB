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


def rate_for(conn, instrument_id: int, category: str) -> float:
    r = conn.execute("SELECT rate FROM rates WHERE instrument_id=? AND category=?",
                     (instrument_id, category)).fetchone()
    return float(r["rate"]) if r else 0.0


def rates(conn, instrument_id: int) -> dict[str, float]:
    return {c: rate_for(conn, instrument_id, c) for c in db.categories(conn)}


# -- bookings ---------------------------------------------------------------------------

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
        if minutes < inst["min_minutes"]:
            errors.append(f"the shortest booking is {inst['min_minutes']} min")
        if minutes > inst["max_minutes"]:
            errors.append(f"the longest booking is {inst['max_minutes']} min")
        slot = inst["slot_minutes"]
        for t in (start, end):
            if (t.hour * 60 + t.minute) % slot:
                errors.append(f"times must fall on {slot}-minute slots")
                break
        open_t, close_t = inst["open_time"], inst["close_time"]
        last_day = (end - timedelta(minutes=1)).date()
        day = start.date()
        while day <= last_day:
            if day.weekday() >= 5 and not inst["weekends"]:
                errors.append(f"{inst['name']} cannot be booked at weekends")
                break
            day += timedelta(days=1)
        open_m, close_m = minutes_of(open_t), minutes_of(close_t)
        if not (open_m == 0 and close_m >= 1439):        # not open around the clock
            if start.date() != last_day:
                errors.append(f"bookings must stay within one day ({open_t}–{close_t})")
            else:
                s_m = start.hour * 60 + start.minute
                e_m = 1440 if end.date() > start.date() else end.hour * 60 + end.minute
                if s_m < open_m or e_m > close_m:
                    errors.append(f"{inst['name']} can be booked between {open_t} and {close_t}")
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
            if actor is None or actor["role"] != "admin":
                raise BookingError("only the lab manager can book for someone else")
        errors = check(conn, inst, actor, start, end, now=now)
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
        if b["user_id"] != user["id"]:
            raise BookingError("you can only cancel your own bookings")
        if parse(b["start"]) <= now_local(conn):
            raise BookingError("a booking that has started can only be cancelled by the "
                               "administrator")
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
            if b["user_id"] != user["id"]:
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
