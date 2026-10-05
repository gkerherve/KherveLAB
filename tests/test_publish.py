from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from khervelab.core.config import load_config
from khervelab.core.facility import FacilityService
from khervelab.core.models import Booking, User
from khervelab.publish.build import SITE_DIR, InstrumentStatus, build_site, ics, request_url

LON = ZoneInfo("Europe/London")


def weekday(offset=2, hour=9):
    d = datetime.now(LON).replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=offset)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def svc_with_booking(facility):
    svc = FacilityService(facility)
    svc.save_user(User("u-0002", "GK group", permissions=("xps",)))
    b = svc.add_booking(Booking("xps", weekday(), weekday(hour=12), "u-0002",
                                samples=("S-2026-0001",), notes="secret sample notes"))
    return svc, b


def test_site_has_pages_feeds_and_no_private_fields(facility, tmp_path):
    svc, b = svc_with_booking(facility)
    out = tmp_path / "site"
    build_site(svc.cfg, out, datetime.now(timezone.utc))
    for rel in ("index.html", "bookings.json", "assets/data.js", "assets/app.js",
                "assets/vendor/fullcalendar.min.js", "calendar.ics", "xps/index.html",
                "xps/calendar.ics", "tem-2100f/index.html", ".nojekyll"):
        assert (out / rel).exists(), rel
    data = json.loads((out / "bookings.json").read_text())
    (ev,) = data["events"]
    assert ev["title"] == "GK group" and ev["instrument"] == "xps"
    blob = "".join(p.read_text(errors="ignore") for p in out.rglob("*") if p.is_file()
                   and p.suffix in (".html", ".json", ".js", ".ics") and "vendor" not in p.parts)
    assert "secret sample notes" not in blob and "S-2026-0001" not in blob


def test_pages_work_from_file_urls(facility, tmp_path):
    svc, _ = svc_with_booking(facility)
    out = tmp_path / "site"
    build_site(svc.cfg, out, datetime.now(timezone.utc))
    html = (out / "xps" / "index.html").read_text()
    assert 'src="../assets/data.js"' in html and "fetch(" not in (out / "assets/app.js").read_text()
    assert "https://" not in html.split("<script")[1]  # nothing loaded from a CDN


def test_ics_is_valid_rfc5545(facility):
    svc, b = svc_with_booking(facility)
    text = ics(svc.cfg, [b], "Test", datetime(2026, 10, 5, tzinfo=timezone.utc))
    lines = text.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and text.endswith("END:VCALENDAR\r\n")
    assert all(len(l.encode()) <= 75 for l in lines)
    assert f"DTSTART:{b.start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}" in lines
    assert sum(l == "BEGIN:VEVENT" for l in lines) == 1


def test_ics_folds_long_utf8_lines():
    from khervelab.publish.build import _fold
    folded = _fold("SUMMARY:" + "é" * 80)
    assert all(len(p.encode()) <= 75 for p in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == "SUMMARY:" + "é" * 80


def test_status_shown(facility, tmp_path):
    svc, _ = svc_with_booking(facility)
    build_site(svc.cfg, tmp_path / "s", datetime.now(timezone.utc),
               {"xps": InstrumentStatus(False, "down, ion gun replacement")})
    assert "down, ion gun replacement" in (tmp_path / "s" / "xps" / "index.html").read_text()


def test_request_url_needs_github(facility):
    svc, _ = svc_with_booking(facility)
    assert request_url(svc.cfg, "xps") == ""
    fy = facility.path / "facility.yaml"
    fy.write_text(fy.read_text().replace("github: ''", "github: lab/facility"))
    cfg = load_config(facility.path)
    url = request_url(cfg, "xps", "2026-10-07", "09:00", "2")
    assert url.startswith("https://github.com/lab/facility/issues/new?")
    assert "template=booking-request.yml" in url and "instrument=xps" in url


def test_publish_commits_once_and_is_idempotent(facility):
    svc, _ = svc_with_booking(facility)
    assert svc.publish() is True
    assert facility.git.head.commit.message.strip() == "publish calendar"
    assert svc.publish() is False
    assert (facility.path / SITE_DIR / "index.html").exists()
