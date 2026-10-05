from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from khervelab.core import logbook
from khervelab.core.config import load_config
from khervelab.core.facility import BookingRejected, FacilityService
from khervelab.core.models import Booking
from khervelab.core.yamlio import ConfigError


@pytest.fixture
def svc(facility):
    return FacilityService(facility)


def ago(hours: float) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(second=0, microsecond=0)


def xps_counter(svc, name="X-ray source filament"):
    inst = svc.instruments["xps"]
    return next(c for c in logbook.counters(inst, svc.cfg.logs, svc.bookings(),
                                            datetime.now(timezone.utc)) if c.consumable == name)


def test_logging_four_hours_advances_source_counter(svc):
    before = xps_counter(svc).used_hours
    svc.log("xps", "usage", "unbooked survey scans", hours=4)
    assert xps_counter(svc).used_hours == pytest.approx(before + 4)
    assert svc.repo.git.head.commit.message.strip() == "log xps usage 4 h: unbooked survey scans"


def test_month_file_layout_and_reload(svc):
    e = svc.log("xps", "note", "chamber pressure 2e-10 mbar")
    rel = logbook.log_path("xps", e.time, svc.tz)
    assert rel.startswith("logs/xps/") and rel.endswith(".yaml")
    svc.log("xps", "note", "second note")
    text = (svc.repo.path / rel).read_text()
    assert text.count("type: note") == 2
    cfg = load_config(svc.repo.path)
    assert [x.text for x in cfg.logs] == ["chamber pressure 2e-10 mbar", "second note"]


def test_booked_hours_count_and_reset_on_change(svc):
    tz = svc.tz
    start = datetime.now(tz).replace(minute=0, second=0, microsecond=0) - timedelta(hours=30)
    past = Booking("xps", start, start + timedelta(hours=6), "u-0001")
    rel = past.path(tz).as_posix()
    from khervelab.core.config import booking_yaml
    svc.repo.write_file(rel, booking_yaml(past))  # past bookings cannot be made through the rules
    svc.repo.commit("seed past booking", [rel])
    svc.reload()
    assert xps_counter(svc).used_hours == pytest.approx(6)
    svc.log("xps", "consumable_change", "new filament", consumable="X-ray source filament")
    assert xps_counter(svc).used_hours == pytest.approx(0)
    assert xps_counter(svc, "Ion gun filament").used_hours == pytest.approx(6)
    svc.log("xps", "correction", "meter reading", consumable="X-ray source filament", hours=1.5)
    assert xps_counter(svc).used_hours == pytest.approx(1.5)


def test_warn_and_limit_levels(svc):
    svc.log("xps", "correction", consumable="Ion gun filament", hours=950)
    assert xps_counter(svc, "Ion gun filament").level == "warn"
    svc.log("xps", "correction", consumable="Ion gun filament", hours=60)
    assert xps_counter(svc, "Ion gun filament").level == "limit"


def test_maintenance_due_from_last_completed(svc):
    inst = svc.instruments["xps"]
    today = date.today()
    due = {d.task: d for d in logbook.maintenance_due(inst, svc.cfg.logs, today, svc.tz)}
    assert due["Bake-out"].due == today  # never done: due now
    svc.log("xps", "bake_out", "bake 150 C 48 h", task="Bake-out")
    due = {d.task: d for d in logbook.maintenance_due(inst, svc.cfg.logs, today, svc.tz)}
    assert due["Bake-out"].days_left(today) == 180


def test_blocking_fault_blocks_and_publishes_status(svc):
    back = date.today() + timedelta(days=3)
    f_entry = svc.log("xps", "fault", "ion gun replacement", blocking=True, back=back)
    (fault,) = logbook.open_faults(svc.cfg.logs, "xps")
    assert fault.id == f_entry.fault and fault.blocking
    st = svc.statuses()["xps"]
    assert not st.available and st.text.startswith("down, ion gun replacement, back ")
    d = date.today() + timedelta(days=1)
    tz = svc.tz
    b = Booking("xps", datetime(d.year, d.month, d.day, 10, tzinfo=tz),
                datetime(d.year, d.month, d.day, 12, tzinfo=tz), "u-0001")
    with pytest.raises(BookingRejected, match="down"):
        svc.add_booking(b)
    later = back + timedelta(days=2)
    svc.add_booking(Booking("xps", datetime(later.year, later.month, later.day, 10, tzinfo=tz),
                            datetime(later.year, later.month, later.day, 12, tzinfo=tz), "u-0001"))
    svc.update_fault(fault, "in_progress", "replacement gun ordered")
    assert logbook.open_faults(svc.cfg.logs, "xps")[0].status == "in_progress"
    svc.update_fault(fault, "resolved", "new ion gun fitted and aligned")
    assert logbook.open_faults(svc.cfg.logs, "xps") == []
    assert svc.statuses()["xps"].text == "available"
    svc.add_booking(b)  # bookable again
    (resolved,) = logbook.faults(svc.cfg.logs, "xps")
    assert resolved.resolution == "new ion gun fitted and aligned"


def test_non_blocking_fault_is_a_known_issue(svc):
    svc.log("sem-sigma-300", "fault", "EDS detector noisy")
    st = svc.statuses()["sem-sigma-300"]
    assert st.available and "known issue" in st.text


def test_maintenance_booking_allowed_during_downtime(svc):
    svc.log("xps", "fault", "vacuum leak", blocking=True)
    d = date.today() + timedelta(days=1)
    tz = svc.tz
    svc.add_booking(Booking("xps", datetime(d.year, d.month, d.day, 10, tzinfo=tz),
                            datetime(d.year, d.month, d.day, 12, tzinfo=tz), "u-0001",
                            kind="maintenance"))


def test_status_on_published_page(svc):
    svc.log("xps", "fault", "ion gun replacement", blocking=True)
    svc.publish()
    html = (svc.repo.path / "docs" / "xps" / "index.html").read_text()
    assert "down, ion gun replacement" in html


def test_invalid_log_names_file_and_key(svc):
    p = svc.repo.path / "logs" / "xps" / "2026-10.yaml"
    p.parent.mkdir(parents=True)
    p.write_text("entries:\n  - type: explosion\n    time: '2026-10-05T10:00+01:00'\n")
    with pytest.raises(ConfigError, match=r"2026-10.yaml \[entries\[0\].type\]"):
        load_config(svc.repo.path)
