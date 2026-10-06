from __future__ import annotations

from datetime import date, datetime

import pytest

from khervelab import analytics, logic, ppms

# Made-up exports in the shapes PPMS installations commonly produce.
SYSTEMS = "System;Type;Description\nXPS-1;Spectroscopy;Photoelectron\nSEM-2;Microscopy;\n"
USERS = ("Login,First name,Last name,Email,Group,User type,Active\n"
         "jdoe,Jo,Doe,jo@uni.example,Surfaces,Internal,yes\n"
         "asmith,Ann,Smith,ann@corp.example,Acme Ltd,Industrial,yes\n"
         "old,Old,User,,Surfaces,Internal,no\n")
PRICES = "System,User type,Price per hour\nXPS-1,Internal,40\nXPS-1,Industrial,\"1,200.00\"\n"
RIGHTS = "Login,System,Rights\njdoe,XPS-1,A\nasmith,XPS-1,N\n"
BOOKINGS = ("Title line exported by PPMS\n"
            "System,Login,Date,Start time,End time,Amount,Status,Project,Comment\n"
            "XPS-1,jdoe,03/02/2026,09:00,11:30,\"£100.00\",Booked,P-17,Co3O4\n"
            "XPS-1,asmith,04/02/2026,22:00,02:00,\"1.234,50\",,,overnight\n"
            "SEM-2,ghost,05/02/2026,10:00,11:00,,Cancelled,,\n"
            "XPS-1,jdoe,bad date,10:00,11:00,,,,\n")


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return ppms.read_table(p)


def _sources(tmp_path):
    out = []
    for name, text in (("systems.csv", SYSTEMS), ("users.csv", USERS), ("prices.csv", PRICES),
                       ("rights.csv", RIGHTS), ("bookings.csv", BOOKINGS)):
        t = _write(tmp_path, name, text)
        kind = ppms.guess_kind(t.headers)
        out.append(ppms.Source(kind, t, ppms.guess_mapping(kind, t.headers)))
    return out


def test_files_are_recognised_and_columns_matched(tmp_path):
    kinds = [s.kind for s in _sources(tmp_path)]
    assert kinds == ["systems", "users", "prices", "rights", "bookings"]
    b = _sources(tmp_path)[-1]
    assert b.table.headers[0] == "System"                 # the title line is skipped
    assert b.mapping["date"] == "Date" and b.mapping["start"] == "Start time"
    assert b.mapping["amount"] == "Amount" and b.mapping["project"] == "Project"


def test_dry_run_changes_nothing_and_import_matches_it(conn, tmp_path):
    before = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    dry = ppms.run(conn, _sources(tmp_path), dry_run=True)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before
    real = ppms.run(conn, _sources(tmp_path), dry_run=False)
    assert dry.counts == real.counts
    c = real.counts
    assert c["instruments created"] == 2 and c["users created"] == 3
    assert c["users created from bookings"] == 1 and c["bookings imported"] == 3
    assert c["rows skipped"] == 1 and "cannot read the start" in real.problems[0]
    again = ppms.run(conn, _sources(tmp_path), dry_run=False)
    assert again.counts["already imported"] == 3 and "bookings imported" not in again.counts


def test_imported_history_is_priced_as_charged(conn, tmp_path):
    ppms.run(conn, _sources(tmp_path), dry_run=False)
    rows = {r["username"]: r for r in conn.execute(
        "SELECT b.*, u.username, u.category FROM bookings b JOIN users u ON u.id=b.user_id")}
    jo = rows["jdoe"]
    assert (jo["start"], jo["end"]) == ("2026-02-03T09:00", "2026-02-03T11:30")
    assert logic.cost(conn, jo) == 100.0 and "Project: P-17" in jo["purpose"]
    night = rows["asmith"]
    assert night["end"] == "2026-02-05T02:00" and logic.cost(conn, night) == 1234.5
    assert night["category"] == "Industry" and night["rate"] == 1200.0
    assert rows["ghost"]["status"] == "cancelled"
    a = analytics.build(conn, date(2026, 2, 1), date(2026, 2, 28))
    assert a.kpis[0].value == "£1,334.50"
    basis = a.charts["price_basis"]
    assert basis.labels == ["Fixed charge (imported)"]


