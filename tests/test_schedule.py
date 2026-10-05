from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from khervelab.core import schedule
from khervelab.core.models import Booking, Instrument, User
from khervelab.core.config import parse_range

LON = ZoneInfo("Europe/London")
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=LON)


def inst(**kw) -> Instrument:
    hours = {d: (parse_range("08:00-20:00", "x", "k"),) for d in ("mon", "tue", "wed", "thu", "fri")}
    base = dict(id="xps", name="XPS", bookable_hours=hours, slot_granularity_minutes=30,
                min_booking_minutes=60, max_booking_minutes=480, max_advance_days=28,
                max_concurrent_per_user=2, requires_permission=True)
    base.update(kw)
    return Instrument(**base)


def bk(day, h1, h2, user="u-1", instrument="xps", **kw) -> Booking:
    s = datetime(2026, 10, day, int(h1), int((h1 % 1) * 60), tzinfo=LON)
    e = datetime(2026, 10, day, int(h2), int((h2 % 1) * 60), tzinfo=LON)
    return Booking(instrument, s, e, user, **kw)


def rules(vs):
    return {v.rule for v in vs}


TRAINED = User("u-1", "Alice", permissions=("xps",))


def test_clean_booking_passes():
    assert schedule.check(bk(7, 9, 13), inst(), [], LON, NOW, TRAINED) == []


def test_outside_bookable_hours_blocks():
    vs = schedule.check(bk(7, 6, 9), inst(), [], LON, NOW, TRAINED)
    assert "bookable_hours" in rules(vs) and schedule.blocking(vs)


def test_weekend_blocks_on_weekday_instrument():
    assert "bookable_hours" in rules(schedule.check(bk(10, 9, 12), inst(), [], LON, NOW, TRAINED))


def test_duration_limits():
    assert "min_duration" in rules(schedule.check(bk(7, 9, 9.5), inst(), [], LON, NOW, TRAINED))
    assert "max_duration" in rules(schedule.check(bk(7, 8, 19), inst(), [], LON, NOW, TRAINED))


def test_maintenance_ignores_duration_limits():
    vs = schedule.check(bk(7, 8, 19, kind="maintenance"), inst(), [], LON, NOW, TRAINED)
    assert vs == []


def test_overlap_names_the_clash():
    other = bk(7, 12, 14, user="u-2")
    vs = schedule.check(bk(7, 9, 13), inst(), [other], LON, NOW, TRAINED)
    clash = [v for v in vs if v.rule == "overlap"]
    assert clash and clash[0].clash == other and clash[0].blocking


def test_touching_bookings_do_not_overlap():
    assert schedule.check(bk(7, 9, 13), inst(), [bk(7, 13, 15, user="u-2")], LON, NOW, TRAINED) == []


def test_other_instrument_does_not_clash():
    assert schedule.check(bk(7, 9, 13), inst(), [bk(7, 9, 13, instrument="tem")], LON, NOW,
                          TRAINED) == []


def test_editing_ignores_own_old_position():
    old = bk(7, 9, 13)
    assert schedule.check(bk(7, 10, 14), inst(), [old], LON, NOW, TRAINED, ignore=old) == []


def test_advance_window():
    late = Booking("xps", NOW + timedelta(days=40), NOW + timedelta(days=40, hours=2), "u-1")
    assert "advance_window" in rules(schedule.check(late, inst(), [], LON, NOW, TRAINED))


def test_concurrent_limit_warns_and_override_silences():
    existing = [bk(7, 9, 11), bk(8, 9, 11)]
    vs = schedule.check(bk(9, 9, 11), inst(), existing, LON, NOW, TRAINED)
    assert rules(vs) == {"concurrent_limit"} and not schedule.blocking(vs)
    ov = bk(9, 9, 11, override=("concurrent_limit",))
    assert schedule.check(ov, inst(), existing, LON, NOW, TRAINED) == []


