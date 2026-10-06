from __future__ import annotations

import os
import socket
import urllib.request
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt")

from PyQt6.QtCore import QPoint, QPointF, QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

from khervelab import db, logic  # noqa: E402
from khervelab.gui.admin import InstrumentsDialog, NetworkServer, UsersDialog  # noqa: E402
from khervelab.gui.calendar import GUTTER, HEADER, PPM, BookingItem  # noqa: E402
from khervelab.gui.dialogs import (BookingDetailsDialog, BookingDialog, LoginDialog,  # noqa: E402
                                   SetupDialog, qdt)
from khervelab.gui.main_window import MainWindow  # noqa: E402
from khervelab.gui.panels import ReportsDialog  # noqa: E402


@pytest.fixture(autouse=True)
def no_popups(monkeypatch):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


@pytest.fixture
def lab(tmp_path):
    conn = db.connect(tmp_path / "lab.db")
    db.init(conn)
    boss = logic.create_user(conn, "manager", "managerpass", "Lab Manager", role="admin",
                             status="active")
    alice = logic.create_user(conn, "alice", "alicepass1", "Alice Martin", group_name="Surfaces",
                              category="Internal", status="active")
    xps = logic.add_instrument(conn, *logic.EXAMPLES[0])      # trained-only auto, 24/7
    nap = logic.add_instrument(conn, *logic.EXAMPLES[1])      # manual, weekdays 08-20
    conn.execute("INSERT INTO rates VALUES (?, 'Internal', 45)", (xps,))
    conn.execute("INSERT INTO authorised VALUES (?, ?)", (alice, xps))
    row = lambda uid: conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()  # noqa
    return {"conn": conn, "dir": tmp_path, "boss": row(boss), "alice": row(alice),
            "xps": xps, "nap": nap}


def window(qtbot, lab, who="alice"):
    st = QSettings(str(lab["dir"] / "s.ini"), QSettings.Format.IniFormat)
    w = MainWindow(lab["conn"], lab[who], lab["dir"], st)
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


def weekday(days=2) -> date:
    d = date.today() + timedelta(days=days)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def at(d: date, h: int) -> datetime:
    return datetime(d.year, d.month, d.day, h)


def items(w):
    return [i for i in w.tabs.currentWidget().view.scene().items() if isinstance(i, BookingItem)]


def test_setup_and_login(qtbot, tmp_path):
    conn = db.connect(tmp_path / "new.db")
    db.init(conn)
    dlg = SetupDialog(conn)
    qtbot.addWidget(dlg)
    dlg.lab.setText("Surface Lab")
    dlg.full_name.setText("Boss")
    dlg.username.setText("boss")
    dlg.pw1.setText("bosspass1")
    dlg.pw2.setText("bosspass1")
    dlg.examples[0][0].setChecked(True)
    dlg._ok()
    assert dlg.user_id and db.setting(conn, "lab_name") == "Surface Lab"
    assert conn.execute("SELECT name FROM instruments").fetchone()[0] == "XPS"
    login = LoginDialog(conn)
    qtbot.addWidget(login)
    login.username.setText("boss")
    login.password.setText("bosspass1")
    login._ok()
    assert login.user["username"] == "boss"


def test_tabs_one_per_instrument(qtbot, lab):
    w = window(qtbot, lab)
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == ["All instruments", "NAP-XPS",
                                                                   "XPS"]
    assert w.requests is None            # users get no management tools


