from __future__ import annotations

import sqlite3

import pytest

from khervelab import db, logic
from tests.test_logic import mk_inst, nextweekday, user


def row(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def test_superuser_books_for_someone_with_their_rules(conn):
    i = mk_inst(conn, "trained")
    conn.execute("INSERT INTO rates VALUES (?, 'Industry', 200)", (i,))
    sup = user(conn, "sue", role="superuser")
    corp = user(conn, "corp", category="Industry")
    res = logic.book(conn, i, corp, nextweekday(9), nextweekday(11), actor_id=sup)
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (res.id,)).fetchone()
    # corp is not trained: the request waits, charged at corp's rate
    assert res.status == "pending" and b["user_id"] == corp and b["rate"] == 200
    conn.execute("INSERT INTO authorised VALUES (?, ?)", (corp, i))
    assert logic.book(conn, i, corp, nextweekday(13), nextweekday(14),
                      actor_id=sup).status == "approved"
    # unlike the manager, a super user is held to the person's rules
    with pytest.raises(logic.BookingError, match="closed at"):
        logic.book(conn, i, corp, nextweekday(6), nextweekday(7), actor_id=sup)


def test_ordinary_users_cannot_act_for_others(conn):
    i = mk_inst(conn, "auto")
    a, b = user(conn, "alice"), user(conn, "bob")
    with pytest.raises(logic.BookingError, match="super user"):
        logic.book(conn, i, b, nextweekday(9), nextweekday(10), actor_id=a)
    res = logic.book(conn, i, b, nextweekday(9), nextweekday(10))
    with pytest.raises(logic.BookingError, match="your own"):
        logic.cancel(conn, res.id, row(conn, a))
    with pytest.raises(logic.BookingError, match="super user"):
        logic.reassign(conn, res.id, row(conn, a), a)


def test_superuser_moves_cancels_and_reassigns(conn):
    i = mk_inst(conn, "auto")
    conn.execute("INSERT INTO rates VALUES (?, 'Internal', 10)", (i,))
    conn.execute("INSERT INTO rates VALUES (?, 'Industry', 100)", (i,))
    sup = row(conn, user(conn, "sue", role="superuser"))
    a = user(conn, "alice")
    corp = user(conn, "corp", category="Industry")
    res = logic.book(conn, i, a, nextweekday(9), nextweekday(10))
    logic.reschedule(conn, res.id, sup, nextweekday(11), nextweekday(12))
    logic.reassign(conn, res.id, sup, corp)
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (res.id,)).fetchone()
    assert b["user_id"] == corp and b["rate"] == 100 and b["start"].endswith("11:00")
    assert logic.cost(conn, b) == 100.0
    logic.cancel(conn, res.id, sup)
    assert conn.execute("SELECT status FROM bookings").fetchone()[0] == "cancelled"


def test_old_lab_database_gains_the_superuser_role(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            password_hash TEXT NOT NULL, full_name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',
            group_name TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
            status TEXT NOT NULL DEFAULT 'pending', created TEXT NOT NULL);
        INSERT INTO users VALUES (7, 'boss', 'x', 'Boss', '', '', '', 'admin', 'active', '2026');
    """)
    old.commit()
    old.close()
    conn = db.connect(path)
    db.init(conn)
    conn.execute("UPDATE users SET role='superuser' WHERE id=7")          # allowed now
    assert tuple(conn.execute("SELECT id, username, role FROM users").fetchone()) == (7, "boss",
                                                                              "superuser")
