from __future__ import annotations

from datetime import datetime, timedelta

from khervelab import db, logic
from tests.conftest import flashed


def slot(hour, days=2, minutes=0):
    d = datetime.now().replace(hour=hour, minute=minutes, second=0, microsecond=0) + timedelta(days=days)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%dT%H:%M")


def setup_lab(b, examples=("XPS",)):
    data = {"lab_name": "APSL", "currency": "£", "full_name": "Lab Manager",
            "email": "manager@lab.example", "username": "manager", "password": "managerpass",
            "password2": "managerpass"}
    for ex in examples:
        data[f"ex_{ex}"] = "on"
    return b.post("/setup", data, follow_redirects=True)


def register(b, username="alice", category="Internal"):
    return b.post("/register", {"full_name": username.title(), "email": f"{username}@lab.example",
                                "group_name": "Surfaces", "category": category,
                                "username": username, "password": "alicepass1",
                                "password2": "alicepass1"}, follow_redirects=True)


def login(b, username, password):
    return b.post("/login", {"username": username, "password": password}, follow_redirects=True)


def test_first_run_goes_to_setup(browser):
    r = browser.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/setup")
    html = setup_lab(browser).get_data(as_text=True)
    assert "Instruments and rates" in html and "XPS" in html
    assert browser.get("/setup").status_code == 302  # only once


def test_post_without_csrf_is_refused(browser):
    setup_lab(browser)
    r = browser.c.post("/admin/settings", data={"lab_name": "hacked"})
    assert r.status_code == 400