def test_booking_dialog_instant_and_pending(qtbot, lab):
    d = weekday()
    dlg = BookingDialog(lab["conn"], lab["alice"], lab["xps"], at(d, 9), at(d, 11))
    qtbot.addWidget(dlg)
    assert "£90.00" in dlg.info.text() and "Approved at once" in dlg.info.text()
    dlg._ok()
    assert dlg.result_status == "approved"
    dlg2 = BookingDialog(lab["conn"], lab["alice"], lab["nap"], at(d, 9), at(d, 11))
    qtbot.addWidget(dlg2)
    assert "approval" in dlg2.info.text()
    dlg2._ok()
    assert dlg2.result_status == "pending"
    clash = BookingDialog(lab["conn"], lab["alice"], lab["xps"], at(d, 10), at(d, 12))
    qtbot.addWidget(clash)
    from PyQt6.QtWidgets import QDialogButtonBox
    assert not clash.bb.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_instrument_tab_shows_its_bookings_and_all_tab_shows_both(qtbot, lab):
    d = weekday()
    logic.book(lab["conn"], lab["xps"], lab["alice"]["id"], at(d, 9), at(d, 11))
    logic.book(lab["conn"], lab["nap"], lab["alice"]["id"], at(d, 9), at(d, 11))
    w = window(qtbot, lab)
    w.anchor = d
    w.tabs.setCurrentIndex(2)            # XPS
    w.refresh()
    assert len(items(w)) == 1
    w.tabs.setCurrentIndex(0)
    w.set_mode("day")
    cols = [c.instrument for c in w.tabs.currentWidget().view.columns]
    assert cols == [lab["nap"], lab["xps"]]
    assert len(items(w)) == 2


def test_drag_on_instrument_tab_requests_booking(qtbot, lab):
    w = window(qtbot, lab)
    d = weekday()
    w.anchor = d
    w.tabs.setCurrentIndex(2)
    w.set_mode("week")
    view = w.tabs.currentWidget().view
    view.verticalScrollBar().setValue(int(8 * 60 * PPM))
    col = d.weekday()

    def vp(minutes):
        p = view.mapFromScene(GUTTER + col * view.colw + view.colw / 2, HEADER + minutes * PPM + 2)
        return QPoint(p.x(), p.y())
    got = []
    view.createRequested.disconnect()
    view.createRequested.connect(lambda i, s, e: got.append((i, s, e)))
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=vp(9 * 60))
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent
    view.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(vp(11 * 60)),
                                    QPointF(vp(11 * 60)), Qt.MouseButton.NoButton,
                                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=vp(11 * 60))
    assert got == [(lab["xps"], at(d, 9), at(d, 11))]


def test_move_and_details(qtbot, lab):
    d = weekday()
    res = logic.book(lab["conn"], lab["xps"], lab["alice"]["id"], at(d, 9), at(d, 11))
    w = window(qtbot, lab)
    w.move_booking(res.id, at(d, 13), at(d, 15), lab["xps"])
    b = lab["conn"].execute("SELECT * FROM bookings").fetchone()
    assert b["start"].endswith("13:00") and b["status"] == "approved"
    dlg = BookingDetailsDialog(lab["conn"], lab["alice"], res.id)
    qtbot.addWidget(dlg)
    assert dlg.editable
    dlg.end.setDateTime(qdt(at(d, 16)))
    dlg._save()
    assert lab["conn"].execute("SELECT end FROM bookings").fetchone()[0].endswith("16:00")
    dlg = BookingDetailsDialog(lab["conn"], lab["alice"], res.id)
    qtbot.addWidget(dlg)
    dlg._cancel()
    assert lab["conn"].execute("SELECT status FROM bookings").fetchone()[0] == "cancelled"


def test_manager_requests_and_approval(qtbot, lab):
    d = weekday()
    res = logic.book(lab["conn"], lab["nap"], lab["alice"]["id"], at(d, 9), at(d, 11))
    w = window(qtbot, lab, "boss")
    assert w.requests.bookings.rowCount() == 1 and w.a_requests.text() == "Requests (1)"
    w.requests.bookings.selectRow(0)
    w.requests._booking(True)
    assert lab["conn"].execute("SELECT status FROM bookings WHERE id=?",
                               (res.id,)).fetchone()[0] == "approved"
    assert w.a_requests.text() == "Requests"


