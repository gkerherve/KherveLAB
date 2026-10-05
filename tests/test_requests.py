from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from khervelab.core.facility import BookingRejected, FacilityService
from khervelab.core.github import Issue
from khervelab.core.models import Booking, User
from khervelab.core.requests import (RequestQueue, ensure_issue_template, parse_date,
                                     parse_duration, parse_request, parse_time, sections,
                                     suggest_alternative)


class FakeClient:
    def __init__(self, issues):
        self.issues = issues
        self.comments: list[tuple[int, str]] = []
        self.closed: list[tuple[int, str]] = []

    def open_issues(self, label):
        return [i for i in self.issues if i.number not in {n for n, _ in self.closed}]

    def comment(self, number, body):
        self.comments.append((number, body))

    def close(self, number, reason="completed"):
        self.closed.append((number, reason))


def future_weekday(days=3) -> date:
    d = date.today() + timedelta(days=days)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def form(instrument="xps", day=None, start="09:00", duration="2", samples="S-2026-0001"):
    day = day or future_weekday().isoformat()
    return (f"### Instrument\n\n{instrument}\n\n### Date\n\n{day}\n\n### Start time\n\n{start}\n\n"
            f"### Duration (hours)\n\n{duration}\n\n### Purpose\n\n_No response_\n\n"
            f"### Samples\n\n{samples}\n")


def issue(n=1, body=None, author="phd-student"):
    return Issue(n, "Booking request", body if body is not None else form(), author,
                 f"https://github.com/lab/f/issues/{n}", "2026-10-05T09:00:00Z")


@pytest.fixture
def svc(facility):
    s = FacilityService(facility)
    s.save_user(User("u-0002", "Student", github="phd-student", permissions=("xps",)))
    return s


def test_sections_parses_issue_form():
    sec = sections(form())
    assert sec["instrument"] == "xps" and sec["purpose"] == "" and sec["samples"] == "S-2026-0001"


@pytest.mark.parametrize("text,expected", [
    ("2026-10-07", date(2026, 10, 7)), ("7/10/2026", date(2026, 10, 7)),
    ("7 Oct 2026", date(2026, 10, 7)), ("Wed 7th October 2026", date(2026, 10, 7)),
    ("nonsense", None)])
def test_parse_date(text, expected):
    assert parse_date(text, date(2026, 10, 5)) == expected


@pytest.mark.parametrize("text,expected", [("09:00", (9, 0)), ("9", (9, 0)), ("2pm", (14, 0)),
                                           ("14.30", (14, 30)), ("25:00", None)])
def test_parse_time(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("text,expected", [("2", 120), ("1.5 hours", 90), ("90 min", 90),
                                           ("1h30", 90), ("2h", 120), ("lots", None)])
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


def test_parse_request_maps_user_and_checks_rules(svc):
    r = parse_request(svc, issue())
    assert r.parsed and r.instrument == "xps" and r.user == "u-0002"
    assert r.samples == ("S-2026-0001",) and r.violations == []
    assert r.end - r.start == timedelta(hours=2)


def test_hand_edited_issue_is_tolerated(svc):
    body = f"Instrument: High-throughput XPS\nDate: {future_weekday():%d/%m/%Y}\nStart: 2pm\nDuration: 90 min"
    r = parse_request(svc, issue(body=body))
    assert r.parsed and r.instrument == "xps" and r.start.hour == 14


def test_unknown_instrument_is_an_error(svc):
    r = parse_request(svc, issue(body=form(instrument="time machine")))
    assert not r.parsed or r.errors
    assert any("unknown instrument" in e for e in r.errors)


def test_violations_flagged_before_decision(svc):
    d = future_weekday()
    tz = svc.tz
    svc.add_booking(Booking("xps", datetime(d.year, d.month, d.day, 9, tzinfo=tz),
                            datetime(d.year, d.month, d.day, 12, tzinfo=tz), "u-0001"))
    r = parse_request(svc, issue())
    assert any(v.rule == "overlap" for v in r.violations)
    alt = suggest_alternative(svc, r)
    assert alt and alt[0].hour == 12


def test_approve_books_comments_and_closes(svc):
    client = FakeClient([issue(7)])
    q = RequestQueue(svc, client)
    (r,) = q.poll()
    b = q.approve(r)
    assert b.notes == "request #7" and b.user == "u-0002"
    assert client.closed == [(7, "completed")]
    assert "Confirmed" in client.comments[0][1]
    assert svc.repo.git.head.commit.message.startswith("book xps")
    assert q.items == []


def test_unknown_author_must_be_linked(svc):
    q = RequestQueue(svc, FakeClient([issue(3, author="newcomer")]))
    (r,) = q.poll()
    assert r.user == ""
    with pytest.raises(ValueError):
        q.approve(r)
    u = q.link_user(r, "New starter")
    assert u.github == "newcomer" and svc.users[u.id].github == "newcomer"
    with pytest.raises(BookingRejected, match="not trained"):
        q.approve(r)
    assert q.approve(r, override=True).override == ("permission",)


def test_decline_and_propose(svc):
    client = FakeClient([issue(4), issue(5)])
    q = RequestQueue(svc, client)
    a, b = q.poll()
    q.decline(a, "instrument down for service")
    assert client.closed == [(4, "not_planned")]
    q.propose(b, b.start + timedelta(days=1), b.end + timedelta(days=1))
    assert (5, "not_planned") not in client.closed and "instead" in client.comments[-1][1]


def test_approve_respects_hard_clash(svc):
    d = future_weekday()
    tz = svc.tz
    svc.add_booking(Booking("xps", datetime(d.year, d.month, d.day, 9, tzinfo=tz),
                            datetime(d.year, d.month, d.day, 12, tzinfo=tz), "u-0001"))
    client = FakeClient([issue(9)])
    q = RequestQueue(svc, client)
    (r,) = q.poll()
    with pytest.raises(BookingRejected):
        q.approve(r)
    assert client.closed == []


def test_new_facility_has_issue_template(svc):
    assert (svc.repo.path / ".github/ISSUE_TEMPLATE/booking-request.yml").exists()
    assert ensure_issue_template(svc) is False
