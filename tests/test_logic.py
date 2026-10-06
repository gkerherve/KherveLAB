from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from khervelab import db, logic, reports


def mk_inst(conn, approval="manual", **kw):
    if kw.get("weekends"):        # weekends on the daytime hours and slots
        kw.setdefault("weekend_mode", "daytime")
        kw.setdefault("weekend_start", kw.get("open_time", "08:00"))
        kw.setdefault("weekend_end", kw.get("close_time", "20:00"))
    cols = {"name": "XPS", "approval": approval, **kw}
    keys = ", ".join(cols)
    cur = conn.execute(f"INSERT INTO instruments ({keys}) VALUES ({', '.join('?' * len(cols))})",
                       tuple(cols.values()))
    return cur.lastrowid


def user(conn, name="alice", role="user", category="Internal"):
    return logic.create_user(conn, name, "password123", name.title(), f"{name}@lab.example",
                             "Group A", category, role=role, status="active")


def nextweekday(hour, days=2):
    d = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def test_manual_instrument_waits_for_approval(conn):
    i = mk_inst(conn)
    u = user(conn)
    res = logic.book(conn, i, u, nextweekday(9), nextweekday(11))
    assert res.status == "pending"
    admin = user(conn, "boss", role="admin")
    logic.decide(conn, res.id, admin, True, "fine")
    row = conn.execute("SELECT * FROM bookings WHERE id=?", (res.id,)).fetchone()
    assert row["status"] == "approved" and row["note"] == "fine"
    with pytest.raises(logic.BookingError):
        logic.decide(conn, res.id, admin, False)


def test_auto_instrument_books_at_once(conn):
    i = mk_inst(conn, "auto")
    assert logic.book(conn, i, user(conn), nextweekday(9), nextweekday(10)).status == "approved"


def test_trained_mode_depends_on_training(conn):
    i = mk_inst(conn, "trained")
    a, b = user(conn, "alice"), user(conn, "bob")
    conn.execute("INSERT INTO authorised VALUES (?, ?)", (a, i))
    assert logic.book(conn, i, a, nextweekday(9), nextweekday(10)).status == "approved"
    assert logic.book(conn, i, b, nextweekday(10), nextweekday(11)).status == "pending"


def test_admin_bookings_are_approved_and_skip_soft_rules(conn):
    i = mk_inst(conn, "manual")
    admin = user(conn, "boss", role="admin")
    sat = datetime.now() + timedelta(days=(5 - datetime.now().weekday()) % 7 or 7)
    res = logic.book(conn, i, admin, sat.replace(hour=22, minute=7), sat.replace(hour=23, minute=0))
    assert res.status == "approved"


def test_clash_blocks_including_pending(conn):
    i = mk_inst(conn)
    a, b = user(conn, "alice"), user(conn, "bob")
    logic.book(conn, i, a, nextweekday(9), nextweekday(11))
    with pytest.raises(logic.BookingError, match="overlaps"):
        logic.book(conn, i, b, nextweekday(10), nextweekday(12))
    logic.book(conn, i, b, nextweekday(11), nextweekday(12))  # touching is fine


def test_rejected_slot_is_free_again(conn):
    i = mk_inst(conn)
    a, b = user(conn, "alice"), user(conn, "bob")
    res = logic.book(conn, i, a, nextweekday(9), nextweekday(11))
    logic.decide(conn, res.id, user(conn, "boss", role="admin"), False)
    assert logic.book(conn, i, b, nextweekday(9), nextweekday(11)).status == "pending"


@pytest.mark.parametrize("start,end,msg", [
    ((7, 0), (9, 0), "closed at .*Daytime 08:00–20:00"),
    ((9, 0), (9, 15), "shortest"),
    ((9, 0), (19, 0), "longest"),
    ((9, 10), (10, 0), "30 min slot of the daytime"),
])
def test_rules(conn, start, end, msg):
    i = mk_inst(conn)
    d = nextweekday(0)
    with pytest.raises(logic.BookingError, match=msg):
        logic.book(conn, i, user(conn), d.replace(hour=start[0], minute=start[1]),
                   d.replace(hour=end[0], minute=end[1]))


def test_past_weekend_and_horizon(conn):
    i = mk_inst(conn, max_days_ahead=10)
    u = user(conn)
    with pytest.raises(logic.BookingError, match="past"):
        logic.book(conn, i, u, nextweekday(9, days=-3), nextweekday(10, days=-3))
    with pytest.raises(logic.BookingError, match="days ahead"):
        logic.book(conn, i, u, nextweekday(9, days=20), nextweekday(10, days=20))
    sat = datetime.now() + timedelta(days=(5 - datetime.now().weekday()) % 7 or 7)
    with pytest.raises(logic.BookingError, match="weekends"):
        logic.book(conn, i, u, sat.replace(hour=9, minute=0), sat.replace(hour=10, minute=0))