def test_instruments_dialog_saves_rates_and_training(qtbot, lab):
    dlg = InstrumentsDialog(lab["conn"])
    qtbot.addWidget(dlg)
    dlg._add()
    dlg.name.setText("Raman")
    dlg.rates["Internal"].setValue(25)
    dlg.approval_btns["auto"].setChecked(True)
    dlg._save()
    iid = lab["conn"].execute("SELECT id FROM instruments WHERE name='Raman'").fetchone()[0]
    assert logic.rate_for(lab["conn"], iid, "Internal") == 25
    assert lab["conn"].execute("SELECT approval FROM instruments WHERE id=?",
                               (iid,)).fetchone()[0] == "auto"


def test_manager_cannot_demote_self(qtbot, lab):
    dlg = UsersDialog(lab["conn"], lab["boss"])
    qtbot.addWidget(dlg)
    for r in range(dlg.table.rowCount()):
        if dlg.table.item(r, 1).text() == "manager":
            dlg.table.selectRow(r)
    dlg.role.setCurrentIndex(dlg.role.findData("user"))
    dlg._save()
    assert lab["conn"].execute("SELECT role FROM users WHERE username='manager'"
                               ).fetchone()[0] == "admin"


def test_reports_dialog(qtbot, lab):
    d = weekday()
    logic.book(lab["conn"], lab["xps"], lab["alice"]["id"], at(d, 9), at(d, 11))
    dlg = ReportsDialog(lab["conn"], lab["boss"])
    qtbot.addWidget(dlg)
    from PyQt6.QtCore import QDate
    dlg.start.setDate(QDate(d.year, d.month, d.day))
    dlg.end.setDate(QDate(d.year, d.month, d.day))
    assert dlg.rep.total_cost == 90.0 and dlg.by_user.rowCount() == 1
    mine = ReportsDialog(lab["conn"], lab["alice"])
    qtbot.addWidget(mine)
    assert not mine.user.isEnabled()          # users only see their own statement


def test_network_server_serves_the_same_lab(qtbot, lab):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = NetworkServer(lab["dir"])
    server.start(port, everyone=False)
    try:
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=5).read()
        assert b"Log in" in html
    finally:
        server.stop()
    assert not server.running


def make_sessions(lab):
    conn = lab["conn"]
    conn.execute("UPDATE instruments SET booking_mode='sessions' WHERE id=?", (lab["nap"],))
    specs = [logic.SessionSpec(n, s, e, d, {"Internal": 200.0} if n != "Evening and overnight"
                               else {}) for n, s, e, d in
             logic.SESSION_TEMPLATES["Two day sessions and an evening run"]]
    logic.save_sessions(conn, lab["nap"], specs)
    conn.execute("INSERT INTO rates VALUES (?, 'Internal', 20)", (lab["nap"],))
    conn.execute("UPDATE instruments SET approval='auto' WHERE id=?", (lab["nap"],))


def test_instrument_dialog_sets_up_sessions(qtbot, lab):
    dlg = InstrumentsDialog(lab["conn"])
    qtbot.addWidget(dlg)
    for r in range(dlg.list.count()):
        if dlg.list.item(r).text().startswith("NAP-XPS"):
            dlg.list.setCurrentRow(r)
    dlg.mode_sessions.setChecked(True)
    assert not dlg.slot.isEnabled()
    dlg.sessions._template(1)                       # two day sessions and an evening run
    dlg.sessions.table.item(0, dlg.sessions.FIXED).setText("180")   # Internal, morning
    dlg._save()
    rows = logic.sessions(lab["conn"], lab["nap"])
    assert [r["name"] for r in rows] == ["Morning", "Afternoon", "Evening and overnight"]
    assert logic.session_price(lab["conn"], rows[0]["id"], "Internal") == 180
    assert logic.session_price(lab["conn"], rows[1]["id"], "Internal") is None
    mode = lab["conn"].execute("SELECT booking_mode FROM instruments WHERE id=?",
                               (lab["nap"],)).fetchone()[0]
    assert mode == "sessions"


