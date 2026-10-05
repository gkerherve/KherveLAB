from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("pytestqt")

from PyQt6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QLabel  # noqa: E402

from khervelab.core.facility import FacilityService  # noqa: E402
from khervelab.core.models import Booking, User  # noqa: E402
from khervelab.ui.booking_dialog import BookingDialog  # noqa: E402
from khervelab.ui.calendar_view import (DAY, GUTTER, HEADER, MONTH, PPM, WEEK,  # noqa: E402
                                        BookingItem)
from khervelab.ui.main_window import MainWindow  # noqa: E402


def next_weekday(offset=2) -> date:
    d = date.today() + timedelta(days=offset)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


@pytest.fixture
def window(qtbot, facility, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("visible_instruments", ["xps", "sem-sigma-300"])
    settings.setValue("current_instrument", "xps")
    from cryptography.fernet import Fernet
    from khervelab.local.store import LocalStore
    store = LocalStore(tmp_path / "local", Fernet.generate_key())
    from khervelab.local.dataindex import DataIndex
    w = MainWindow(facility, settings, store, index=DataIndex(tmp_path / "index.db"))
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


def add(w: MainWindow, d: date, h1=10, h2=12, inst="xps") -> Booking:
    tz = w.svc.tz
    b = Booking(inst, datetime(d.year, d.month, d.day, h1, tzinfo=tz),
                datetime(d.year, d.month, d.day, h2, tzinfo=tz), "u-0001")
    return w.svc.add_booking(b)


def booking_items(w):
    return [i for i in w.calendar.scene().items() if isinstance(i, BookingItem)]


def test_sidebar_lists_every_instrument(window):
    n = sum(window.tree.topLevelItem(i).childCount() for i in range(window.tree.topLevelItemCount()))
    assert n == len(window.svc.instruments) >= 45
    assert "High-throughput XPS" in window.details.toPlainText()


def test_week_view_shows_booking(window):
    d = next_weekday()
    add(window, d)
    window.anchor = d
    window.refresh()
    items = booking_items(window)
    assert len(items) == 1
    seg = items[0].seg
    assert window.calendar.columns[seg.col].day == d
    assert (seg.m0, seg.m1) == (600, 720)


def test_overlapping_instruments_get_lanes(window):
    d = next_weekday()
    add(window, d, inst="xps")
    add(window, d, inst="sem-sigma-300")
    window.anchor = d
    window.refresh()
    lanes = sorted((i.seg.lane, i.seg.lanes) for i in booking_items(window))
    assert lanes == [(0, 2), (1, 2)]


def test_day_view_has_column_per_visible_instrument(window):
    window._set_mode(DAY)
    assert [c.instrument for c in window.calendar.columns] == sorted(
        ["xps", "sem-sigma-300"],
        key=lambda i: (window.svc.instruments[i].facility, window.svc.instruments[i].name))


def test_month_view_builds(window):
    d = next_weekday()
    add(window, d)
    window.anchor = d
    window._set_mode(MONTH)
    assert window.calendar._month_cells
    window._set_mode(WEEK)


def test_drag_creates_request(window, qtbot):
    d = next_weekday()
    window.anchor = d
    window.refresh()
    cal = window.calendar
    col = d.weekday()
    cal.verticalScrollBar().setValue(int(8 * 60 * PPM))
    def vp(minutes):
        scene_pt = (GUTTER + col * cal.colw + cal.colw / 2, HEADER + minutes * PPM + 2)
        p = cal.mapFromScene(*scene_pt)
        return QPoint(p.x(), p.y())
    got = []
    cal.createRequested.disconnect()  # the window would open a modal dialog
    cal.createRequested.connect(lambda i, s, e: got.append((i, s, e)))
    qtbot.mousePress(cal.viewport(), Qt.MouseButton.LeftButton, pos=vp(9 * 60))
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QPointF, QEvent
    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(vp(11 * 60)), QPointF(vp(11 * 60)),
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    cal.mouseMoveEvent(ev)
    qtbot.mouseRelease(cal.viewport(), Qt.MouseButton.LeftButton, pos=vp(11 * 60))
    assert len(got) == 1
    iid, s, e = got[0]
    assert iid == "xps"
    assert s.date() == d and s.hour == 9
    assert e.hour == 11


def test_dialog_blocks_overlap(window, qtbot):
    d = next_weekday()
    existing = add(window, d, 10, 12)
    tz = window.svc.tz
    draft = Booking("xps", datetime(d.year, d.month, d.day, 11, tzinfo=tz),
                    datetime(d.year, d.month, d.day, 13, tzinfo=tz), "u-0001")
    dlg = BookingDialog(window.svc, draft)
    qtbot.addWidget(dlg)
    from PyQt6.QtWidgets import QDialogButtonBox
    assert not dlg.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert "clashes" in dlg.info.text()
    edit = BookingDialog(window.svc, existing, editing=existing)
    qtbot.addWidget(edit)
    assert edit.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled(), edit.info.text()


def test_move_writes_one_commit(window):
    d = next_weekday()
    b = add(window, d, 10, 12)
    n = len(list(window.repo.git.iter_commits()))
    window._move(b, b.start + timedelta(hours=2), b.end + timedelta(hours=2), "xps")
    assert len(list(window.repo.git.iter_commits())) == n + 1
    (moved,) = window.svc.bookings()
    assert moved.start.hour == 12


def test_requests_panel_lists_and_approves(window, qtbot, monkeypatch):
    from khervelab.core.requests import RequestQueue
    from tests.test_requests import FakeClient, issue
    from khervelab.core.models import User
    window.svc.save_user(User("u-0002", "Student", github="phd-student", permissions=("xps",)))
    client = FakeClient([issue(11)])
    panel = window.requests
    panel.queue = RequestQueue(window.svc, client)
    panel.queue.poll()
    panel._polled(panel.queue.items)
    assert panel.table.rowCount() == 1
    assert "OK" in panel.table.item(0, 4).text()
    assert window.a_requests.text() == "Requests (1)"
    panel.table.selectRow(0)
    panel.approve()
    assert client.closed == [(11, "completed")]
    assert len(window.svc.bookings()) == 1


def test_real_names_only_on_this_machine(window, qtbot):
    from khervelab.local.store import Training
    from khervelab.local.training import new_person, sync_permissions
    from khervelab.ui.people import ExpiryDialog, PeopleDialog
    p = new_person(window.svc, window.store, "Marie Curie", "MC group")
    window.store.save_training(Training(None, p.id, "xps", "trained", date.today(),
                                        expiry=date.today() + timedelta(days=30)))
    sync_permissions(window.svc, window.store, p)
    d = next_weekday()
    tz = window.svc.tz
    window.svc.add_booking(Booking("xps", datetime(d.year, d.month, d.day, 9, tzinfo=tz),
                                   datetime(d.year, d.month, d.day, 11, tzinfo=tz), p.user_id))
    window.anchor = d
    window.refresh()
    (item,) = booking_items(window)
    assert "Marie Curie" in item.toolTip()
    published = window.repo.path / "users" / f"{p.user_id}.yaml"
    assert "Curie" not in published.read_text()
    dlg = PeopleDialog(window.svc, window.store)
    qtbot.addWidget(dlg)
    assert dlg.list.count() == 1 and dlg.table.rowCount() == 1
    exp = ExpiryDialog(window.svc, window.store)
    qtbot.addWidget(exp)
    assert len(exp.lapses) == 1


def test_fresh_install_without_local_db_shows_display_strings(window):
    window.svc.save_user(User("u-0002", "AB group"))
    d = next_weekday()
    tz = window.svc.tz
    window.svc.add_booking(Booking("xps", datetime(d.year, d.month, d.day, 9, tzinfo=tz),
                                   datetime(d.year, d.month, d.day, 11, tzinfo=tz), "u-0002"),
                           override=True)
    window.store = None
    window.anchor = d
    window.refresh()
    (item,) = booking_items(window)
    assert "AB group" in item.toolTip()


def test_quick_log_fault_shows_on_dashboard(window, qtbot):
    from khervelab.ui.logbook_ui import InstrumentCard, QuickLogDialog
    dlg = QuickLogDialog(window.svc, "xps", "u-0001", kind="fault")
    qtbot.addWidget(dlg)
    dlg.text.setText("ion gun replacement")
    dlg.blocking.setChecked(True)
    dlg._ok()
    assert dlg.entry is not None and dlg.entry.blocking
    window.dashboard_dock.show()
    window.refresh()
    cards = window.dashboard.findChildren(InstrumentCard)
    assert cards
    text = " ".join(l.text() for c in cards for l in c.findChildren(QLabel))
    assert "down, ion gun replacement" in text
    assert "down, ion gun replacement" in window.details.toPlainText()


def test_data_panel_and_sample_page(window, qtbot, tmp_path):
    import shutil
    from pathlib import Path
    from khervelab.ui.samples_ui import SamplePage, SamplesBrowser
    s = window.svc.add_sample("Pt foil", composition="Pt")
    folder = tmp_path / "data"
    folder.mkdir()
    shutil.copy(Path(__file__).parent / "data" / "Pt4f.vms", folder / f"{s.id}_Pt4f.vms")
    window.index.add_folder(folder, "xps")
    window.index.scan(window.svc.tz)
    window.index.match(window.svc)
    window.data.query.setText("Pt 4f, 20 eV pass energy")
    window.data.search()
    assert window.data.table.rowCount() == 1
    assert window.data.table.item(0, 4).text() == s.id
    opened = []
    window.data.open_files = opened.append
    window.data.table.selectRow(0)
    window.data._open()
    assert opened and opened[0][0].name == f"{s.id}_Pt4f.vms"
    page = SamplePage(window.svc, s.id, window.index, opened.append)
    qtbot.addWidget(page)
    assert page.files.rowCount() == 1 and "Pt foil" in page.view.toPlainText()
    browser = SamplesBrowser(window.svc, window.index)
    qtbot.addWidget(browser)
    assert browser.table.rowCount() == 1


def test_report_dialog_previews(window, qtbot):
    from khervelab.ui.reports_ui import ReportDialog
    d = next_weekday()
    add(window, d)
    dlg = ReportDialog(window.svc, window.store, ["xps"])
    qtbot.addWidget(dlg)
    dlg.preset.setCurrentIndex(2)  # this calendar year to date
    dlg.end.setDate(dlg.end.date().addDays(30))
    assert dlg.report is not None and dlg.table.rowCount() >= 1
    assert "booked" in dlg.summary.text()
