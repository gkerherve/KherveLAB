from __future__ import annotations

from datetime import time

import pytest

from khervelab.core.config import load_config, load_instrument, parse_range
from khervelab.core.yamlio import ConfigError


def test_template_loads_every_instrument(template_copy):
    cfg = load_config(template_copy)
    assert cfg.facility.timezone == "Europe/London"
    assert len(cfg.instruments) >= 45
    for must in ("xps", "nap-xps", "tem-spectra-300", "apt-leap-5000", "tofsims-leis",
                 "xrd-empyrean-1", "sta-449f5", "dil-402e", "afm-mfp3d", "bet"):
        assert must in cfg.instruments
    assert cfg.bookings == {}


def test_catalogue_marks_website_and_added_instruments(template_copy):
    cfg = load_config(template_copy)
    sources = {i.source for i in cfg.instruments.values()}
    assert sources == {"materials-website", "added"}


def test_parse_range_handles_midnight():
    r = parse_range("00:00-24:00", "x", "k")
    assert r.start == time(0) and r.end is None
    assert r.contains(time(23, 30), None)


@pytest.mark.parametrize("text", ["9-17", "18:00-09:00", "25:00-26:00", "09:00-24:30"])
def test_parse_range_rejects(text):
    with pytest.raises(ConfigError):
        parse_range(text, "x", "k")


def test_bad_instrument_names_file_and_key(template_copy):
    p = template_copy / "instruments" / "xps.yaml"
    p.write_text(p.read_text().replace("min_booking_minutes: 60", "min_booking_minutes: lots"))
    with pytest.raises(ConfigError) as exc:
        load_instrument(p)
    assert "xps.yaml" in str(exc.value) and "min_booking_minutes" in str(exc.value)


def test_id_must_match_filename(template_copy):
    p = template_copy / "instruments" / "xps.yaml"
    p.rename(template_copy / "instruments" / "other.yaml")
    with pytest.raises(ConfigError, match="file name"):
        load_config(template_copy)


def test_malformed_booking_is_never_skipped(template_copy):
    d = template_copy / "bookings" / "xps" / "2026" / "10"
    d.mkdir(parents=True)
    (d / "xps-2026-10-07-0900.yaml").write_text(
        "instrument: xps\nstart: 2026-10-07T09:00\nend: 2026-10-07T10:00+01:00\nuser: u-0001\n")
    with pytest.raises(ConfigError, match="offset"):
        load_config(template_copy)


def test_booking_in_wrong_path_is_reported(template_copy):
    d = template_copy / "bookings" / "xps" / "2026" / "10"
    d.mkdir(parents=True)
    (d / "xps-2026-10-07-1000.yaml").write_text(
        "instrument: xps\nstart: '2026-10-07T09:00+01:00'\nend: '2026-10-07T10:00+01:00'\n"
        "user: u-0001\n")
    with pytest.raises(ConfigError, match="should be at"):
        load_config(template_copy)