def test_booking_dialog_lists_and_books_sessions(qtbot, lab):
    make_sessions(lab)
    d = weekday()
    dlg = BookingDialog(lab["conn"], lab["alice"], lab["nap"], at(d, 10), at(d, 14))
    qtbot.addWidget(dlg)
    assert dlg.session_list.isVisibleTo(dlg) and not dlg.start.isVisibleTo(dlg)
    picked = [s.strftime("%H:%M") for s, _, _ in dlg.chosen()]
    assert picked == ["08:00", "12:30"]             # the two sessions the drag touched
    assert "£400.00" in dlg.info.text()
    dlg._ok()
    assert len(dlg.result_ids) == 2
    prices = [r[0] for r in lab["conn"].execute("SELECT price FROM bookings ORDER BY start")]
    assert prices == [200.0, 200.0]
    again = BookingDialog(lab["conn"], lab["alice"], lab["nap"], at(d, 10), at(d, 11))
    qtbot.addWidget(again)
    assert again.chosen() == []                     # morning now taken, shown disabled


def test_session_bands_and_snapping(qtbot, lab):
    make_sessions(lab)
    d = weekday()
    res = logic.book(lab["conn"], lab["nap"], lab["alice"]["id"], at(d, 8),
                     at(d, 12) + timedelta(minutes=30))
    w = window(qtbot, lab)
    w.anchor = d
    w.tabs.setCurrentIndex(1)                       # NAP-XPS tab
    w.set_mode("week")
    texts = [i.text() for i in w.tabs.currentWidget().view.scene().items()
             if hasattr(i, "text") and callable(i.text)]
    assert any(t.startswith("Morning 08:00") for t in texts)
    assert "Sessions —" in w.tabs.currentWidget().info.text()
    # dropped part-way into the afternoon, it snaps to the afternoon session
    w.move_booking(res.id, at(d, 12), at(d, 16), lab["nap"])
    b = lab["conn"].execute("SELECT start, end FROM bookings").fetchone()
    assert (b[0][11:], b[1][11:]) == ("12:30", "17:00")


def test_durations_in_minutes_or_hours_up_to_a_day(qtbot, lab):
    from khervelab.gui.admin import DurationEdit
    w = DurationEdit(1440)
    qtbot.addWidget(w)
    w.setMinutes(270)
    assert w.unit.currentText() == "h" and w.value_box.value() == 4.5
    w.unit.setCurrentIndex(0)                       # switch to minutes: same length
    assert w.value_box.value() == 270 and w.minutes() == 270
    w.unit.setCurrentIndex(1)
    w.value_box.setValue(30)                        # clamped to 24 h
    assert w.minutes() == 1440
    dlg = InstrumentsDialog(lab["conn"])
    qtbot.addWidget(dlg)
    dlg.slot.setMinutes(1440)
    dlg.min.setMinutes(1440)
    dlg.max.setMinutes(4320)
    dlg._save()
    iid = dlg.current
    row = lab["conn"].execute("SELECT slot_minutes, min_minutes, max_minutes FROM instruments "
                              "WHERE id=?", (iid,)).fetchone()
    assert tuple(row) == (1440, 1440, 4320)


