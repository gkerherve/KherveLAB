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
