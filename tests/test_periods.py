from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from khervelab import logic
from tests.test_logic import mk_inst, user


def weekday(n: int = 2) -> datetime:
    d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=n)
    while d.weekday() != 1:            # a Tuesday: weekdays either side
        d += timedelta(days=1)
    return d


def saturday() -> datetime:
    d = weekday()
    return d + timedelta(days=(5 - d.weekday()) % 7)


def at(d: datetime, hhmm: str, days: int = 0) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return d.replace(hour=h, minute=m) + timedelta(days=days)


def lab(conn, evening="block", weekend="closed", **kw):
    """Daytime 08:00-17:00 in 4.5 h slots; evening 17:00-08:00."""
    cols = dict(open_time="08:00", close_time="17:00", slot_minutes=270, min_minutes=270,
                max_minutes=540, evening_mode=evening, evening_start="17:00",
                evening_end="08:00", weekend_mode=weekend, weekend_start="08:00",
                weekend_end="08:00", max_days_ahead=60)
    cols.update(kw)
    i = mk_inst(conn, "auto", **cols)
    return i, conn.execute("SELECT * FROM instruments WHERE id=?", (i,)).fetchone()


def test_periods_of_a_weekday(conn):
    _, inst = lab(conn)
    d = weekday()
    ps = logic.periods(inst, at(d, "00:00"), at(d, "23:59"))
    got = [(p.kind, p.start.strftime("%a %H:%M"), p.end.strftime("%a %H:%M"), p.slot) for p in ps]
    assert got[-2:] == [("day", f"{d:%a} 08:00", f"{d:%a} 17:00", 270),
                        ("evening", f"{d:%a} 17:00", (d + timedelta(days=1)).strftime("%a") +
                         " 08:00", None)]
    assert got[0][0] == "evening"       # last night's evening runs until 08:00


def test_evening_as_one_booking(conn):
    i, _ = lab(conn, evening="block")
    u = user(conn)
    d = weekday()
    with pytest.raises(logic.BookingError, match="booked as one block"):
        logic.book(conn, i, u, at(d, "17:00"), at(d, "22:00"))
    res = logic.book(conn, i, u, at(d, "17:00"), at(d, "08:00", 1))     # 15 h, over "longest"
    assert res.status == "approved"


def test_evening_on_daytime_slots_and_own_slots(conn):
    i, _ = lab(conn, evening="daytime")
    u = user(conn)
    d = weekday()
    logic.book(conn, i, u, at(d, "17:00"), at(d, "21:30"))              # one 4.5 h slot
    with pytest.raises(logic.BookingError, match="4.5 h slot of the evening"):
        logic.book(conn, i, u, at(d, "21:30", 1), at(d, "23:00", 1))
    j, _ = lab(conn, evening="own", evening_slot=60, name="BET")
    logic.book(conn, j, u, at(d, "19:00"), at(d, "21:00"))
    with pytest.raises(logic.BookingError, match="1 h slot of the evening"):
        logic.book(conn, j, u, at(d, "19:30", 1), at(d, "21:00", 1))


def test_closed_evening_and_closed_weekend(conn):
    i, _ = lab(conn, evening="closed", weekend="closed")
    u = user(conn)
    d = weekday()
    with pytest.raises(logic.BookingError, match="closed at"):
        logic.book(conn, i, u, at(d, "18:00"), at(d, "19:00"))
    with pytest.raises(logic.BookingError, match="cannot be booked at weekends"):
        logic.book(conn, i, u, at(saturday(), "08:00"), at(saturday(), "12:30"))


def test_weekend_modes(conn):
    s = saturday()
    u = user(conn)
    i, _ = lab(conn, weekend="block")                     # Sat 08:00 -> Sun 08:00 as one
    with pytest.raises(logic.BookingError, match="weekend .* one block"):
        logic.book(conn, i, u, at(s, "08:00"), at(s, "20:00"))
    logic.book(conn, i, u, at(s, "08:00"), at(s, "08:00", 1))
    j, _ = lab(conn, weekend="own", weekend_slot=720, name="TGA")      # 12 h weekend slots
    logic.book(conn, j, u, at(s, "20:00"), at(s, "08:00", 1))
    with pytest.raises(logic.BookingError, match="12 h slot of the weekend"):
        logic.book(conn, j, u, at(s, "09:00", 1), at(s, "21:00", 1))


def test_friday_evening_hands_over_to_the_weekend(conn):
    # Friday's evening ends Saturday 08:00, where the weekend starts
    i, inst = lab(conn, evening="block", weekend="block")
    fri = saturday() - timedelta(days=1)
    ps = logic.periods(inst, at(fri, "16:00"), at(fri, "09:00", 1))
    assert [p.kind for p in ps] == ["day", "evening", "weekend"]
    u = user(conn)
    # a booking may run on from one period into the next
    logic.book(conn, i, u, at(fri, "17:00"), at(fri, "08:00", 2))


def test_daytime_slots_then_evening_block_together(conn):
    i, _ = lab(conn, evening="block")
    d = weekday()
    logic.book(conn, i, user(conn), at(d, "12:30"), at(d, "08:00", 1))


def test_snapping(conn):
    _, inst = lab(conn, evening="block")
    d = weekday()
    assert logic.snap_start(inst, at(d, "10:10")) == at(d, "08:00")
    assert logic.snap_start(inst, at(d, "13:00")) == at(d, "12:30")
    assert logic.snap_start(inst, at(d, "23:00")) == at(d, "17:00")       # whole evening
    assert logic.snap_end(inst, at(d, "08:00"), at(d, "11:00")) == at(d, "12:30")
    assert logic.snap_end(inst, at(d, "17:00"), at(d, "19:00")) == at(d, "08:00", 1)


def test_describe(conn):
    _, inst = lab(conn, evening="block", weekend="own", weekend_slot=720)
    text = logic.describe_periods(inst)
    assert text == ("Daytime 08:00–17:00 in 4.5 h slots · Evening 17:00–08:00 (next day) as one "
                    "booking · Weekend 08:00–08:00 (next day) each day in 12 h slots")
