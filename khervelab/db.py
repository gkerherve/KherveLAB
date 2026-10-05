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
    weekends INTEGER NOT NULL DEFAULT 0
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
    note TEXT NOT NULL DEFAULT ''
);
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
}


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
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