def test_editor_sets_evening_and_weekend(qtbot, lab):
    from PyQt6.QtCore import QTime
    dlg = InstrumentsDialog(lab["conn"])
    qtbot.addWidget(dlg)
    dlg.all_day.setChecked(False)
    dlg.open_t.setTime(QTime(8, 0))
    dlg.close_t.setTime(QTime(17, 0))
    dlg.slot.setMinutes(270)
    ev = dlg.period["evening"]
    ev["mode"].setCurrentIndex(ev["mode"].findData("block"))
    ev["start"].setTime(QTime(17, 0))
    ev["end"].setTime(QTime(8, 0))
    we = dlg.period["weekend"]
    we["mode"].setCurrentIndex(we["mode"].findData("own"))
    assert we["slot"].isEnabled() and not ev["slot"].isEnabled()
    we["slot"].setMinutes(720)
    dlg._save()
    row = lab["conn"].execute("SELECT * FROM instruments WHERE id=?", (dlg.current,)).fetchone()
    assert (row["open_time"], row["close_time"], row["slot_minutes"]) == ("08:00", "17:00", 270)
    assert (row["evening_mode"], row["evening_start"], row["evening_end"]) == \
        ("block", "17:00", "08:00")
    assert (row["weekend_mode"], row["weekend_slot"], row["weekends"]) == ("own", 720, 1)


def test_drag_in_evening_takes_the_whole_block(qtbot, lab):
    conn = lab["conn"]
    conn.execute("UPDATE instruments SET open_time='08:00', close_time='17:00', "
                 "evening_mode='block', evening_start='17:00', evening_end='08:00' WHERE id=?",
                 (lab["xps"],))
    w = window(qtbot, lab)
    d = weekday()
    w.anchor = d
    w.tabs.setCurrentIndex(2)
    w.set_mode("week")
    view = w.tabs.currentWidget().view
    s0, e0 = view._snap_create(d.weekday(), at(d, 19), None)
    assert (s0, e0) == (at(d, 17), at(d, 8) + timedelta(days=1))


def test_nobody_logged_in_sees_the_schedule_and_a_login_button(qtbot, lab):
    d = weekday()
    logic.book(lab["conn"], lab["xps"], lab["alice"]["id"], at(d, 9), at(d, 11), "secret")
    st = QSettings(str(lab["dir"] / "g.ini"), QSettings.Format.IniFormat)
    w = MainWindow(lab["conn"], None, lab["dir"], st)
    qtbot.addWidget(w)
    w.show()
    assert w.guest and w.login_button.text() == "Log in" and w.requests is None
    w.anchor = d
    w.tabs.setCurrentIndex(2)
    w.set_mode("week")
    (item,) = items(w)
    assert not item.editable and "secret" not in item.toolTip()
    assert "log in to see your price" in w.tabs.currentWidget().info.text()
    # trying to book asks to log in first, then carries on in the new window
    asked = []
    w.login = lambda then=None: asked.append(then)
    w.create_booking(lab["xps"], at(d, 13), at(d, 14))
    assert asked and callable(asked[0])


def test_log_out_and_view_carries_over(qtbot, lab):
    w = window(qtbot, lab)
    assert w.logout_button.text() == "Log out"
    w.anchor = weekday(9)
    w.tabs.setCurrentIndex(2)
    w.set_mode("day")
    got = []
    w.switchUser.connect(lambda u, then: got.append(u))
    w.logout()
    assert got == [None]
    st = QSettings(str(lab["dir"] / "g.ini"), QSettings.Format.IniFormat)
    g = MainWindow(lab["conn"], None, lab["dir"], st)
    qtbot.addWidget(g)
    g.restore_view(w.view_state())
    assert g.anchor == weekday(9) and g.mode == "day"
    assert g.tabs.currentWidget().instrument_id == lab["xps"]


def test_disabled_while_logged_in_is_logged_out(qtbot, lab):
    w = window(qtbot, lab)
    got = []
    w.switchUser.connect(lambda u, then: got.append(u))
    lab["conn"].execute("UPDATE users SET status='disabled' WHERE id=?", (lab["alice"]["id"],))
    w.refresh()
    assert got == [None]


def _press_point(w, d, minutes):
    view = w.tabs.currentWidget().view
    view.verticalScrollBar().setValue(int(8 * 60 * PPM))
    p = view.mapFromScene(GUTTER + d.weekday() * view.colw + view.colw / 2,
                          HEADER + minutes * PPM + 2)
    return view, QPoint(p.x(), p.y())