def test_overnight_run_on_round_the_clock_instrument(conn):
    i = mk_inst(conn, "auto", open_time="00:00", close_time="24:00", weekends=1, max_minutes=1440)
    s = nextweekday(18)
    assert logic.book(conn, i, user(conn), s, s + timedelta(hours=15)).status == "approved"
    j = mk_inst(conn, "auto")
    with pytest.raises(logic.BookingError, match="closed from .* 20:00"):
        logic.book(conn, j, user(conn, "bob"), s, s + timedelta(hours=15))


def test_until_midnight_respects_closing_time(conn):
    i = mk_inst(conn, "auto")
    s = nextweekday(18)
    with pytest.raises(logic.BookingError, match="closed from .* 20:00"):
        logic.book(conn, i, user(conn), s, s.replace(hour=0) + timedelta(days=1))


def test_cancel_rules(conn):
    i = mk_inst(conn, "auto")
    a, b = user(conn, "alice"), user(conn, "bob")
    res = logic.book(conn, i, a, nextweekday(9), nextweekday(10))
    rb = conn.execute("SELECT * FROM users WHERE id=?", (b,)).fetchone()
    with pytest.raises(logic.BookingError, match="your own"):
        logic.cancel(conn, res.id, rb)
    ra = conn.execute("SELECT * FROM users WHERE id=?", (a,)).fetchone()
    logic.cancel(conn, res.id, ra)
    with pytest.raises(logic.BookingError, match="closed"):
        logic.cancel(conn, res.id, ra)


def test_pending_accounts_cannot_book(conn):
    i = mk_inst(conn, "auto")
    uid = logic.create_user(conn, "new", "password123", "New Person")
    with pytest.raises(logic.BookingError, match="not active"):
        logic.book(conn, i, uid, nextweekday(9), nextweekday(10))


def test_users_and_passwords(conn):
    with pytest.raises(ValueError, match="8 characters"):
        logic.create_user(conn, "x", "short", "X")
    user(conn, "alice")
    with pytest.raises(ValueError, match="taken"):
        logic.create_user(conn, "ALICE", "password123", "Other")
    assert logic.authenticate(conn, "alice", "password123")
    assert logic.authenticate(conn, "alice", "wrong") is None


def test_rate_is_fixed_at_booking_time(conn):
    i = mk_inst(conn, "auto")
    conn.execute("INSERT INTO rates VALUES (?, 'Internal', 50)", (i,))
    conn.execute("INSERT INTO rates VALUES (?, 'Industry', 200)", (i,))
    a = user(conn, "alice", category="Internal")
    c = user(conn, "corp", category="Industry")
    logic.book(conn, i, a, nextweekday(9), nextweekday(11))
    logic.book(conn, i, c, nextweekday(11), nextweekday(12, days=2) + timedelta(minutes=30))
    conn.execute("UPDATE rates SET rate=999 WHERE instrument_id=?", (i,))
    d = nextweekday(0).date()
    rep = reports.build(conn, d, d)
    costs = {ln.user: ln.cost for ln in rep.lines}
    assert costs == {"Alice": 100.0, "Corp": 300.0}
    assert rep.total_cost == 400.0


def test_hours_across_clock_change(conn):
    db.set_setting(conn, "timezone", "Europe/London")
    # 26 Oct 2025: 00:00 to 04:00 local spans the extra hour, 5 h elapsed
    assert logic.hours(conn, datetime(2025, 10, 26, 0), datetime(2025, 10, 26, 4)) == 5
    assert logic.hours(conn, datetime(2025, 3, 30, 0), datetime(2025, 3, 30, 4)) == 3


def test_report_exports(conn):
    i = mk_inst(conn, "auto")
    conn.execute("INSERT INTO rates VALUES (?, 'Internal', 40)", (i,))
    a = user(conn, "alice")
    b = user(conn, "bob")
    logic.book(conn, i, a, nextweekday(9), nextweekday(11), "Co3O4 films")
    logic.book(conn, i, b, nextweekday(13), nextweekday(14))
    d = nextweekday(0).date()
    rep = reports.build(conn, d, d)
    assert [t.key for t in rep.by_user()] == ["Alice", "Bob"]
    csv_text = reports.to_csv(rep)
    assert "Co3O4 films" in csv_text and "120.0" in csv_text
    assert reports.to_xlsx(rep)[:2] == b"PK"
    assert reports.to_pdf(rep)[:4] == b"%PDF"
    only_alice = reports.build(conn, d, d, user_id=a)
    assert only_alice.total_cost == 80.0
    assert reports.to_pdf(only_alice)[:4] == b"%PDF"
    assert reports.to_pdf(reports.build(conn, d, d, user_id=999))[:4] == b"%PDF"  # empty is fine


