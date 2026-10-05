from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from khervelab.core.facility import BookingRejected, FacilityService
from khervelab.core.models import Booking
from khervelab.core.repo import ConflictError, FacilityRepo, OfflineError, PersonalDataError

LON = ZoneInfo("Europe/London")


def day(offset: int, hour: int, minute: int = 0) -> datetime:
    base = datetime.now(LON).replace(hour=0, minute=0, second=0, microsecond=0)
    d = base + timedelta(days=offset)
    while d.weekday() >= 5:  # weekday instruments
        d += timedelta(days=1)
    return d.replace(hour=hour, minute=minute)


def booking(inst="sem-sigma-300", h1=9, h2=11, user="u-0001", off=3) -> Booking:
    return Booking(inst, day(off, h1), day(off, h2), user)


def log(repo: FacilityRepo) -> list[str]:
    return [c.message.strip() for c in repo.git.iter_commits()]


def test_create_scaffolds_and_commits(facility):
    assert (facility.path / "facility.yaml").exists()
    assert log(facility) == ["create facility"]
    assert "Test facility" in (facility.path / "facility.yaml").read_text()


def test_create_refuses_non_empty(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "f").write_text("hi")
    with pytest.raises(Exception):
        FacilityRepo.create(tmp_path / "x")


def test_book_move_cancel_are_one_commit_each(facility):
    svc = FacilityService(facility)
    b = svc.add_booking(booking())
    assert log(facility)[0].startswith("book sem-sigma-300 ")
    assert log(facility)[0].endswith(" u-0001")
    moved = svc.update_booking(b, b.with_times(day(3, 13), day(3, 15)))
    assert log(facility)[0].startswith("move sem-sigma-300 ")
    assert len(list((facility.path / "bookings").rglob("*.yaml"))) == 1
    svc.delete_booking(moved)
    assert log(facility)[0].startswith("cancel ")
    assert list((facility.path / "bookings").rglob("*.yaml")) == []
    assert len(log(facility)) == 4
    assert facility.status().dirty == []


def test_reload_round_trips(facility):
    svc = FacilityService(facility)
    b = svc.add_booking(booking())
    again = FacilityService(facility)
    (got,) = again.bookings()
    assert got.start == b.start and got.end == b.end and got.created == b.created


def test_clash_is_rejected(facility):
    svc = FacilityService(facility)
    svc.add_booking(booking())
    with pytest.raises(BookingRejected, match="clashes"):
        svc.add_booking(booking(h1=10, h2=12, user="u-0002"))


def test_warning_needs_override_and_is_recorded(facility):
    svc = FacilityService(facility)
    stranger = booking(user="u-9999")  # unknown user: no permission check possible
    svc.add_booking(stranger)
    from khervelab.core.models import User
    svc.save_user(User("u-0002", "Student"))
    with pytest.raises(BookingRejected, match="not trained"):
        svc.add_booking(booking(h1=13, h2=14, user="u-0002"))
    b = svc.add_booking(booking(h1=13, h2=14, user="u-0002"), override=True)
    assert b.override == ("permission",)
    assert "permission" in (facility.path / svc.path_of(b)).read_text()


def test_recurring_is_one_commit(facility):
    svc = FacilityService(facility)
    items = svc.add_recurring(booking(inst="xps", off=1), every_days=7, count=3)
    assert len(items) == 3
    assert "(+2 repeats" in log(facility)[0]


def test_email_never_committed(facility):
    svc = FacilityService(facility)
    b = booking()
    from dataclasses import replace
    with pytest.raises(PersonalDataError):
        svc.add_booking(replace(b, notes="contact jane.doe@imperial.ac.uk"))
    assert log(facility) == ["create facility"]


def test_offline_push_queues(facility):
    facility.git.create_remote("origin", "https://nonexistent.invalid/x.git")
    svc = FacilityService(facility)
    svc.add_booking(booking())
    assert facility.push() is False
    assert facility.status().ahead == 2
    with pytest.raises(OfflineError):
        facility.pull()


def test_two_copies_sync(two_copies):
    a, b = two_copies
    sa = FacilityService(a)
    sa.add_booking(booking())
    assert a.push()
    assert b.pull() == 1
    sb = FacilityService(b)
    assert len(sb.bookings()) == 1


def test_same_slot_raises_conflict_with_both_versions(two_copies):
    a, b = two_copies
    sa, sb = FacilityService(a), FacilityService(b)
    sa.add_booking(booking(user="u-0001", h1=9, h2=11))
    sb.add_booking(booking(user="u-0002", h1=9, h2=12))
    assert a.push()
    with pytest.raises(ConflictError) as exc:
        b.pull()
    (c,) = exc.value.conflicts
    assert "u-0002" in c.ours and "u-0001" in c.theirs
    assert b.status().dirty == []  # merge was aborted cleanly


@pytest.mark.parametrize("keep", ["ours", "theirs"])
def test_resolve_conflict(two_copies, keep):
    a, b = two_copies
    sa, sb = FacilityService(a), FacilityService(b)
    sa.add_booking(booking(user="u-0001"))
    sb.add_booking(booking(user="u-0002"))
    sb.add_booking(booking(user="u-0002", h1=14, h2=16))  # unrelated local work survives
    assert a.push()
    with pytest.raises(ConflictError) as exc:
        b.pull()
    path = exc.value.conflicts[0].path
    sb.resolve_conflicts({path: keep})
    users = sorted(x.user for x in sb.bookings())
    winner = "u-0002" if keep == "ours" else "u-0001"
    assert users == sorted([winner, "u-0002"])
    assert b.push()
    a.pull()
    assert len(FacilityService(a).bookings()) == 2


def test_different_slots_merge_and_clash_is_found(two_copies):
    a, b = two_copies
    sa, sb = FacilityService(a), FacilityService(b)
    sa.add_booking(booking(h1=9, h2=11, user="u-0001"))
    sb.add_booking(booking(h1=10, h2=12, user="u-0002"))
    assert a.push()
    b.pull()  # different files: Git merges cleanly
    sb.reload()
    (pair,) = sb.find_clashes()
    assert {pair[0].user, pair[1].user} == {"u-0001", "u-0002"}
