from __future__ import annotations

import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from khervelab.core.config import booking_yaml, load_config
from khervelab.core.facility import FacilityService
from khervelab.core.models import Booking
from khervelab.core.samples import children, lineage, next_sample_id
from khervelab.local.dataindex import DataIndex, parse_query
from khervelab.local.labels import LAYOUTS, make_labels
from khervelab.local.launch import command_for
from khervelab.local.readers import norm_region, parse_vamas

DATA = Path(__file__).parent / "data"


@pytest.fixture
def svc(facility):
    return FacilityService(facility)


# -- samples ---------------------------------------------------------------------

def test_sample_ids_are_generated_per_year():
    assert next_sample_id(set(), 2026) == "S-2026-0001"
    assert next_sample_id({"S-2026-0009", "S-2025-0100"}, 2026) == "S-2026-0010"


def test_derived_samples_trace_to_origin(svc):
    a = svc.add_sample("Pt foil", composition="Pt", preparation="as received", owner="u-0001")
    b = svc.add_sample("Pt foil, annealed 600 C", parent=a.id, preparation="UHV anneal")
    c = svc.add_sample("Pt foil, annealed then sputtered", parent=b.id)
    assert [s.id for s in lineage(svc.samples, c.id)] == [c.id, b.id, a.id]
    assert [s.id for s in children(svc.samples, a.id)] == [b.id]
    assert svc.repo.git.head.commit.message.startswith(f"sample {c.id} from {b.id}")
    cfg = load_config(svc.repo.path)
    assert cfg.samples[c.id].parent == b.id


def test_unknown_parent_rejected(svc):
    with pytest.raises(ValueError):
        svc.add_sample("orphan", parent="S-2026-9999")


def test_booking_links_samples(svc):
    s = svc.add_sample("Co3O4 film")
    d = date.today() + timedelta(days=2)
    tz = svc.tz
    b = svc.add_booking(Booking("xps", datetime(d.year, d.month, d.day, 9, tzinfo=tz),
                                datetime(d.year, d.month, d.day, 11, tzinfo=tz), "u-0001",
                                samples=(s.id,)))
    assert svc.bookings_for_sample(s.id) == [b]


def test_labels_pdf_every_layout(svc, tmp_path):
    s = [svc.add_sample(f"Sample {i}", composition="TiO2") for i in range(25)]
    for key in LAYOUTS:
        out = make_labels(s, tmp_path / f"{key}.pdf", key, skip=3)
        data = out.read_bytes()
        assert data[:4] == b"%PDF" and len(data) > 2000


# -- readers ---------------------------------------------------------------------

def test_vamas_header_fields():
    info = parse_vamas(DATA / "Pt4f.vms")
    (r,) = info.regions
    assert (r.name, r.pass_energy, r.dwell, r.scans, r.points) == ("Pt 4f", 20.0, 0.05, 1, 281)
    assert info.technique == "XPS" and info.acquired == datetime(2023, 8, 9, 8, 8, 56,
                                                                 tzinfo=timezone.utc)


def test_vamas_map_mode_multi_block():
    info = parse_vamas(DATA / "Cr2O3.vms")
    assert [r.name for r in info.regions] == ["Wide", "O 1s", "C 1s", "Cr 2p"]
    assert info.regions[1].pass_energy == 10.0


@pytest.mark.parametrize("raw,expected", [("Co2p", "Co 2p"), ("co 2p", "Co 2p"),
                                          ("C1s", "C 1s"), ("Pt 4f7/2", "Pt 4f"),
                                          ("Survey", "Survey")])
def test_region_names_normalise(raw, expected):
    assert norm_region(raw) == expected


# -- index -----------------------------------------------------------------------

def _vamas(template: str, block: str, species: str, transition: str, pe: float,
           when: datetime, sample: str) -> str:
    lines = template.splitlines()
    # zero-based lines of Pt4f.vms: block id 21, sample 22, date 23-28, PE 51, species 59-60
    lines[21], lines[22] = block, sample
    lines[23:29] = [str(when.year), str(when.month), str(when.day), str(when.hour),
                    str(when.minute), str(when.second)]
    lines[51] = f"{pe:g}"
    lines[59], lines[60] = species, transition
    return "\n".join(lines) + "\n"


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "xps-pc" / "data"
    f.mkdir(parents=True)
    return f


def _seed_booking(svc, start: datetime, hours: float, samples=()):
    b = Booking("xps", start, start + timedelta(hours=hours), "u-0001", samples=tuple(samples))
    rel = b.path(svc.tz).as_posix()
    svc.repo.write_file(rel, booking_yaml(b))
    svc.repo.commit("seed", [rel])
    svc.reload()
    return b


