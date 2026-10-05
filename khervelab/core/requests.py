"""Booking requests: parse GitHub issue forms, approve, decline, propose.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from . import schedule
from .facility import FacilityService
from .github import GitHubClient, Issue
from .models import Booking, User

LABEL = "booking-request"
TEMPLATE_PATH = ".github/ISSUE_TEMPLATE/booking-request.yml"

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


@dataclass
class BookingRequest:
    issue: Issue
    instrument: str = ""
    start: datetime | None = None
    end: datetime | None = None
    purpose: str = ""
    samples: tuple[str, ...] = ()
    user: str = ""                      # mapped anonymous id, "" when unknown
    errors: list[str] = field(default_factory=list)
    violations: list[schedule.Violation] = field(default_factory=list)

    @property
    def number(self) -> int:
        return self.issue.number

    @property
    def parsed(self) -> bool:
        return not self.errors and self.start is not None

    def booking(self) -> Booking:
        return Booking(self.instrument, self.start, self.end, self.user or "unknown",
                       samples=self.samples, notes=f"request #{self.number}")


def sections(body: str) -> dict[str, str]:
    """`### Label` headings → their text, as GitHub renders an issue form.
    Hand-edited issues work too: `Label: value` lines are picked up."""
    out: dict[str, str] = {}
    parts = re.split(r"(?m)^#{2,4}\s*(.+?)\s*$", body or "")
    for i in range(1, len(parts) - 1, 2):
        out[parts[i].strip().lower()] = parts[i + 1].strip()
    for line in (body or "").splitlines():
        m = re.match(r"^\s*\**([A-Za-z ()]+?)\**\s*[:=]\s*(.+)$", line)
        if m and m.group(1).strip().lower() not in out:
            out[m.group(1).strip().lower()] = m.group(2).strip()
    return {k: ("" if v.strip() == "_No response_" else v.strip()) for k, v in out.items()}


def _get(sec: dict[str, str], *names: str) -> str:
    for n in names:
        for k, v in sec.items():
            if k == n or k.startswith(n):
                return v
    return ""


def parse_date(text: str, today: date) -> date | None:
    t = text.strip().lower().replace(",", " ")
    if not t:
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})$", t)  # UK order: day/month/year
    if m:
        y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        return date(y, int(m[2]), int(m[1]))
    m = re.match(r"^(?:[a-z]+\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?(?:\s+(\d{4}))?$", t)
    if m and m[2] in _MONTHS:
        y = int(m[3]) if m[3] else today.year
        d = date(y, _MONTHS[m[2]], int(m[1]))
        return d if m[3] or d >= today else date(y + 1, d.month, d.day)
    if t == "today":
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    return None


def parse_time(text: str) -> tuple[int, int] | None:
    t = text.strip().lower().replace(" ", "")
    m = re.match(r"^(\d{1,2})(?:[:.h](\d{2}))?(am|pm)?$", t)
    if not m:
        return None
    h, mi = int(m[1]), int(m[2] or 0)
    if m[3] == "pm" and h < 12:
        h += 12
    if m[3] == "am" and h == 12:
        h = 0
    return (h, mi) if 0 <= h < 24 and 0 <= mi < 60 else None


def parse_duration(text: str) -> int | None:
    """Minutes from '2', '2h', '1.5 hours', '90 min', '1h30'."""
    t = text.strip().lower()
    m = re.match(r"^(\d+)\s*h(?:ours?|rs?)?\s*(\d+)\s*(?:m|min|mins|minutes)?$", t)
    if m:
        return int(m[1]) * 60 + int(m[2])
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minutes)?$", t)
    if not m:
        return None
    v = float(m[1])
    return int(round(v if m[2] and m[2].startswith("m") else v * 60))


def match_instrument(svc: FacilityService, text: str) -> str:
    t = text.strip().lower()
    if not t:
        return ""
    if t in svc.instruments:
        return t
    head = t.split()[0].strip("—-:")
    if head in svc.instruments:
        return head
    by_name = [i.id for i in svc.instruments.values() if i.name.lower() == t]
    if by_name:
        return by_name[0]
    partial = [i.id for i in svc.instruments.values() if t in i.name.lower() or t in i.id]
    return partial[0] if len(partial) == 1 else ""


def parse_request(svc: FacilityService, issue: Issue, today: date | None = None) -> BookingRequest:
    today = today or datetime.now(svc.tz).date()
    sec = sections(issue.body)
    req = BookingRequest(issue)
    raw_inst = _get(sec, "instrument")
    req.instrument = match_instrument(svc, raw_inst)
    if not req.instrument:
        req.errors.append(f"unknown instrument {raw_inst!r}" if raw_inst else "no instrument given")
    d = parse_date(_get(sec, "date", "day"), today)
    if d is None:
        req.errors.append("date not understood")
    tm = parse_time(_get(sec, "start", "time", "from"))
    if tm is None:
        req.errors.append("start time not understood")
    mins = parse_duration(_get(sec, "duration", "length", "hours"))
    if mins is None or mins <= 0:
        req.errors.append("duration not understood")
    req.purpose = _get(sec, "purpose", "description")
    req.samples = tuple(s.strip() for s in re.split(r"[,\n;]+", _get(sec, "samples", "sample"))
                        if s.strip())
    if d and tm and mins:
        start = datetime(d.year, d.month, d.day, tm[0], tm[1], tzinfo=svc.tz)
        req.start, req.end = start, start + timedelta(minutes=mins)
    req.user = user_for_github(svc, issue.author)
    if req.parsed and req.instrument:
        req.violations = [v for v in svc.check(req.booking()) if v.rule != "granularity"]
    return req


def user_for_github(svc: FacilityService, login: str) -> str:
    login = login.lower()
    for u in svc.users.values():
        if u.github and u.github.lower() == login:
            return u.id
    return ""


def ensure_issue_template(svc: FacilityService) -> bool:
    """Add the issue form to a facility created before it existed."""
    path = svc.repo.path / TEMPLATE_PATH
    if path.exists():
        return False
    from .repo import TEMPLATE
    svc.repo.write_file(TEMPLATE_PATH, (TEMPLATE / TEMPLATE_PATH).read_text(encoding="utf-8"))
    svc.repo.commit("add booking-request issue form", [TEMPLATE_PATH])
    return True


class RequestQueue:
    def __init__(self, svc: FacilityService, client: GitHubClient):
        self.svc = svc
        self.client = client
        self.items: list[BookingRequest] = []

    def poll(self) -> list[BookingRequest]:
        issues = self.client.open_issues(LABEL)
        self.items = [parse_request(self.svc, i) for i in issues]
        return self.items

    def recheck(self) -> None:
        self.items = [parse_request(self.svc, r.issue) for r in self.items]

    def link_user(self, req: BookingRequest, display: str) -> User:
        """Create an anonymous user for an unknown GitHub author."""
        u = User(self.svc.next_user_id(), display, github=req.issue.author)
        self.svc.save_user(u)
        req.user = u.id
        return u

    def approve(self, req: BookingRequest, override: bool = False,
                start: datetime | None = None, end: datetime | None = None) -> Booking:
        if not req.user:
            raise ValueError(f"GitHub user {req.issue.author} is not linked to a user id")
        b = req.booking()
        if start and end:
            b = replace(b, start=start, end=end)
        b = self.svc.add_booking(b, override=override)
        s, e = b.start.astimezone(self.svc.tz), b.end.astimezone(self.svc.tz)
        inst = self.svc.instruments[b.instrument]
        self.client.comment(req.number, f"Confirmed: **{inst.name}**, {s:%A %d %B %Y}, "
                                        f"{s:%H:%M}–{e:%H:%M}. It is on the published calendar.")
        self.client.close(req.number, "completed")
        self._drop(req)
        return b

    def decline(self, req: BookingRequest, reason: str) -> None:
        self.client.comment(req.number, f"Declined: {reason}" if reason else "Declined.")
        self.client.close(req.number, "not_planned")
        self._drop(req)

    def propose(self, req: BookingRequest, start: datetime, end: datetime, note: str = "") -> None:
        s, e = start.astimezone(self.svc.tz), end.astimezone(self.svc.tz)
        text = (f"That slot is not available. Would **{s:%A %d %B %Y}, {s:%H:%M}–{e:%H:%M}** "
                "work instead? Reply here and the manager will confirm.")
        if note:
            text += f"\n\n{note}"
        self.client.comment(req.number, text)

    def _drop(self, req: BookingRequest) -> None:
        self.items = [r for r in self.items if r.number != req.number]


def suggest_alternative(svc: FacilityService, req: BookingRequest,
                        days: int = 14) -> tuple[datetime, datetime] | None:
    """The first free slot of the same length on the same instrument."""
    if not req.parsed or not req.instrument:
        return None
    inst = svc.instruments[req.instrument]
    length = req.end - req.start
    step = timedelta(minutes=inst.slot_granularity_minutes)
    t = req.start
    limit = req.start + timedelta(days=days)
    while t < limit:
        t += step
        cand = replace(req.booking(), start=t, end=t + length)
        if not schedule.blocking(svc.check(cand)):
            return cand.start, cand.end
    return None


__all__ = ["BookingRequest", "RequestQueue", "parse_request", "sections", "ensure_issue_template",
           "suggest_alternative"]
