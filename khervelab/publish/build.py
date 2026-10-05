"""Generate the published calendar: static HTML, JSON and iCalendar feeds.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

The output is committed to ``docs/`` (the only folder GitHub Pages serves
from a branch without an Actions workflow) and is exactly what the manager
last pushed. Only what the repository already makes public goes out: the
instrument colour, the time, the anonymous display string and the kind.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..core.config import FacilityConfig, format_range
from ..core.models import DAYS, Booking, Instrument

HERE = Path(__file__).resolve().parent
SITE_DIR = "docs"


@dataclass(frozen=True)
class InstrumentStatus:
    available: bool = True
    text: str = "available"


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(HERE / "templates"),
                       autoescape=select_autoescape(["html"]), trim_blocks=True,
                       lstrip_blocks=True, keep_trailing_newline=True)


def public_event(cfg: FacilityConfig, b: Booking) -> dict:
    inst = cfg.instruments.get(b.instrument)
    user = cfg.users.get(b.user)
    who = user.display if user else b.user
    title = who if b.kind == "measurement" else f"{b.kind.title()} · {who}" \
        if b.kind == "training" else b.kind.title()
    return {
        "id": _uid(b, cfg),
        "instrument": b.instrument,
        "title": title,
        "start": b.start.isoformat(timespec="minutes"),
        "end": b.end.isoformat(timespec="minutes"),
        "kind": b.kind,
        "color": inst.colour if inst else "#888888",
    }


def _uid(b: Booking, cfg: FacilityConfig) -> str:
    key = b.path(cfg.facility.tz).as_posix()
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def request_url(cfg: FacilityConfig, instrument: str = "", day: str = "", start: str = "",
                duration: str = "") -> str:
    """Pre-filled GitHub issue form; empty when the facility has no GitHub repo."""
    if not cfg.facility.github:
        return ""
    params = {"template": "booking-request.yml", "labels": "booking-request",
              "title": f"Booking request: {instrument or 'instrument'} {day}".strip()}
    for k, v in (("instrument", instrument), ("date", day), ("start", start),
                 ("duration", duration)):
        if v:
            params[k] = v
    return f"https://github.com/{cfg.facility.github}/issues/new?{urlencode(params)}"


# -- iCalendar -----------------------------------------------------------------

def _ics_escape(s: str) -> str:
    return (s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\n", "\\n"))


def _fold(line: str) -> str:
    out, raw = [], line.encode("utf-8")
    while len(raw) > 75:
        cut = 75 if not out else 74
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:  # never split a UTF-8 sequence
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n ".join(out)


def _ics_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ics(cfg: FacilityConfig, bookings: list[Booking], name: str, stamp: datetime) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Kherve//KherveLAB//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_escape(name)}",
             f"X-WR-TIMEZONE:{cfg.facility.timezone}"]
    for b in bookings:
        ev = public_event(cfg, b)
        inst = cfg.instruments.get(b.instrument)
        lines += ["BEGIN:VEVENT", f"UID:{ev['id']}@khervelab", f"DTSTAMP:{_ics_dt(stamp)}",
                  f"DTSTART:{_ics_dt(b.start)}", f"DTEND:{_ics_dt(b.end)}",
                  f"SUMMARY:{_ics_escape((inst.name if inst else b.instrument) + ' — ' + ev['title'])}",
                  f"CATEGORIES:{_ics_escape(b.kind)}"]
        if inst and inst.location:
            lines.append(f"LOCATION:{_ics_escape(inst.location)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


# -- site ----------------------------------------------------------------------

def _hours_text(inst: Instrument) -> list[str]:
    out = []
    for d in DAYS:
        rs = inst.bookable_hours.get(d)
        if rs:
            out.append(f"{d.title()} {', '.join(format_range(r) for r in rs)}")
    return out


def build_site(cfg: FacilityConfig, out: Path, as_of: datetime,
               statuses: dict[str, InstrumentStatus] | None = None,
               horizon_days: int = 120) -> list[Path]:
    """Write the whole site into `out` and return the files written.

    Bookings from 30 days back to `horizon_days` ahead are published; the
    history stays in Git. Output depends only on the repository, so an
    unchanged repository republishes byte-identical files (no empty commits)."""
    statuses = statuses or {}
    tz = cfg.facility.tz
    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    lo = as_of - timedelta(days=30)
    hi = as_of + timedelta(days=horizon_days)
    bookings = sorted((b for b in cfg.bookings.values() if b.end > lo and b.start < hi),
                      key=lambda b: (b.start, b.instrument))
    insts = sorted(cfg.instruments.values(), key=lambda i: (i.facility, i.name))
    groups: dict[str, list[Instrument]] = {}
    for i in insts:
        groups.setdefault(i.facility or "Other", []).append(i)

    events = [public_event(cfg, b) for b in bookings]
    inst_json = {
        i.id: {"name": i.name, "colour": i.colour, "facility": i.facility,
               "category": i.category, "page": f"{i.id}/",
               "granularity": i.slot_granularity_minutes,
               "status": (statuses.get(i.id) or InstrumentStatus()).text,
               "available": (statuses.get(i.id) or InstrumentStatus()).available,
               "request": request_url(cfg, i.id)}
        for i in insts}
    data = {"facility": cfg.facility.name, "timezone": cfg.facility.timezone,
            "as_of": as_of.astimezone(tz).isoformat(timespec="minutes"),
            "github": cfg.facility.github, "instruments": inst_json, "events": events}
    written: list[Path] = []

    def put(rel: str, text: str) -> None:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="")
        written.append(p)

    put("bookings.json", json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    # the same data as a script, so index.html also works from file:// (no fetch)
    put("assets/data.js", "window.KHERVELAB = " + json.dumps(data, ensure_ascii=False) + ";\n")
    for name in ("app.js", "app.css"):
        put(f"assets/{name}", (HERE / "static" / name).read_text(encoding="utf-8"))
    vendor = out / "assets" / "vendor"
    shutil.copytree(HERE / "static" / "vendor", vendor)
    written += sorted(vendor.iterdir())
    put(".nojekyll", "")

    env = _env()
    common = dict(facility=cfg.facility, as_of=as_of.astimezone(tz), groups=groups,
                  statuses=statuses, default_status=InstrumentStatus())
    put("index.html", env.get_template("index.html").render(
        root="", instrument=None, request=request_url(cfg), **common))
    stamp = as_of.replace(microsecond=0)
    put("calendar.ics", ics(cfg, bookings, cfg.facility.name, stamp))
    for i in insts:
        put(f"{i.id}/index.html", env.get_template("index.html").render(
            root="../", instrument=i, hours=_hours_text(i), request=request_url(cfg, i.id),
            **common))
        put(f"{i.id}/calendar.ics",
            ics(cfg, [b for b in bookings if b.instrument == i.id],
                f"{i.name} — {cfg.facility.name}", stamp))
    return written


def as_of_for(today: date | None = None) -> datetime:
    d = today or date.today()
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