def test_full_flow_register_approve_book_report(app, browser, new_browser):
    setup_lab(browser, examples=("NAP-XPS",))           # manual approval
    conn = db.connect(app.config["DB_PATH"])
    iid = conn.execute("SELECT id FROM instruments WHERE name='NAP-XPS'").fetchone()[0]
    browser.post(f"/admin/instruments/{iid}", {
        "name": "NAP-XPS", "approval": "manual", "active": "on", "slot_minutes": "30",
        "min_minutes": "60", "max_minutes": "600", "max_days_ahead": "60",
        "open_time": "08:00", "close_time": "20:00", "colour": "#8250df",
        "rate_Internal": "60", "rate_External academic": "90", "rate_Industry": "250"},
        follow_redirects=True)
    assert logic.rate_for(conn, iid, "Internal") == 60

    alice = new_browser()
    html = register(alice).get_data(as_text=True)
    assert "will approve it" in html
    assert "waiting for the administrator" in login(alice, "alice", "alicepass1").get_data(as_text=True)

    uid = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
    assert "New accounts (1)" in browser.text("/admin/requests")
    browser.post(f"/admin/accounts/{uid}/decide", {"decision": "approve"})
    assert "Instruments" in login(alice, "alice", "alicepass1").get_data(as_text=True)

    r = alice.post("/book", {"instrument_id": iid, "start": slot(9), "end": slot(12),
                             "purpose": "CO oxidation on Pt"}, follow_redirects=True)
    assert "Request sent" in r.get_data(as_text=True)
    bid = conn.execute("SELECT id FROM bookings").fetchone()[0]

    # the slot is taken while pending
    bob = new_browser()
    register(bob, "bob")
    conn.execute("UPDATE users SET status='active' WHERE username='bob'")
    login(bob, "bob", "alicepass1")
    r = bob.post("/book", {"instrument_id": iid, "start": slot(10), "end": slot(11)},
                 follow_redirects=True)
    assert "overlaps" in r.get_data(as_text=True)

    assert "CO oxidation on Pt" in browser.text("/admin/requests")
    browser.post(f"/admin/bookings/{bid}/decide", {"decision": "approve", "note": "ok"})
    mine = alice.text("/bookings")
    assert "approved" in mine and "£180.00" in mine

    day = slot(9)[:10]
    rep = browser.text(f"/admin/reports?from={day}&to={day}")
    assert "Alice" in rep and "£180.00" in rep
    pdf = browser.get(f"/admin/reports/export?from={day}&to={day}&user={uid}&format=pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    assert "usage-alice" in pdf.headers["Content-Disposition"]
    csv = browser.get(f"/admin/reports/export?from={day}&to={day}&format=csv")
    assert "CO oxidation on Pt" in csv.get_data(as_text=True)
    xlsx = browser.get(f"/admin/reports/export?from={day}&to={day}&format=xlsx")
    assert xlsx.data[:2] == b"PK"


def test_users_cannot_reach_admin_pages(app, browser, new_browser):
    setup_lab(browser)
    conn = db.connect(app.config["DB_PATH"])
    db.set_setting(conn, "account_approval", "0")
    u = new_browser()
    register(u)
    login(u, "alice", "alicepass1")
    for url in ("/admin/requests", "/admin/users", "/admin/reports", "/admin/settings",
                "/admin/backup", "/admin/instruments"):
        assert u.get(url).status_code == 403, url


def test_events_feed_and_names(app, browser, new_browser):
    setup_lab(browser, examples=("BET",))               # auto approval
    conn = db.connect(app.config["DB_PATH"])
    db.set_setting(conn, "account_approval", "0")
    iid = conn.execute("SELECT id FROM instruments").fetchone()[0]
    a, b = new_browser(), new_browser()
    register(a, "alice")
    register(b, "bob")
    login(a, "alice", "alicepass1")
    login(b, "bob", "alicepass1")
    r = a.post("/book", {"instrument_id": iid, "start": slot(9), "end": slot(11)},
               follow_redirects=True)
    assert "Booked BET" in r.get_data(as_text=True)
    day = slot(0)[:10]
    q = f"/api/events?instrument={iid}&start={day}T00:00:00&end={day}T23:59:00"
    assert a.get(q).json[0]["title"] == "You"
    assert b.get(q).json[0]["title"] == "Alice"
    db.set_setting(conn, "show_names", "0")
    assert b.get(q).json[0]["title"] == "Booked"
    assert browser.get(q).json[0]["title"] == "Alice"   # the manager always sees names


def test_user_cancels_own_future_booking(app, browser, new_browser):
    setup_lab(browser, examples=("BET",))
    conn = db.connect(app.config["DB_PATH"])
    db.set_setting(conn, "account_approval", "0")
    iid = conn.execute("SELECT id FROM instruments").fetchone()[0]
    a = new_browser()
    register(a)
    login(a, "alice", "alicepass1")
    a.post("/book", {"instrument_id": iid, "start": slot(9), "end": slot(10)})
    bid = conn.execute("SELECT id FROM bookings").fetchone()[0]
    r = a.post(f"/bookings/{bid}/cancel", follow_redirects=True)
    assert "cancelled" in " ".join(flashed(r.get_data(as_text=True)))
    assert conn.execute("SELECT status FROM bookings").fetchone()[0] == "cancelled"


def test_settings_and_backup(app, browser):
    setup_lab(browser)
    browser.post("/admin/settings", {"lab_name": "Surface Lab", "currency": "€",
                                     "timezone": "Europe/Paris",
                                     "categories": "Academic\nCommercial",
                                     "account_approval": "on"})
    html = browser.text("/admin/instruments")
    assert "Surface Lab" in html and "Academic (€/h)" in html
    r = browser.get("/admin/backup")
    assert r.status_code == 200 and r.data[:15] == b"SQLite format 3"


def test_manager_cannot_lock_themselves_out(app, browser):
    setup_lab(browser)
    conn = db.connect(app.config["DB_PATH"])
    uid = conn.execute("SELECT id FROM users").fetchone()[0]
    r = browser.post(f"/admin/users/{uid}", {"full_name": "Lab Manager", "role": "user",
                                             "status": "active", "category": "Internal"},
                     follow_redirects=True)
    assert "cannot remove your own" in r.get_data(as_text=True)
    assert conn.execute("SELECT role FROM users").fetchone()[0] == "admin"


def test_all_pages_render(app, browser):
    setup_lab(browser, examples=("XPS", "BET"))
    conn = db.connect(app.config["DB_PATH"])
    iid = conn.execute("SELECT id FROM instruments").fetchone()[0]
    for url in ("/", f"/instrument/{iid}", "/bookings", "/account", "/admin/requests",
                "/admin/bookings", "/admin/instruments", "/admin/instruments/new",
                f"/admin/instruments/{iid}", "/admin/users", "/admin/users/1", "/admin/reports",
                "/admin/settings"):
        r = browser.get(url)
        assert r.status_code == 200, url
    assert browser.get("/instrument/9999").status_code == 404


def test_session_booking_through_the_web(app, browser, new_browser):
    setup_lab(browser, examples=("BET",))
    conn = db.connect(app.config["DB_PATH"])
    db.set_setting(conn, "account_approval", "0")
    iid = conn.execute("SELECT id FROM instruments").fetchone()[0]
    conn.execute("UPDATE instruments SET booking_mode='sessions' WHERE id=?", (iid,))
    logic.save_sessions(conn, iid, [
        logic.SessionSpec("Morning", "08:00", "12:30", "01234", {"Internal": 150.0}),
        logic.SessionSpec("Afternoon", "12:30", "17:00", "01234", {"Internal": 150.0})])
    a = new_browser()
    register(a)
    login(a, "alice", "alicepass1")
    page = a.text(f"/instrument/{iid}")
    assert "booked by session" in page and "Morning 08:00–12:30" in page
    day = slot(9)[:10]
    r = a.post("/book", {"instrument_id": iid, "start": f"{day}T10:00", "end": f"{day}T14:00"},
               follow_redirects=True)
    assert "Booked 2 BET sessions" in r.get_data(as_text=True)
    rows = conn.execute("SELECT start, end, price FROM bookings ORDER BY start").fetchall()
    assert [(x[0][11:], x[1][11:], x[2]) for x in rows] == [("08:00", "12:30", 150.0),
                                                           ("12:30", "17:00", 150.0)]
    ev = a.get(f"/api/events?instrument={iid}&start={day}T00:00:00&end={day}T23:59:00").json
    assert sum(1 for e in ev if e.get("display") == "background") == 2


def test_web_selection_in_the_evening_books_the_whole_evening(app, browser, new_browser):
    setup_lab(browser, examples=("BET",))
    conn = db.connect(app.config["DB_PATH"])
    db.set_setting(conn, "account_approval", "0")
    iid = conn.execute("SELECT id FROM instruments").fetchone()[0]
    conn.execute("UPDATE instruments SET open_time='08:00', close_time='17:00', "
                 "evening_mode='block', evening_start='17:00', evening_end='08:00' WHERE id=?",
                 (iid,))
    a = new_browser()
    register(a)
    login(a, "alice", "alicepass1")
    day = slot(9)[:10]
    a.post("/book", {"instrument_id": iid, "start": f"{day}T19:00", "end": f"{day}T20:00"})
    row = conn.execute("SELECT start, end FROM bookings").fetchone()
    assert row[0] == f"{day}T17:00" and row[1].endswith("T08:00")
    assert "Evening 17:00–08:00" in a.text(f"/instrument/{iid}")