def test_users_rights_and_categories(conn, tmp_path):
    ppms.run(conn, _sources(tmp_path), dry_run=False)
    u = {r["username"]: r for r in conn.execute("SELECT * FROM users")}
    assert u["jdoe"]["full_name"] == "Jo Doe" and u["jdoe"]["group_name"] == "Surfaces"
    assert u["old"]["status"] == "disabled"
    xps = conn.execute("SELECT id FROM instruments WHERE name='XPS-1'").fetchone()[0]
    assert logic.is_authorised(conn, u["jdoe"]["id"], xps)
    assert not logic.is_authorised(conn, u["asmith"]["id"], xps)     # novice


def test_imported_accounts_are_claimed_with_their_email(conn, tmp_path):
    ppms.run(conn, _sources(tmp_path), dry_run=False)
    assert logic.authenticate(conn, "jdoe", "") is None
    assert logic.unclaimed(conn, "jdoe")
    with pytest.raises(ValueError, match="not the email"):
        logic.claim_account(conn, "jdoe", "someone@else.example", "newpassword1")
    with pytest.raises(ValueError, match="no email"):
        logic.claim_account(conn, "old", "", "newpassword1")
    logic.claim_account(conn, "jdoe", "JO@uni.example", "newpassword1")
    assert logic.authenticate(conn, "jdoe", "newpassword1")
    with pytest.raises(ValueError, match="already has a password"):
        logic.claim_account(conn, "jdoe", "jo@uni.example", "another-one")


@pytest.mark.parametrize("text, dayfirst, expected", [
    ("03/02/2026 09:30", True, datetime(2026, 2, 3, 9, 30)),
    ("03/02/2026 09:30", False, datetime(2026, 3, 2, 9, 30)),
    ("2026-02-03 09:30:00", True, datetime(2026, 2, 3, 9, 30)),
    ("3 Feb 2026 2:15 PM", True, datetime(2026, 2, 3, 14, 15)),
    ("03.02.2026 09:30", True, datetime(2026, 2, 3, 9, 30)),
])
def test_dates(text, dayfirst, expected):
    assert ppms.parse_datetime(text, dayfirst) == expected


def test_numbers_and_types():
    assert ppms.parse_number("£1,234.50") == 1234.5
    assert ppms.parse_number("1.234,50 €") == 1234.5
    assert ppms.parse_number("12,5") == 12.5
    assert ppms.parse_hours("2:30") == 2.5
    cats = ["Internal", "External academic", "Industry"]
    assert ppms.match_category("Commercial", cats) == "Industry"
    assert ppms.match_category("Academic (external)", cats) == "External academic"
    assert ppms.match_category("Martian", cats) is None


def test_excel_export_and_incidents(conn, tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["System", "Start date", "End date", "Severity", "Description"])
    ws.append(["XPS-1", datetime(2026, 3, 1, 8), datetime(2026, 3, 2, 8), "Critical",
               "Pump failure"])
    ws.append(["XPS-1", datetime(2026, 3, 9, 8), None, "Minor", "Noisy"])
    wb.save(tmp_path / "incidents.xlsx")
    t = ppms.read_table(tmp_path / "incidents.xlsx")
    assert ppms.guess_kind(t.headers) == "incidents"
    s = ppms.run(conn, [ppms.Source("incidents", t, ppms.guess_mapping("incidents", t.headers))],
                 dry_run=False)
    assert s.counts["incidents imported"] == 2
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM issues ORDER BY start")]
    assert kinds == ["down", "problem"]