def test_reschedule_rules(conn):
    i = mk_inst(conn, "manual")
    a, b = user(conn, "alice"), user(conn, "bob")
    boss = user(conn, "boss", role="admin")
    res = logic.book(conn, i, a, nextweekday(9), nextweekday(11))
    logic.decide(conn, res.id, boss, True)
    row = lambda uid: conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    # moving an approved manual booking sends it back for approval
    assert logic.reschedule(conn, res.id, row(a), nextweekday(13), nextweekday(15)) == "pending"
    with pytest.raises(logic.BookingError, match="your own"):
        logic.reschedule(conn, res.id, row(b), nextweekday(9), nextweekday(10))
    other = logic.book(conn, i, b, nextweekday(16), nextweekday(17))
    with pytest.raises(logic.BookingError, match="overlaps"):
        logic.reschedule(conn, res.id, row(a), nextweekday(14), nextweekday(17))
    # the manager's change keeps the status
    logic.decide(conn, other.id, boss, True)
    assert logic.reschedule(conn, other.id, row(boss), nextweekday(17), nextweekday(18)) == \
        "approved"


def test_reschedule_to_other_instrument_takes_its_rate(conn):
    i, j = mk_inst(conn, "auto"), mk_inst(conn, "auto", name="BET")
    conn.execute("INSERT INTO rates VALUES (?, 'Internal', 10)", (i,))
    conn.execute("INSERT INTO rates VALUES (?, 'Internal', 30)", (j,))
    a = user(conn)
    res = logic.book(conn, i, a, nextweekday(9), nextweekday(10))
    ua = conn.execute("SELECT * FROM users WHERE id=?", (a,)).fetchone()
    logic.reschedule(conn, res.id, ua, nextweekday(9), nextweekday(10), instrument_id=j)
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (res.id,)).fetchone()
    assert b["instrument_id"] == j and b["rate"] == 30


def test_manager_books_on_behalf_of_a_user(conn):
    i = mk_inst(conn, "manual")
    conn.execute("INSERT INTO rates VALUES (?, 'Industry', 200)", (i,))
    corp = user(conn, "corp", category="Industry")
    boss = user(conn, "boss", role="admin")
    sat = datetime.now() + timedelta(days=(5 - datetime.now().weekday()) % 7 or 7)
    res = logic.book(conn, i, corp, sat.replace(hour=9, minute=0), sat.replace(hour=10, minute=0),
                     actor_id=boss)
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (res.id,)).fetchone()
    assert res.status == "approved" and b["user_id"] == corp and b["rate"] == 200
    with pytest.raises(logic.BookingError, match="only the lab manager"):
        logic.book(conn, i, corp, nextweekday(9), nextweekday(10), actor_id=user(conn, "x"))


def test_long_slots_count_from_opening_time(conn):
    # 4.5 h slots on an instrument open 08:00-17:00: 08:00-12:30 and 12:30-17:00
    i = mk_inst(conn, "auto", slot_minutes=270, min_minutes=270, max_minutes=540,
                open_time="08:00", close_time="17:00")
    u = user(conn)
    d = nextweekday(0)
    assert logic.book(conn, i, u, d.replace(hour=8), d.replace(hour=12, minute=30)).status \
        == "approved"
    logic.book(conn, i, u, d.replace(hour=12, minute=30), d.replace(hour=17))
    with pytest.raises(logic.BookingError, match="4.5 h slot of the daytime, counted from 08:00"):
        logic.book(conn, i, user(conn, "bob"), d.replace(hour=9) + timedelta(days=1),
                   d.replace(hour=13, minute=30) + timedelta(days=1))


def test_24_hour_slot_and_overnight_9_hour_run(conn):
    i = mk_inst(conn, "auto", slot_minutes=1440, min_minutes=1440, max_minutes=4320,
                open_time="00:00", close_time="24:00", weekends=1)
    u = user(conn)
    d = nextweekday(0)
    logic.book(conn, i, u, d, d + timedelta(days=1))
    with pytest.raises(logic.BookingError, match="24 h slot"):
        logic.book(conn, i, u, d + timedelta(days=1), d + timedelta(days=2, hours=12))
    j = mk_inst(conn, "auto", name="TGA", slot_minutes=540, min_minutes=540, max_minutes=1440,
                open_time="00:00", close_time="24:00", weekends=1)
    # 9 h slots from midnight: 00:00, 09:00, 18:00; an 18:00 run ends 03:00 next day
    logic.book(conn, j, u, d.replace(hour=18), d.replace(hour=3) + timedelta(days=1))


def test_fmt_duration():
    assert [logic.fmt_duration(m) for m in (30, 60, 270, 540, 1440, 75)] == \
        ["30 min", "1 h", "4.5 h", "9 h", "24 h", "1.25 h"]


def test_labs_on_old_default_colours_move_to_the_new_ones(conn):
    db.set_setting(conn, "colour_closed", "#3d4249")      # the old dark default
    db.set_setting(conn, "colour_free", "#123456")        # a colour the manager chose
    db.init(conn)
    assert db.setting(conn, "colour_closed") == db.DEFAULT_SETTINGS["colour_closed"]
    assert db.setting(conn, "colour_free") == "#123456"