def test_index_match_and_search(svc, folder, tmp_path):
    tz = svc.tz
    s = svc.add_sample("Co3O4 thin film")
    when = datetime(2026, 3, 12, 10, 30, tzinfo=timezone.utc)
    _seed_booking(svc, when.astimezone(tz) - timedelta(minutes=30), 3, [s.id])
    tpl = (DATA / "Pt4f.vms").read_text(encoding="latin-1")
    (folder / "march").mkdir()
    (folder / "march" / "co_survey.vms").write_text(
        _vamas(tpl, "Co2p PE20", "Co", "2p", 20, when, "film A"), encoding="latin-1")
    (folder / "march" / "o1s.vms").write_text(
        _vamas(tpl, "O1s PE50", "O", "1s", 50, when + timedelta(minutes=20), "film A"),
        encoding="latin-1")
    (folder / "old.vms").write_text(
        _vamas(tpl, "Co2p", "Co", "2p", 20, datetime(2025, 1, 5, 9, tzinfo=timezone.utc), "x"),
        encoding="latin-1")
    shutil.copy(DATA / "Pt4f.vms", folder / f"{s.id}_Pt.vms")  # sample id in the filename
    idx = DataIndex(tmp_path / "index.db")
    idx.add_folder(folder, "xps")
    rep = idx.scan(tz)
    assert rep.added == 4 and rep.errors == 0
    idx.match(svc)

    hits = idx.search("Co 2p, 20 eV pass energy, since March", ["xps"])
    assert [h.filename for h in hits] == ["co_survey.vms"]
    assert hits[0].sample == s.id and hits[0].booking.startswith("bookings/xps/2026/03/")
    assert {h.filename for h in idx.search("Co2p")} == {"co_survey.vms", "old.vms"}
    assert [h.filename for h in idx.search("unbooked, Co 2p")] == ["old.vms"]
    assert {h.filename for h in idx.for_sample(s.id)} == {"co_survey.vms", "o1s.vms",
                                                          f"{s.id}_Pt.vms"}

    # incremental: nothing changed, nothing re-read; a deleted file leaves the index
    rep2 = idx.scan(tz)
    assert rep2.added == rep2.updated == 0
    (folder / "old.vms").unlink()
    assert idx.scan(tz).removed == 1

    # manual correction survives re-matching
    (hit,) = idx.search("O 1s")
    idx.set_link(hit.id, "", "")
    idx.match(svc)
    (hit,) = idx.search("O 1s")
    assert hit.sample == "" and hit.manual


def test_result_workbook_inherits_sample(svc, folder, tmp_path):
    from openpyxl import Workbook
    s = svc.add_sample("PEEK film")
    tpl = (DATA / "Pt4f.vms").read_text(encoding="latin-1")
    (folder / f"{s.id}-peek.vms").write_text(
        _vamas(tpl, "C1s", "C", "1s", 20, datetime(2026, 2, 1, 9, tzinfo=timezone.utc), "p"),
        encoding="latin-1")
    wb = Workbook()
    wb.active.title = "C1s"
    wb.create_sheet("Results Table")
    wb.save(folder / f"{s.id}-peek.xlsx")
    idx = DataIndex(tmp_path / "index.db")
    idx.add_folder(folder, "xps")
    idx.scan()
    idx.match(svc)
    results = idx.search("results")
    assert [r.filename for r in results] == [f"{s.id}-peek.xlsx"]
    assert results[0].sample == s.id


def test_query_parser():
    q = parse_query("Co 2p, 20 eV pass energy, since March, on xps, S-2026-0004, unbooked",
                    ["xps"], today=date(2026, 10, 5))
    assert q.text == ["Co 2p"] and q.pass_energy == 20 and q.since == date(2026, 3, 1)
    assert q.instrument == "xps" and q.sample == "S-2026-0004" and q.unbooked
    assert parse_query("since November", today=date(2026, 10, 5)).since == date(2025, 11, 1)
    assert parse_query("PE 50").pass_energy == 50


def test_thousands_of_files_index_and_search_fast(svc, folder, tmp_path):
    tpl = (DATA / "Pt4f.vms").read_text(encoding="latin-1")
    levels = [("C", "1s"), ("O", "1s"), ("Co", "2p"), ("Fe", "2p"), ("Ni", "2p"), ("Ti", "2p")]
    t0 = datetime(2024, 1, 1, 8, tzinfo=timezone.utc)
    for i in range(3000):
        el, tr = levels[i % len(levels)]
        sub = folder / f"user{i % 7}" / f"{(t0 + timedelta(hours=7 * i)):%Y-%m}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"scan_{i:05d}.vms").write_text(
            _vamas(tpl, f"{el}{tr}", el, tr, (20, 50, 100, 10, 40)[i % 5], t0 + timedelta(hours=7 * i),
                   f"s{i}"), encoding="latin-1")
    idx = DataIndex(tmp_path / "index.db")
    idx.add_folder(folder, "xps")
    rep = idx.scan()
    assert rep.added == 3000 and rep.errors == 0
    idx.match(svc)
    start = time.perf_counter()
    hits = idx.search("Co 2p, 20 eV pass energy, since 2025")
    elapsed = time.perf_counter() - start
    assert hits and all("Co 2p" in h.region_summary() for h in hits)
    assert elapsed < 1.0


def test_launch_command_expands_files():
    cmd = command_for("open -a KherveFitting {files}", [Path("/a b/x.vms"), Path("/y.vms")])
    assert cmd == ["open", "-a", "KherveFitting", "/a b/x.vms", "/y.vms"]
    assert command_for("kf", [Path("/x.vms")]) == ["kf", "/x.vms"]