def _xps_week(qtbot, lab):
    w = window(qtbot, lab)
    d = weekday()
    w.anchor = d
    w.tabs.setCurrentIndex(2)
    w.set_mode("week")
    return w, d


def test_a_plain_click_does_not_book(qtbot, lab):
    w, d = _xps_week(qtbot, lab)
    view, pt = _press_point(w, d, 9 * 60)
    got, hints = [], []
    view.createRequested.disconnect()
    view.createRequested.connect(lambda *a: got.append(a))
    view.hint.connect(hints.append)
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=pt)
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=pt, delay=50)
    assert got == [] and hints and "press and hold" in hints[0]


def test_press_and_hold_books_the_slot(qtbot, lab):
    w, d = _xps_week(qtbot, lab)
    view, pt = _press_point(w, d, 9 * 60)
    got = []
    view.createRequested.disconnect()
    view.createRequested.connect(lambda *a: got.append(a))
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=pt)
    qtbot.wait(view.HOLD_MS + 150)
    assert view._ghost is not None                     # armed: the slot lights up
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=pt)
    assert got == [(lab["xps"], at(d, 9), at(d, 9) + timedelta(minutes=30))]


def test_jitter_on_a_booking_opens_it_instead_of_moving_it(qtbot, lab):
    d = weekday()
    res = logic.book(lab["conn"], lab["xps"], lab["alice"]["id"], at(d, 9), at(d, 11))
    w, _ = _xps_week(qtbot, lab)
    view, pt = _press_point(w, d, 10 * 60)
    opened, moved = [], []
    view.openRequested.disconnect()
    view.openRequested.connect(opened.append)
    view.moveRequested.connect(lambda *a: moved.append(a))
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QMouseEvent
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=pt)
    jitter = QPointF(pt.x() + 1, pt.y() + 1)
    view.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, jitter, jitter, Qt.MouseButton.NoButton,
                                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=pt)
    assert opened == [res.id] and moved == []


def test_editor_whole_weekend_with_a_48_hour_slot(qtbot, lab):
    from PyQt6.QtCore import QTime
    dlg = InstrumentsDialog(lab["conn"])
    qtbot.addWidget(dlg)
    we = dlg.period["weekend"]
    we["mode"].setCurrentIndex(we["mode"].findData("own"))
    dlg.weekend_span.setCurrentIndex(dlg.weekend_span.findData("whole"))
    we["start"].setTime(QTime(8, 0))
    we["end"].setTime(QTime(8, 0))
    assert "= 48 h" in dlg.weekend_span_note.text()
    we["slot"].setMinutes(48 * 60)
    assert we["slot"].minutes() == 48 * 60                 # beyond the old 24 h limit
    dlg._save()
    row = lab["conn"].execute("SELECT weekend_span, weekend_slot FROM instruments WHERE id=?",
                              (dlg.current,)).fetchone()
    assert tuple(row) == ("whole", 48 * 60)


def test_empty_calendar_shows_grey_slots_and_issue_colours(qtbot, lab):
    from PyQt6.QtWidgets import QGraphicsRectItem
    conn = lab["conn"]
    conn.execute("UPDATE instruments SET open_time='08:00', close_time='17:00', slot_minutes=270, "
                 "evening_mode='closed', weekend_mode='closed' WHERE id=?", (lab["xps"],))
    d = weekday()
    logic.report_issue(conn, lab["xps"], "down", at(d, 13), None, "leak", lab["alice"]["id"])
    w = window(qtbot, lab)
    w.anchor = d
    w.tabs.setCurrentIndex(2)
    w.set_mode("day")
    view = w.tabs.currentWidget().view
    pal = view.palette_cfg
    boxes = [i for i in view.scene().items() if isinstance(i, QGraphicsRectItem)
             and not isinstance(i, BookingItem) and i.zValue() == -0.9]
    fills = sorted(i.brush().color().name() for i in boxes)
    assert len(boxes) == 2                                   # 08:00-12:30 and 12:30-17:00
    assert fills == sorted([pal["free"], pal["down"]])
    # the status line reports what is in effect now
    now = logic.now_local(conn)
    logic.report_issue(conn, lab["nap"], "down", now - timedelta(hours=1), None, "leak", None)
    w.tabs.setCurrentIndex(1)
    assert "OUT OF ORDER" in w.tabs.currentWidget().info.text()


