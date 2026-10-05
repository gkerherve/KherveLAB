"""Load and validate a facility repository's YAML files into dataclasses.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (BOOKING_KINDS, DAYS, Booking, Consumable, Facility, Instrument,
                     MaintenanceTask, TimeRange, User)
from .yamlio import ConfigError, dump_yaml, load_yaml

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_RANGE_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
_COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _req(d: dict, key: str, path: Path, kind: type | tuple = str) -> Any:
    if key not in d or d[key] is None:
        raise ConfigError(path, "required key is missing", key)
    val = d[key]
    if not isinstance(val, kind):
        raise ConfigError(path, f"expected {_kind_name(kind)}, got {type(val).__name__}", key)
    return val


def _opt(d: dict, key: str, path: Path, kind: type | tuple, default: Any) -> Any:
    val = d.get(key, default)
    if val is None:
        return default
    if kind is int and isinstance(val, bool):
        raise ConfigError(path, "expected integer, got bool", key)
    if not isinstance(val, kind):
        raise ConfigError(path, f"expected {_kind_name(kind)}, got {type(val).__name__}", key)
    return val


def _kind_name(kind) -> str:
    if isinstance(kind, tuple):
        return " or ".join(k.__name__ for k in kind)
    return {"str": "text", "int": "integer", "list": "list", "dict": "mapping",
            "bool": "true/false"}.get(kind.__name__, kind.__name__)


def _mapping(data: Any, path: Path) -> dict:
    if not isinstance(data, dict):
        raise ConfigError(path, "top level must be a mapping")
    return data


def parse_range(text: str, path: Path, key: str) -> TimeRange:
    m = _RANGE_RE.match(str(text).strip())
    if not m:
        raise ConfigError(path, f"time range {text!r} must look like 09:00-17:30", key)
    h1, m1, h2, m2 = map(int, m.groups())
    if not (0 <= h1 < 24 and 0 <= m1 < 60 and 0 <= m2 < 60 and (h2 < 24 or (h2, m2) == (24, 0))):
        raise ConfigError(path, f"time range {text!r} is out of bounds", key)
    start = time(h1, m1)
    end = None if h2 == 24 else time(h2, m2)
    if end is not None and end <= start:
        raise ConfigError(path, f"time range {text!r} ends before it starts", key)
    return TimeRange(start, end)


def format_range(r: TimeRange) -> str:
    end = "24:00" if r.end is None else f"{r.end:%H:%M}"
    return f"{r.start:%H:%M}-{end}"


def load_facility(path: Path) -> Facility:
    d = _mapping(load_yaml(path), path)
    tzname = _opt(d, "timezone", path, str, "Europe/London")
    try:
        ZoneInfo(tzname)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(path, f"unknown timezone {tzname!r}", "timezone") from None
    return Facility(
        name=_req(d, "name", path),
        timezone=tzname,
        organisation=_opt(d, "organisation", path, str, ""),
        contact=_opt(d, "contact", path, str, ""),
        github=_github_slug(_opt(d, "github", path, str, ""), path),
        site_url=_opt(d, "site_url", path, str, ""),
    )


def _github_slug(val: str, path: Path) -> str:
    val = val.strip()
    if val and not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", val):
        raise ConfigError(path, f"{val!r} must look like owner/repo", "github")
    return val


def load_instrument(path: Path) -> Instrument:
    d = _mapping(load_yaml(path), path)
    iid = _req(d, "id", path)
    if not _ID_RE.match(iid):
        raise ConfigError(path, f"id {iid!r} must be lower-case letters, digits and dashes", "id")
    if iid != path.stem:
        raise ConfigError(path, f"id {iid!r} must match the file name {path.stem!r}", "id")
    colour = _opt(d, "colour", path, str, "#1f6feb")
    if not _COLOUR_RE.match(colour):
        raise ConfigError(path, f"colour {colour!r} must be #rrggbb", "colour")

    hours_raw = _opt(d, "bookable_hours", path, dict, {})
    hours: dict[str, tuple[TimeRange, ...]] = {}
    for day, ranges in hours_raw.items():
        key = f"bookable_hours.{day}"
        if day not in DAYS:
            raise ConfigError(path, f"unknown day {day!r}; use {', '.join(DAYS)}", key)
        if isinstance(ranges, str):
            ranges = [ranges]
        if not isinstance(ranges, list):
            raise ConfigError(path, "expected a list of time ranges", key)
        hours[day] = tuple(parse_range(r, path, key) for r in ranges)

    ints = {}
    for key, default in (("slot_granularity_minutes", 30), ("min_booking_minutes", 30),
                         ("max_booking_minutes", 480), ("max_advance_days", 28),
                         ("max_concurrent_per_user", 3)):
        ints[key] = _opt(d, key, path, int, default)
        if ints[key] <= 0:
            raise ConfigError(path, "must be a positive integer", key)
    if ints["min_booking_minutes"] > ints["max_booking_minutes"]:
        raise ConfigError(path, "is larger than max_booking_minutes", "min_booking_minutes")
    if 1440 % ints["slot_granularity_minutes"]:
        raise ConfigError(path, "must divide a day evenly (e.g. 15, 30, 60)",
                          "slot_granularity_minutes")

    consumables = []
    for i, c in enumerate(_opt(d, "consumables", path, list, [])):
        key = f"consumables[{i}]"
        if not isinstance(c, dict):
            raise ConfigError(path, "expected a mapping", key)
        consumables.append(Consumable(
            name=_req(c, "name", path),
            limit_hours=float(_req(c, "limit_hours", path, (int, float))),
            warn_at=float(_opt(c, "warn_at", path, (int, float), c.get("limit_hours", 0)))))
    maintenance = []
    for i, m in enumerate(_opt(d, "maintenance", path, list, [])):
        if not isinstance(m, dict):
            raise ConfigError(path, "expected a mapping", f"maintenance[{i}]")
        maintenance.append(MaintenanceTask(
            task=_req(m, "task", path), interval_days=_req(m, "interval_days", path, int)))

    return Instrument(
        id=iid,
        name=_req(d, "name", path),
        category=_opt(d, "category", path, str, ""),
        facility=_opt(d, "facility", path, str, ""),
        make_model=_opt(d, "make_model", path, str, ""),
        techniques=tuple(str(t) for t in _opt(d, "techniques", path, list, [])),
        description=_opt(d, "description", path, str, ""),
        colour=colour,
        location=_opt(d, "location", path, str, ""),
        source=_opt(d, "source", path, str, ""),
        bookable_hours=hours,
        requires_permission=_opt(d, "requires_permission", path, bool, False),
        consumables=tuple(consumables),
        maintenance=tuple(maintenance),
        **ints,
    )


def load_user(path: Path) -> User:
    d = _mapping(load_yaml(path), path)
    uid = _req(d, "id", path)
    if uid != path.stem:
        raise ConfigError(path, f"id {uid!r} must match the file name {path.stem!r}", "id")
    perms = _opt(d, "permissions", path, list, [])
    return User(
        id=uid,
        display=_opt(d, "display", path, str, uid),
        github=_opt(d, "github", path, str, ""),
        permissions=tuple(str(p) for p in perms),
        manager=_opt(d, "manager", path, bool, False),
    )


def user_to_dict(u: User) -> dict:
    d = {"id": u.id, "display": u.display}
    if u.github:
        d["github"] = u.github
    d["permissions"] = list(u.permissions)
    if u.manager:
        d["manager"] = True
    return d


def _parse_dt(val: Any, path: Path, key: str) -> datetime:
    if isinstance(val, datetime):
        # ruamel hands back its own TimeStamp subclass; normalise it
        dt = datetime.fromisoformat(val.isoformat())
    else:
        try:
            dt = datetime.fromisoformat(str(val))
        except ValueError:
            raise ConfigError(path, f"{val!r} is not an ISO 8601 date-time", key) from None
    if dt.tzinfo is None:
        raise ConfigError(path, "date-time must carry a UTC offset, e.g. +01:00", key)
    return dt


def load_booking(path: Path) -> Booking:
    d = _mapping(load_yaml(path), path)
    return booking_from_dict(d, path)


def booking_from_dict(d: dict, path: Path) -> Booking:
    start = _parse_dt(_req(d, "start", path, (str, datetime)), path, "start")
    end = _parse_dt(_req(d, "end", path, (str, datetime)), path, "end")
    if end <= start:
        raise ConfigError(path, "end must be after start", "end")
    kind = _opt(d, "kind", path, str, "measurement")
    if kind not in BOOKING_KINDS:
        raise ConfigError(path, f"kind must be one of {', '.join(BOOKING_KINDS)}", "kind")
    created = d.get("created")
    return Booking(
        instrument=_req(d, "instrument", path),
        start=start,
        end=end,
        user=_req(d, "user", path),
        kind=kind,
        samples=tuple(str(s) for s in _opt(d, "samples", path, list, [])),
        notes=_opt(d, "notes", path, str, ""),
        created=_parse_dt(created, path, "created") if created else None,
        override=tuple(str(o) for o in _opt(d, "override", path, list, [])),
    )


def booking_to_dict(b: Booking) -> dict:
    d: dict[str, Any] = {
        "instrument": b.instrument,
        "start": b.start.isoformat(timespec="minutes"),
        "end": b.end.isoformat(timespec="minutes"),
        "user": b.user,
        "kind": b.kind,
    }
    if b.samples:
        d["samples"] = list(b.samples)
    if b.notes:
        d["notes"] = b.notes
    if b.created:
        d["created"] = b.created.isoformat(timespec="seconds")
    if b.override:
        d["override"] = list(b.override)
    return d


def booking_yaml(b: Booking) -> str:
    return dump_yaml(booking_to_dict(b))


@dataclass
class FacilityConfig:
    """Everything loaded from one working copy."""

    root: Path
    facility: Facility
    instruments: dict[str, Instrument] = field(default_factory=dict)
    users: dict[str, User] = field(default_factory=dict)
    bookings: dict[str, Booking] = field(default_factory=dict)  # repo path -> booking


def load_config(root: Path) -> FacilityConfig:
    """Load a working copy. Any invalid file raises ConfigError — a malformed
    booking is never silently skipped."""
    root = Path(root)
    cfg = FacilityConfig(root=root, facility=load_facility(root / "facility.yaml"))
    for p in sorted((root / "instruments").glob("*.yaml")):
        inst = load_instrument(p)
        cfg.instruments[inst.id] = inst
    for p in sorted((root / "users").glob("*.yaml")):
        u = load_user(p)
        cfg.users[u.id] = u
    tz = cfg.facility.tz
    for p in sorted((root / "bookings").rglob("*.yaml")):
        b = load_booking(p)
        if b.instrument not in cfg.instruments:
            raise ConfigError(p, f"unknown instrument {b.instrument!r}", "instrument")
        rel = p.relative_to(root).as_posix()
        expected = b.path(tz).as_posix()
        if rel != expected:
            raise ConfigError(p, f"booking file should be at {expected}", "start")
        cfg.bookings[rel] = b
    return cfg
