from __future__ import annotations

from datetime import timedelta

import pytest

from khervelab import logic
from tests.test_logic import mk_inst, nextweekday, user
from tests.test_periods import at, lab, weekday


def test_slots_of_an_empty_day(conn):
    _, inst = lab(conn, evening="block")
    d = weekday()
    got = [(a.strftime("%H:%M"), b.strftime("%H:%M"), label)
           for a, b, label in logic.slots(conn, inst, at(d, "00:00"), at(d, "00:00", 1))]
    # last night's evening, the two 4.5 h day slots, tonight's evening
    assert got[-3:] == [("08:00", "12:30", "Daytime"), ("12:30", "17:00", "Daytime"),
                        ("17:00", "08:00", "Evening")]


def test_short_slots_are_listed_one_by_one(conn):
    i = mk_inst(conn, "auto", slot_minutes=30, open_time="08:00", close_time="10:00")
    inst = conn.execute("SELECT * FROM instruments WHERE id=?", (i,)).fetchone()
    d = nextweekday(0)
    assert len(logic.slots(conn, inst, d, d + timedelta(days=1))) == 4


def test_out_of_order_blocks_and_problem_warns(conn):
    i, _ = lab(conn, evening="block")
    u, boss = user(conn), user(conn, "boss", role="admin")
    d = weekday()
    logic.report_issue(conn, i, "down", at(d, "10:00"), None, "X-ray source tripped", u)
    with pytest.raises(logic.BookingError, match="out of order until fixed: X-ray source"):
        logic.book(conn, i, u, at(d, "12:30"), at(d, "17:00"))
    assert logic.issue_state(conn, i, at(d, "08:00"), at(d, "12:30")) == "down"
    # the manager can still book it (e.g. for the repair)
    logic.book(conn, i, boss, at(d, "12:30"), at(d, "17:00"))
    (issue,) = logic.issues(conn, i, at(d, "00:00"), at(d, "23:00"))
    logic.resolve_issue(conn, issue["id"], at(d, "17:00"))
    logic.book(conn, i, u, at(d, "17:00"), at(d, "08:00", 1))       # fixed: bookable again
    j, _ = lab(conn, name="BET")
    logic.report_issue(conn, j, "problem", at(d, "08:00"), at(d, "12:30"), "noisy", u)
    assert logic.issue_state(conn, j, at(d, "08:00"), at(d, "12:30")) == "problem"
    logic.book(conn, j, u, at(d, "08:00"), at(d, "12:30"))          # still bookable


def test_resolving_a_future_report_removes_it(conn):
    i, _ = lab(conn)
    d = weekday()
    iid = logic.report_issue(conn, i, "down", at(d, "08:00", 5), None, "", None)
    logic.resolve_issue(conn, iid)
    assert conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 0


def test_issue_end_must_follow_start(conn):
    i, _ = lab(conn)
    d = weekday()
    with pytest.raises(ValueError):
        logic.report_issue(conn, i, "problem", at(d, "10:00"), at(d, "09:00"), "", None)
