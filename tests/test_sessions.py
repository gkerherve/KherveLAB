from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from khervelab import logic, reports
from tests.test_logic import mk_inst, nextweekday, user


def lab_sessions(conn, approval="auto"):
    """08:00-12:30 and 12:30-17:00 on weekdays at fixed prices, plus an
    evening run 17:00-08:00 charged by the hour."""
    i = mk_inst(conn, approval, booking_mode="sessions", max_days_ahead=60)
    conn.execute("INSERT INTO rates VALUES (?, 'Internal', 10)", (i,))
    conn.execute("INSERT INTO rates VALUES (?, 'Industry', 50)", (i,))
    specs = [logic.SessionSpec(n, s, e, d, {}) for n, s, e, d in
             logic.SESSION_TEMPLATES["Two day sessions and an evening run"]]
    specs[0].prices = {"Internal": 200.0, "Industry": 900.0}
    specs[1].prices = {"Internal": 200.0, "Industry": 900.0}
    logic.save_sessions(conn, i, specs)
    return i


def at(d: datetime, hhmm: str, days: int = 0) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return d.replace(hour=h, minute=m) + timedelta(days=days)


def test_session_lengths_and_labels():
    assert logic.session_hours("08:00", "12:30") == 4.5
    assert logic.session_hours("17:00", "08:00") == 15
    assert logic.session_hours("08:00", "08:00") == 24
    assert logic.days_label("01234") == "weekdays" and logic.days_label("06") == "Mon, Sun"


def test_occurrences_include_last_nights_evening(conn):
    i = lab_sessions(conn)
    d = nextweekday(0, days=3)
    occ = logic.occurrences(conn, i, at(d, "00:00"), at(d, "23:59"))
    names = [(o.name, o.start.strftime("%a %H:%M"), o.end.strftime("%a %H:%M")) for o in occ]
    if d.weekday() > 0:   # the previous weekday's evening run ends this morning
        assert names[0][0] == "Evening and overnight" and names[0][2].endswith("08:00")
    assert [n[0] for n in names[-3:]] == ["Morning", "Afternoon", "Evening and overnight"]
    assert occ[-1].end - occ[-1].start == timedelta(hours=15)


def test_only_whole_sessions_can_be_booked(conn):
    i = lab_sessions(conn)
    u = user(conn)
    d = nextweekday(0)
    with pytest.raises(logic.BookingError, match="booked by session"):
        logic.book(conn, i, u, at(d, "09:00"), at(d, "11:00"))
    res = logic.book(conn, i, u, at(d, "08:00"), at(d, "12:30"))
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (res.id,)).fetchone()
    assert b["session_id"] and b["price"] == 200.0 and logic.cost(conn, b) == 200.0


def test_price_depends_on_category_and_falls_back_to_hourly(conn):
    i = lab_sessions(conn)
    corp = user(conn, "corp", category="Industry")
    d = nextweekday(0)
    logic.book(conn, i, corp, at(d, "08:00"), at(d, "12:30"))
    logic.book(conn, i, corp, at(d, "17:00"), at(d, "08:00", days=1))     # no fixed price
    rows = conn.execute("SELECT * FROM bookings ORDER BY start").fetchall()
    assert [logic.cost(conn, b) for b in rows] == [900.0, 15 * 50.0]
    assert logic.price_label(conn, rows[0]).endswith("per session")


def test_book_range_takes_every_session_it_touches(conn):
    i = lab_sessions(conn)
    u, v = user(conn, "alice"), user(conn, "bob")
    d = nextweekday(0)
    logic.book(conn, i, v, at(d, "12:30"), at(d, "17:00"))
    made, problems = logic.book_range(conn, i, u, at(d, "10:00"), at(d, "18:00"))
    assert len(made) == 2                  # morning and evening; afternoon is taken
    assert len(problems) == 1 and "Afternoon" in problems[0]
    with pytest.raises(logic.BookingError):
        logic.book_range(conn, i, u, at(d, "13:00"), at(d, "14:00"))


def test_manager_can_still_block_free_time(conn):
    i = lab_sessions(conn)
    boss = user(conn, "boss", role="admin")
    res = logic.book(conn, i, boss, at(nextweekday(0), "10:00"), at(nextweekday(0), "11:00"))
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (res.id,)).fetchone()
    assert b["session_id"] is None and b["price"] is None


def test_session_reports(conn):
    i = lab_sessions(conn)
    u = user(conn)
    d = nextweekday(0)
    logic.book(conn, i, u, at(d, "08:00"), at(d, "12:30"))
    logic.book(conn, i, u, at(d, "17:00"), at(d, "08:00", days=1))
    rep = reports.build(conn, d.date(), d.date())
    assert rep.total_cost == 200.0 + 150.0
    assert [ln.session for ln in rep.lines] == ["Morning", "Evening and overnight"]
    assert "Morning" in reports.to_csv(rep)
    assert reports.to_pdf(rep)[:4] == b"%PDF"


def test_saving_sessions_keeps_ids_and_validates(conn):
    i = lab_sessions(conn)
    rows = logic.sessions(conn, i)
    specs = [logic.SessionSpec(r["name"], r["start_time"], r["end_time"], r["days"], {}, r["id"])
             for r in rows[:2]]
    logic.save_sessions(conn, i, specs)
    assert [r["id"] for r in logic.sessions(conn, i)] == [rows[0]["id"], rows[1]["id"]]
    with pytest.raises(ValueError, match="at least one day"):
        logic.save_sessions(conn, i, [logic.SessionSpec("x", "08:00", "09:00", "", {})])
    with pytest.raises(ValueError, match="not a time"):
        logic.save_sessions(conn, i, [logic.SessionSpec("x", "25:00", "09:00", "0", {})])
