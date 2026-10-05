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

def rate_for(conn, instrument_id: int, category: str) -> float:
    r = conn.execute("SELECT rate FROM rates WHERE instrument_id=? AND category=?",
                     (instrument_id, category)).fetchone()
    return float(r["rate"]) if r else 0.0


def rates(conn, instrument_id: int) -> dict[str, float]:
    return {c: rate_for(conn, instrument_id, c) for c in db.categories(conn)}


# -- bookings ---------------------------------------------------------------------------

def check(conn, inst: sqlite3.Row, user: sqlite3.Row, start: datetime, end: datetime,
          exclude: int | None = None, now: datetime | None = None) -> list[str]:
    """Every reason the slot cannot be booked; empty when it can. The
    administrator is held only to the hard rules (no clash, end after start)."""
    errors = []
    if end <= start:
        return ["the end must be after the start"]
    admin = user["role"] == "admin"
    now = now or now_local(conn)
    if not inst["active"]:
        errors.append(f"{inst['name']} is not available for booking")
    if not admin:
        if start < now:
            errors.append("the start is in the past")
        if start > now + timedelta(days=inst["max_days_ahead"]):
            errors.append(f"bookings open {inst['max_days_ahead']} days ahead")
        minutes = (end - start).total_seconds() / 60
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
         purpose: str = "", now: datetime | None = None) -> Created:
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
        errors = check(conn, inst, user, start, end, now=now)
        if errors:
            raise BookingError("; ".join(errors))
        if user["role"] == "admin" or inst["approval"] == "auto" or (
                inst["approval"] == "trained" and is_authorised(conn, user_id, instrument_id)):
            status = "approved"
        else:
            status = "pending"
        rate = rate_for(conn, instrument_id, user["category"])
        stamp = fmt(now_local(conn))
        cur = conn.execute(
            "INSERT INTO bookings (instrument_id, user_id, start, end, purpose, status, rate, "
            "created, decided_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (instrument_id, user_id, fmt(start), fmt(end), purpose.strip(), status, rate, stamp,
             stamp if status == "approved" else None))
        conn.execute("COMMIT")
        return Created(cur.lastrowid, status)
    except Exception:
        conn.execute("ROLLBACK")
        raise


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


def cost(conn, b: sqlite3.Row) -> float:
    return round(hours(conn, parse(b["start"]), parse(b["end"])) * b["rate"], 2)