def test_permission_warns_for_untrained_user():
    untrained = User("u-1", "Bob")
    vs = schedule.check(bk(7, 9, 11), inst(), [], LON, NOW, untrained)
    assert rules(vs) == {"permission"} and not schedule.blocking(vs)
    assert schedule.check(bk(7, 9, 11), inst(), [], LON, NOW, User("u-1", "M", manager=True)) == []


def test_granularity_warns_and_snap_fixes():
    b = bk(7, 9.25, 11)
    assert "granularity" in rules(schedule.check(b, inst(), [], LON, NOW, TRAINED))
    snapped = schedule.snap_booking(b, inst(), LON)
    assert snapped.start.hour == 9 and snapped.start.minute in (0, 30)
    assert schedule.check(snapped, inst(), [], LON, NOW, TRAINED) == []


def test_overnight_on_247_instrument():
    allweek = {d: (parse_range("00:00-24:00", "x", "k"),)
               for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    i = inst(bookable_hours=allweek, max_booking_minutes=24 * 60)
    b = Booking("xps", datetime(2026, 10, 7, 18, tzinfo=LON), datetime(2026, 10, 8, 9, tzinfo=LON),
                "u-1")
    assert schedule.check(b, i, [], LON, NOW, TRAINED) == []


def test_overnight_blocked_when_day_closes():
    b = Booking("xps", datetime(2026, 10, 7, 18, tzinfo=LON), datetime(2026, 10, 8, 9, tzinfo=LON),
                "u-1")
    assert "bookable_hours" in rules(schedule.check(b, inst(max_booking_minutes=24 * 60), [], LON,
                                                    NOW, TRAINED))


# -- daylight saving: both transitions -------------------------------------

def test_duration_across_spring_forward():
    # 29 March 2026: clocks go 01:00 GMT -> 02:00 BST; 00:00-04:00 is 3 h
    b = Booking("xps", datetime(2026, 3, 29, 0, tzinfo=LON), datetime(2026, 3, 29, 4, tzinfo=LON),
                "u-1")
    assert b.minutes == 180


def test_duration_across_fall_back():
    # 25 October 2026: clocks go 02:00 BST -> 01:00 GMT; 00:00-04:00 is 5 h
    b = Booking("xps", datetime(2026, 10, 25, 0, tzinfo=LON),
                datetime(2026, 10, 25, 4, tzinfo=LON), "u-1")
    assert b.minutes == 300


def test_overlap_in_repeated_hour():
    # 01:30 happens twice on 25 Oct 2026; fold distinguishes them
    first = Booking("xps", datetime(2026, 10, 25, 1, 0, tzinfo=LON),
                    datetime(2026, 10, 25, 1, 30, tzinfo=LON), "u-1")
    second = Booking("xps", datetime(2026, 10, 25, 1, 0, fold=1, tzinfo=LON),
                     datetime(2026, 10, 25, 1, 30, fold=1, tzinfo=LON), "u-2")
    assert not first.overlaps(second)


def test_weekly_recurrence_keeps_wall_clock_across_dst():
    b = Booking("xps", datetime(2026, 10, 19, 9, tzinfo=LON), datetime(2026, 10, 19, 11, tzinfo=LON),
                "u-1")
    occ = schedule.expand_recurring(b, 7, 3, LON)
    assert [o.start.astimezone(LON).hour for o in occ] == [9, 9, 9]
    assert occ[0].start.utcoffset() != occ[1].start.utcoffset()


def test_pick_winner_prefers_earliest_created():
    a = bk(7, 9, 11, created=datetime(2026, 10, 1, 9, tzinfo=LON))
    b = bk(7, 9, 11, user="u-2", created=datetime(2026, 10, 1, 8, tzinfo=LON))
    assert schedule.pick_winner(a, b) is b


@pytest.mark.parametrize("g", [15, 30, 60])
def test_snap_lands_on_grid(g):
    dt = datetime(2026, 10, 7, 10, 7, tzinfo=LON)
    assert schedule.on_granularity(schedule.snap(dt, g, LON), g, LON)