def test_report_problem_dialog(qtbot, lab):
    from khervelab.gui.dialogs import IssueDialog
    dlg = IssueDialog(lab["conn"], lab["alice"], lab["xps"])
    qtbot.addWidget(dlg)
    dlg.kind.setCurrentIndex(dlg.kind.findData("problem"))
    dlg.note.setPlainText("charge neutraliser unstable")
    dlg._ok()
    row = lab["conn"].execute("SELECT * FROM issues").fetchone()
    assert row["kind"] == "problem" and row["end"] is None and row["reported_by"] == \
        lab["alice"]["id"]
    from khervelab.gui.admin import IssuesDialog
    mgr = IssuesDialog(lab["conn"])
    qtbot.addWidget(mgr)
    mgr.table.selectRow(0)
    mgr._resolve()
    assert lab["conn"].execute("SELECT end FROM issues").fetchone()[0] is not None


def test_remove_instrument_from_the_editor(qtbot, lab):
    from khervelab.gui.admin import InstrumentsDialog
    conn = lab["conn"]
    dlg = InstrumentsDialog(conn)
    qtbot.addWidget(dlg)
    dlg._add()                                       # a new, unused instrument
    new = dlg.current
    dlg._remove()                                    # the question is answered Yes
    assert conn.execute("SELECT COUNT(*) FROM instruments WHERE id=?", (new,)).fetchone()[0] == 0
    assert dlg.list.count() == 2


def test_superuser_books_for_someone_in_the_app(qtbot, lab):
    conn = lab["conn"]
    sid = logic.create_user(conn, "sue", "suepass12", "Sue Super", role="superuser",
                            status="active")
    sue = conn.execute("SELECT * FROM users WHERE id=?", (sid,)).fetchone()
    d = weekday()
    dlg = BookingDialog(conn, sue, lab["xps"], at(d, 9), at(d, 11))
    qtbot.addWidget(dlg)
    assert dlg.user.isVisibleTo(dlg)                              # the "For" list
    dlg.user.setCurrentIndex(dlg.user.findData(lab["alice"]["id"]))
    dlg._ok()
    b = conn.execute("SELECT * FROM bookings").fetchone()
    assert b["user_id"] == lab["alice"]["id"] and b["status"] == "approved"   # alice is trained
    # reassign from the booking details
    det = BookingDetailsDialog(conn, sue, b["id"])
    qtbot.addWidget(det)
    assert det.owner is not None
    det.owner.setCurrentIndex(det.owner.findData(sid))
    det._save()
    assert conn.execute("SELECT user_id FROM bookings").fetchone()[0] == sid
    # sue sees someone's bookings through the toolbar action
    st = QSettings(str(lab["dir"] / "su.ini"), QSettings.Format.IniFormat)
    w = MainWindow(conn, sue, lab["dir"], st)
    qtbot.addWidget(w)
    from khervelab.gui.panels import MyBookingsDialog
    other = MyBookingsDialog(conn, sue, owner=lab["alice"])
    qtbot.addWidget(other)
    assert other.windowTitle() == "Bookings of Alice Martin"
    assert w.requests is None                                     # no manager tools


def test_users_editor_offers_the_superuser_role(qtbot, lab):
    dlg = UsersDialog(lab["conn"], lab["boss"])
    qtbot.addWidget(dlg)
    assert dlg.role.findData("superuser") >= 0
