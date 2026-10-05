"""Dataclasses for everything a facility repository holds.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time, timezone
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
BOOKING_KINDS = ("measurement", "training", "maintenance", "blocked")


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class TimeRange:
    start: time
    end: time | None  # None means midnight at the end of the day ("24:00")

    def contains(self, start: time, end: time | None) -> bool:
        if start < self.start:
            return False
        if self.end is None:
            return True
        return end is not None and end <= self.end


@dataclass(frozen=True)
class Consumable:
    name: str
    limit_hours: float
    warn_at: float


@dataclass(frozen=True)
class MaintenanceTask:
    task: str
    interval_days: int


@dataclass(frozen=True)
class Instrument:
    id: str
    name: str
    category: str = ""
    facility: str = ""
    make_model: str = ""
    techniques: tuple[str, ...] = ()
    description: str = ""
    colour: str = "#1f6feb"
    location: str = ""
    source: str = ""
    bookable_hours: dict[str, tuple[TimeRange, ...]] = field(default_factory=dict)
    slot_granularity_minutes: int = 30
    min_booking_minutes: int = 30
    max_booking_minutes: int = 8 * 60
    max_advance_days: int = 28
    max_concurrent_per_user: int = 3
    requires_permission: bool = False
    consumables: tuple[Consumable, ...] = ()
    maintenance: tuple[MaintenanceTask, ...] = ()


@dataclass(frozen=True)
class User:
    id: str
    display: str
    github: str = ""
    permissions: tuple[str, ...] = ()
    manager: bool = False


@dataclass(frozen=True)
class Facility:
    name: str
    timezone: str = "Europe/London"
    organisation: str = ""
    contact: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class Booking:
    instrument: str
    start: datetime  # always timezone-aware
    end: datetime
    user: str
    kind: str = "measurement"
    samples: tuple[str, ...] = ()
    notes: str = ""
    created: datetime | None = None
    override: tuple[str, ...] = ()

    def __post_init__(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("booking times must be timezone-aware")

    @property
    def minutes(self) -> int:
        # via UTC: Python ignores offsets when both operands share a tzinfo,
        # which miscounts the hour lost or gained at a DST change
        return int((_utc(self.end) - _utc(self.start)).total_seconds() // 60)

    def overlaps(self, other: "Booking") -> bool:
        return (self.instrument == other.instrument
                and _utc(self.start) < _utc(other.end) and _utc(other.start) < _utc(self.end))

    def path(self, tz: ZoneInfo) -> PurePosixPath:
        """Repository path. Same instrument + same local start = same path,
        so two people taking one slot collide in Git rather than silently."""
        s = self.start.astimezone(tz)
        return PurePosixPath(
            "bookings", self.instrument, f"{s:%Y}", f"{s:%m}",
            f"{self.instrument}-{s:%Y-%m-%d-%H%M}.yaml")

    def with_times(self, start: datetime, end: datetime) -> "Booking":
        return replace(self, start=start, end=end)

    def label(self, tz: ZoneInfo) -> str:
        s, e = self.start.astimezone(tz), self.end.astimezone(tz)
        end = f"{e:%H:%M}" if e.date() == s.date() else f"{e:%Y-%m-%d %H:%M}"
        return f"{self.instrument} {s:%Y-%m-%d %H:%M}-{end} {self.user}"
