"""SQLite storage: one file holds the whole lab.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'disabled')),
    created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    colour TEXT NOT NULL DEFAULT '#1f6feb',
    active INTEGER NOT NULL DEFAULT 1,
    approval TEXT NOT NULL DEFAULT 'manual' CHECK (approval IN ('auto', 'trained', 'manual')),
    slot_minutes INTEGER NOT NULL DEFAULT 30,
    min_minutes INTEGER NOT NULL DEFAULT 30,
    max_minutes INTEGER NOT NULL DEFAULT 480,
    max_days_ahead INTEGER NOT NULL DEFAULT 60,
    open_time TEXT NOT NULL DEFAULT '08:00',
    close_time TEXT NOT NULL DEFAULT '20:00',
    weekends INTEGER NOT NULL DEFAULT 0,
    booking_mode TEXT NOT NULL DEFAULT 'free' CHECK (booking_mode IN ('free', 'sessions')),
    -- free-time periods besides the daytime (open_time-close_time, Mon-Fri):
    -- mode 'closed', 'block' (one booking for the whole period), 'daytime'
    -- (the daytime slot length) or 'own' (its own slot length below)
    evening_mode TEXT NOT NULL DEFAULT 'closed',
    evening_start TEXT NOT NULL DEFAULT '17:00',
    evening_end TEXT NOT NULL DEFAULT '08:00',
    evening_slot INTEGER NOT NULL DEFAULT 60,
    weekend_mode TEXT NOT NULL DEFAULT 'closed',
    weekend_start TEXT NOT NULL DEFAULT '08:00',
    weekend_end TEXT NOT NULL DEFAULT '20:00',
    weekend_slot INTEGER NOT NULL DEFAULT 60,
    -- 'daily': a window on Saturday and one on Sunday; 'whole': one window from
    -- Saturday weekend_start to Monday weekend_end (e.g. a 48 h weekend run)
    weekend_span TEXT NOT NULL DEFAULT 'daily'
);

-- fixed sessions, for instruments booked by session rather than free time;
-- end_time <= start_time means the session ends the next day (an evening run)
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    days TEXT NOT NULL DEFAULT '01234',    -- weekdays it starts on, Monday = 0
    sort INTEGER NOT NULL DEFAULT 0
);

-- fixed price per session and user category; no row means the hourly rate applies
CREATE TABLE IF NOT EXISTS session_prices (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    price REAL NOT NULL,
    PRIMARY KEY (session_id, category)
);

-- hourly rate per instrument and user category
CREATE TABLE IF NOT EXISTS rates (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    rate REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_id, category)
);

-- users trained on an instrument (approval mode 'trained' books them at once)
CREATE TABLE IF NOT EXISTS authorised (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    start TEXT NOT NULL,            -- lab-local wall clock, 'YYYY-MM-DDTHH:MM'
    end TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    rate REAL NOT NULL DEFAULT 0,   -- hourly rate fixed when booked, so later rate changes
                                    -- never rewrite past charges
    created TEXT NOT NULL,
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    price REAL                      -- fixed session price; NULL means hours x rate
);
-- reported trouble with an instrument: 'problem' (usable, take care) or
-- 'down' (out of order: cannot be booked); end NULL = until fixed
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('problem', 'down')),
    start TEXT NOT NULL,
    end TEXT,
    note TEXT NOT NULL DEFAULT '',
    reported_by INTEGER REFERENCES users(id),
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS issues_instrument ON issues(instrument_id, start);

CREATE INDEX IF NOT EXISTS bookings_slot ON bookings(instrument_id, start);
CREATE INDEX IF NOT EXISTS bookings_user ON bookings(user_id, start);
"""

DEFAULT_SETTINGS = {
    "lab_name": "My lab",
    "currency": "£",
    "timezone": "Europe/London",
    "categories": "Internal\nExternal academic\nIndustry",
    "account_approval": "1",   # new accounts wait for the administrator
    "show_names": "1",         # logged-in users see who booked a slot
    # calendar colours for slots
    "colour_free": "#f7f9fc",     # raised near-white card
    "colour_closed": "#e4e8ee",   # flat, calm background behind the cards
    "colour_booked": "#3b7ddd",
    "colour_problem": "#f2b33d",
    "colour_down": "#d9534f",
}


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# columns added after the first release, so older lab.db files gain them
MIGRATIONS = [
    ("instruments", "booking_mode",
     "TEXT NOT NULL DEFAULT 'free' CHECK (booking_mode IN ('free', 'sessions'))"),
    ("bookings", "session_id", "INTEGER REFERENCES sessions(id) ON DELETE SET NULL"),
    ("bookings", "price", "REAL"),
    ("instruments", "evening_mode", "TEXT NOT NULL DEFAULT 'closed'"),
    ("instruments", "evening_start", "TEXT NOT NULL DEFAULT '17:00'"),
    ("instruments", "evening_end", "TEXT NOT NULL DEFAULT '08:00'"),
    ("instruments", "evening_slot", "INTEGER NOT NULL DEFAULT 60"),
    ("instruments", "weekend_mode", "TEXT NOT NULL DEFAULT 'closed'"),
    ("instruments", "weekend_start", "TEXT NOT NULL DEFAULT '08:00'"),
    ("instruments", "weekend_end", "TEXT NOT NULL DEFAULT '20:00'"),
    ("instruments", "weekend_slot", "INTEGER NOT NULL DEFAULT 60"),
    ("instruments", "weekend_span", "TEXT NOT NULL DEFAULT 'daily'"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    added = set()
    for table, column, decl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            added.add(column)
    if "weekend_mode" in added:
        # "bookable at weekends" used the daytime hours and slots
        conn.execute("UPDATE instruments SET weekend_mode='daytime', weekend_start=open_time, "
                     "weekend_end=close_time WHERE weekends=1")


# defaults later changed: a lab still on the old default gets the new one
REPLACED_DEFAULTS = [("colour_free", "#d8dde3"), ("colour_free", "#eef1f4"),
                     ("colour_closed", "#3d4249"), ("colour_booked", "#1f6feb"),
                     ("colour_problem", "#e3b341"), ("colour_down", "#cf222e")]


def init(conn: sqlite3.Connection) -> None:
    # tables first (CREATE IF NOT EXISTS leaves old ones alone), then new columns
    conn.executescript(SCHEMA.split("CREATE INDEX")[0])
    _migrate(conn)
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    for key, old_default in REPLACED_DEFAULTS:
        conn.execute("UPDATE settings SET value=? WHERE key=? AND value=?",
                     (DEFAULT_SETTINGS[key], key, old_default))
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('secret_key', ?)",
                 (secrets.token_hex(32),))


def setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                 "SET value=excluded.value", (key, value))


def categories(conn: sqlite3.Connection) -> list[str]:
    return [c.strip() for c in setting(conn, "categories").splitlines() if c.strip()]


def backup(conn: sqlite3.Connection, target: Path | str) -> None:
    dst = sqlite3.connect(target)
    with dst:
        conn.backup(dst)
    dst.close()
