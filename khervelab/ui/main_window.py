"""Main window: instrument sidebar, calendar, sync status.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import html
from dataclasses import replace
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QApplication, QDialog, QDockWidget, QLabel, QLineEdit, QMainWindow,
                             QMessageBox,
                             QSplitter, QTextBrowser, QToolBar, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from .. import __app_name__, __version__
from ..core.config import format_range
from ..core.facility import BookingRejected, FacilityService
from ..core.models import DAYS, Booking
from ..core.repo import ConflictError, FacilityRepo, OfflineError, PersonalDataError, RepoError
from ..core.yamlio import ConfigError
from . import theme
from .booking_dialog import BookingDialog
from .calendar_view import DAY, MONTH, WEEK, CalendarView
from .dialogs import ClashDialog, ConflictDialog, SetupDialog
from .requests_panel import RequestsPanel
from . import people as people_ui

SYNC_INTERVAL_MS = 5 * 60 * 1000
PUSH_DEBOUNCE_MS = 3000


class SyncWorker(QThread):
    done = pyqtSignal(object)        # RepoStatus
    conflict = pyqtSignal(object)    # list[FileConflict]
    failed = pyqtSignal(str)

    def __init__(self, repo: FacilityRepo, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.offline = False

    def run(self):
        try:
            try:
                self.repo.pull()
                self.offline = not self.repo.push() and self.repo.has_remote
            except OfflineError:
                self.offline = True
            self.done.emit(self.repo.status())
        except ConflictError as exc:
            self.conflict.emit(exc.conflicts)
        except RepoError as exc:
            self.failed.emit(str(exc))


def _swatch(colour: str) -> QIcon:
    pm = QPixmap(12, 12)
    pm.fill(QColor(colour))
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self, repo: FacilityRepo, settings: QSettings, store=None, store_error: str = ""):
        super().__init__()
        self.settings = settings
        self.store = store
        self.store_error = store_error
        self.repo = repo
        self.svc = FacilityService(repo)
        self.dark = settings.value("dark", False, type=bool)
        self.colours = theme.apply(QApplication.instance(), self.dark)
        self.anchor = date.today()
        self.mode = settings.value("view", WEEK) or WEEK
        self.current: str | None = settings.value("current_instrument", None)
        checked = settings.value("visible_instruments", None)
        if isinstance(checked, str):
            checked = [checked]
        self.visible: set[str] = set(checked) if checked else {"xps"} & set(self.svc.instruments)
        if not self.visible and self.svc.instruments:
            self.visible = {next(iter(self.svc.instruments))}
        if self.current not in self.svc.instruments:
            self.current = next(iter(sorted(self.visible)), None)
        self.worker: SyncWorker | None = None
        self.offline = False

        self.setWindowTitle(f"{__app_name__} v{__version__} — {self.svc.cfg.facility.name}")
        self.resize(1400, 900)
        self._build_ui()
        self._build_actions()
        self._populate_sidebar()
        self.refresh()

        self.sync_timer = QTimer(self, interval=SYNC_INTERVAL_MS, timeout=self.sync)
        self.sync_timer.start()
        self.push_timer = QTimer(self, singleShot=True, interval=PUSH_DEBOUNCE_MS,
                                 timeout=self._publish_and_sync)
        self.minute_timer = QTimer(self, interval=60_000, timeout=self.calendar.rebuild)
        self.minute_timer.start()
        QTimer.singleShot(200, self.sync)

    # -- layout -----------------------------------------------------------
    def _build_ui(self):
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter instruments, techniques…")
        self.search.textChanged.connect(self._filter_sidebar)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self._tree_changed)
        self.tree.currentItemChanged.connect(self._tree_current)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(6, 6, 0, 6)
        sl.addWidget(self.search)
        split_side = QSplitter(Qt.Orientation.Vertical)
        split_side.addWidget(self.tree)
        split_side.addWidget(self.details)
        split_side.setSizes([600, 220])
        sl.addWidget(split_side)

        self.calendar = CalendarView()
        self.calendar.createRequested.connect(self._create)
        self.calendar.moveRequested.connect(self._move)
        self.calendar.editRequested.connect(self._edit)
        self.calendar.deleteRequested.connect(self._delete)
        self.calendar.dayActivated.connect(self._goto_day)

        split = QSplitter()
        split.addWidget(side)
        split.addWidget(self.calendar)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 1080])
        self.setCentralWidget(split)

        self.sync_label = QLabel()
        self.statusBar().addPermanentWidget(self.sync_label)

        self.requests = RequestsPanel(self.svc)
        self.requests.bookingsChanged.connect(self._after_change)
        self.requests.countChanged.connect(self._requests_count)
        self.requests_dock = QDockWidget("Booking requests", self)
        self.requests_dock.setObjectName("requests")
        self.requests_dock.setWidget(self.requests)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.requests_dock)
        self.requests_dock.hide()

    def _build_actions(self):
        tb = QToolBar("Navigation")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, shortcut=None, tip=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
            return a

        self.a_prev = act("◀", lambda: self._step(-1), "Ctrl+Left", "Previous (←)")
        self.a_today = act("Today", self._today, "Ctrl+T", "Today (T)")
        self.a_next = act("▶", lambda: self._step(1), "Ctrl+Right", "Next (→)")
        tb.addAction(self.a_prev)
        tb.addAction(self.a_today)
        tb.addAction(self.a_next)
        self.range_label = QLabel()
        self.range_label.setContentsMargins(12, 0, 12, 0)
        f = self.range_label.font()
        f.setPointSizeF(f.pointSizeF() + 2)
        f.setBold(True)
        self.range_label.setFont(f)
        tb.addWidget(self.range_label)
        tb.addSeparator()

        self.view_group = QActionGroup(self)
        self.view_actions = {}
        for key, (label, sc) in {WEEK: ("Week", "Ctrl+1"), DAY: ("Day", "Ctrl+2"),
                                 MONTH: ("Month", "Ctrl+3")}.items():
            a = act(label, lambda _=False, k=key: self._set_mode(k), sc)
            a.setCheckable(True)
            a.setChecked(key == self.mode)
            self.view_group.addAction(a)
            tb.addAction(a)
            self.view_actions[key] = a
        tb.addSeparator()
        tb.addAction(act("New booking", self._new_booking, "Ctrl+N"))
        tb.addAction(act("Sync now", self.sync, "Ctrl+R", "Pull and push the facility repository"))
        tb.addAction(act("Publish now", self.publish_now, "Ctrl+Shift+P",
                         "Regenerate the public calendar in docs/ and push it"))
        tb.addSeparator()
        self.a_requests = self.requests_dock.toggleViewAction()
        self.a_requests.setText("Requests")
        self.a_requests.setShortcut(QKeySequence("Ctrl+Shift+R"))
        tb.addAction(self.a_requests)

        # bare keys act only while the calendar has focus, so the sidebar
        # keeps its own arrow-key navigation
        for key, slot in (("Left", lambda: self._step(-1)), ("Right", lambda: self._step(1)),
                          ("T", self._today), ("1", lambda: self._set_mode(WEEK)),
                          ("2", lambda: self._set_mode(DAY)), ("3", lambda: self._set_mode(MONTH))):
            s = QShortcut(QKeySequence(key), self.calendar)
            s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            s.activated.connect(slot)

        mb = self.menuBar()
        fm = mb.addMenu("&File")
        fm.addAction(act("New / open facility…", self._switch_facility))
        fm.addAction(act("Reload from disk", self._reload))
        fm.addAction(act("GitHub token…", lambda: self.requests.edit_token()))
        fm.addSeparator()
        fm.addAction(act("Quit", self.close, "Ctrl+Q"))
        vm = mb.addMenu("&View")
        for a in self.view_actions.values():
            vm.addAction(a)
        vm.addSeparator()
        dark = act("Dark theme", self._toggle_dark)
        dark.setCheckable(True)
        dark.setChecked(self.dark)
        vm.addAction(dark)
        pm = mb.addMenu("&People")
        pm.addAction(act("People and training…", self._people, "Ctrl+Shift+U"))
        pm.addAction(act("Training expiring soon…", self._expiry))
        pm.addSeparator()
        pm.addAction(act("Back up training records…", self._backup))
        pm.addAction(act("Restore training records…", self._restore))
        hm = mb.addMenu("&Help")
        hm.addAction(act("About KherveLAB", self._about))

    # -- sidebar ----------------------------------------------------------
    def _populate_sidebar(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for inst in sorted(self.svc.instruments.values(), key=lambda i: (i.facility, i.name)):
            fac = inst.facility or "Other"
            parent = groups.get(fac)
            if parent is None:
                parent = QTreeWidgetItem(self.tree, [fac])
                parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable
                                | Qt.ItemFlag.ItemIsAutoTristate)
                f = parent.font(0)
                f.setBold(True)
                parent.setFont(0, f)
                groups[fac] = parent
            item = QTreeWidgetItem(parent, [inst.name])
            item.setData(0, Qt.ItemDataRole.UserRole, inst.id)
            item.setIcon(0, _swatch(inst.colour))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if inst.id in self.visible
                               else Qt.CheckState.Unchecked)
            item.setToolTip(0, f"{inst.make_model or inst.name}\n{', '.join(inst.techniques)}")
            if inst.id == self.current:
                self.tree.setCurrentItem(item)
        for g in groups.values():
            g.setExpanded(any(g.child(i).checkState(0) == Qt.CheckState.Checked
                              for i in range(g.childCount())))
        self.tree.blockSignals(False)
        self._show_details()

    def _filter_sidebar(self, text: str):
        t = text.lower().strip()
        for gi in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(gi)
            any_shown = False
            for ci in range(g.childCount()):
                item = g.child(ci)
                inst = self.svc.instruments[item.data(0, Qt.ItemDataRole.UserRole)]
                hay = " ".join([inst.name, inst.id, inst.category, inst.facility, inst.make_model,
                                *inst.techniques]).lower()
                shown = not t or t in hay
                item.setHidden(not shown)
                any_shown |= shown
            g.setHidden(not any_shown)
            if t and any_shown:
                g.setExpanded(True)

    def _tree_changed(self, item: QTreeWidgetItem, _col: int):
        visible = set()
        for gi in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(gi)
            for ci in range(g.childCount()):
                c = g.child(ci)
                if c.checkState(0) == Qt.CheckState.Checked:
                    visible.add(c.data(0, Qt.ItemDataRole.UserRole))
        self.visible = visible
        self.settings.setValue("visible_instruments", sorted(visible))
        self.refresh()

    def _tree_current(self, item: QTreeWidgetItem | None, _prev):
        if item is None:
            return
        iid = item.data(0, Qt.ItemDataRole.UserRole)
        if iid:
            self.current = iid
            self.settings.setValue("current_instrument", iid)
            self._show_details()
            self.refresh()

    def _show_details(self):
        inst = self.svc.instruments.get(self.current or "")
        if inst is None:
            self.details.setHtml("<p>Select an instrument.</p>")
            return
        e = html.escape
        hours = []
        for d in DAYS:
            rs = inst.bookable_hours.get(d)
            if rs:
                hours.append(f"{d.title()} {', '.join(format_range(r) for r in rs)}")
        cons = "".join(f"<li>{e(c.name)}: limit {c.limit_hours:g} h (warn {c.warn_at:g} h)</li>"
                       for c in inst.consumables)
        maint = "".join(f"<li>{e(m.task)}: every {m.interval_days} d</li>" for m in inst.maintenance)
        source = ("listed by the Department of Materials" if inst.source == "materials-website"
                  else "added to the catalogue")
        self.details.setHtml(
            f"<h3 style='margin:0'><span style='color:{inst.colour}'>■</span> {e(inst.name)}</h3>"
            f"<p style='color:#57606a;margin:2px 0'>{e(inst.make_model or '')}</p>"
            f"<p>{e(inst.description)}</p>"
            f"<p><b>Facility:</b> {e(inst.facility)}<br><b>Category:</b> {e(inst.category)}<br>"
            f"<b>Techniques:</b> {e(', '.join(inst.techniques))}</p>"
            f"<p><b>Booking rules</b><br>Slots of {inst.slot_granularity_minutes} min; "
            f"{inst.min_booking_minutes}–{inst.max_booking_minutes} min per booking; "
            f"up to {inst.max_advance_days} days ahead; {inst.max_concurrent_per_user} upcoming per user"
            f"{'; training required' if inst.requires_permission else ''}.<br>"
            f"{'<br>'.join(hours) or 'Any time'}</p>"
            + (f"<p><b>Consumables</b></p><ul>{cons}</ul>" if cons else "")
            + (f"<p><b>Maintenance</b></p><ul>{maint}</ul>" if maint else "")
            + f"<p style='color:#8c959f'>Instrument file: instruments/{inst.id}.yaml ({source})</p>")

    # -- calendar ---------------------------------------------------------
    def refresh(self):
        order = sorted(self.visible & set(self.svc.instruments),
                       key=lambda i: (self.svc.instruments[i].facility, self.svc.instruments[i].name))
        insts = [self.svc.instruments[i] for i in order]
        self.calendar.names = self.store.names() if self.store else {}
        self.calendar.set_state(self.svc, insts, self.current, self.anchor, self.mode, self.colours)
        first, last = self.calendar.visible_range()
        if self.mode == DAY:
            text = f"{first:%A %d %B %Y}"
        elif self.mode == WEEK:
            text = f"{first:%d %b} – {last:%d %b %Y}"
            if self.current and self.current in self.svc.instruments:
                text += f"   ·   new bookings: {self.svc.instruments[self.current].name}"
        else:
            text = f"{first:%B %Y}"
        self.range_label.setText(text)
        self._update_status()

    def _step(self, direction: int):
        if self.mode == DAY:
            self.anchor += timedelta(days=direction)
        elif self.mode == WEEK:
            self.anchor += timedelta(days=7 * direction)
        else:
            m = self.anchor.month - 1 + direction
            self.anchor = date(self.anchor.year + m // 12, m % 12 + 1, 1)
        self.refresh()

    def _today(self):
        self.anchor = date.today()
        self.refresh()

    def _set_mode(self, mode: str):
        self.mode = mode
        self.view_actions[mode].setChecked(True)
        self.settings.setValue("view", mode)
        self.refresh()

    def _goto_day(self, d: date):
        self.anchor = d
        self._set_mode(DAY)

    # -- bookings ---------------------------------------------------------
    def _me(self) -> str:
        uid = self.settings.value("user_id", "")
        if uid in self.svc.users:
            return uid
        managers = [u.id for u in self.svc.users.values() if u.manager]
        return managers[0] if managers else (next(iter(self.svc.users), "") or "")

    def _new_booking(self):
        iid = self.current or next(iter(sorted(self.visible)), None)
        if iid is None:
            return
        tz = self.svc.tz
        now = datetime.now(tz)
        start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if self.anchor != now.date():
            start = datetime(self.anchor.year, self.anchor.month, self.anchor.day, 9, tzinfo=tz)
        inst = self.svc.instruments[iid]
        self._create(iid, start, start + timedelta(minutes=max(60, inst.min_booking_minutes)))

    def _create(self, iid: str, start: datetime, end: datetime):
        inst = self.svc.instruments[iid]
        if (end - start) < timedelta(minutes=inst.min_booking_minutes):
            end = start + timedelta(minutes=inst.min_booking_minutes)
        draft = Booking(iid, start, end, self._me())
        dlg = BookingDialog(self.svc, draft, parent=self, names=self.calendar.names)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_booking is None:
            return
        b = dlg.result_booking
        self.settings.setValue("user_id", b.user)
        self._mutate(lambda: (self.svc.add_recurring(b, dlg.repeat_every, dlg.repeat_count.value(),
                                                     override=dlg.wants_override)
                              if dlg.repeat_every else
                              self.svc.add_booking(b, override=dlg.wants_override)))

    def _edit(self, b: Booking):
        dlg = BookingDialog(self.svc, b, editing=b, parent=self, names=self.calendar.names)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_booking is None:
            return
        new = dlg.result_booking
        if new == b:
            return
        self._mutate(lambda: self.svc.update_booking(b, new, override=dlg.wants_override))

    def _move(self, b: Booking, start: datetime, end: datetime, iid: str):
        new = replace(b, start=start, end=end, instrument=iid)
        try:
            self.svc.update_booking(b, new)
        except BookingRejected as exc:
            if any(v.blocking for v in exc.violations):
                self._explain(exc)
                self.refresh()
                return
            if not self._confirm_override(exc):
                self.refresh()
                return
            self._mutate(lambda: self.svc.update_booking(b, new, override=True))
            return
        except (RepoError, ConfigError) as exc:
            QMessageBox.warning(self, "KherveLAB", str(exc))
        self._after_change()

    def _delete(self, b: Booking):
        inst = self.svc.instruments.get(b.instrument)
        s = b.start.astimezone(self.svc.tz)
        if QMessageBox.question(
                self, "Delete booking",
                f"Delete the booking on {inst.name if inst else b.instrument} "
                f"at {s:%a %d %b %H:%M}?\nThe deletion is recorded in the history.") \
                != QMessageBox.StandardButton.Yes:
            return
        self._mutate(lambda: self.svc.delete_booking(b))

    def _mutate(self, fn):
        try:
            fn()
        except BookingRejected as exc:
            self._explain(exc)
        except PersonalDataError as exc:
            QMessageBox.warning(self, "Personal data", str(exc))
        except (RepoError, ConfigError, OSError) as exc:
            QMessageBox.warning(self, "KherveLAB", str(exc))
        self._after_change()

    def _explain(self, exc: BookingRejected):
        QMessageBox.warning(self, "Booking not saved",
                            "\n".join(f"• {v.message}" for v in exc.violations))

    def _confirm_override(self, exc: BookingRejected) -> bool:
        text = "\n".join(f"• {v.message}" for v in exc.violations)
        return QMessageBox.question(
            self, "Override?", f"{text}\n\nSave anyway? The override is recorded in the booking.") \
            == QMessageBox.StandardButton.Yes

    def _after_change(self):
        self.refresh()
        self.push_timer.start()

    def _requests_count(self, n: int):
        self.a_requests.setText(f"Requests ({n})" if n else "Requests")
        if n and not self.requests_dock.isVisible():
            self.statusBar().showMessage(f"{n} booking request(s) waiting", 8000)

    # -- publishing -------------------------------------------------------
    def _publish(self) -> bool:
        try:
            return self.svc.publish()
        except (RepoError, OSError) as exc:
            self.statusBar().showMessage(f"Publishing failed: {exc}", 10_000)
            return False

    def _publish_and_sync(self):
        self._publish()
        self.sync()

    def publish_now(self):
        changed = self._publish()
        self.statusBar().showMessage("Calendar published" if changed
                                     else "Published calendar already up to date", 5000)
        self.sync()

    # -- sync -------------------------------------------------------------
    def sync(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.repo.has_remote:
            self._update_status()
            return
        self.sync_label.setText("Syncing…")
        self.worker = SyncWorker(self.repo, self)
        self.worker.done.connect(self._sync_done)
        self.worker.conflict.connect(self._sync_conflict)
        self.worker.failed.connect(lambda msg: (self.statusBar().showMessage(msg, 10_000),
                                                self._update_status()))
        self.worker.start()

    def _sync_done(self, _status):
        self.offline = self.worker.offline if self.worker else False
        try:
            self.svc.reload()
        except ConfigError as exc:
            QMessageBox.warning(self, "Invalid file in repository", str(exc))
            return
        for a, b in self.svc.find_clashes():
            dlg = ClashDialog(self.svc, a, b, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.svc.delete_booking(dlg.loser)
                self.push_timer.start()
        self.refresh()

    def _sync_conflict(self, conflicts):
        dlg = ConflictDialog(self.svc, conflicts, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.svc.resolve_conflicts(dlg.choices)
            except (RepoError, ValueError, ConfigError) as exc:
                QMessageBox.warning(self, "KherveLAB", str(exc))
            self.push_timer.start()
        self.refresh()

    def _update_status(self):
        try:
            st = self.repo.status()
        except Exception:
            return
        parts = []
        if not st.has_remote:
            parts.append("Local only (no remote)")
        else:
            if self.offline:
                parts.append("Offline")
            last = self.repo.last_sync
            parts.append(f"Last sync {last.astimezone().strftime('%H:%M')}" if last
                         else "Not synced yet")
            if st.ahead:
                parts.append(f"{st.ahead} commit{'s' if st.ahead != 1 else ''} queued")
        parts.append(f"{len(self.svc.cfg.bookings)} bookings · {len(self.svc.instruments)} "
                     "instruments")
        self.sync_label.setText("  ·  ".join(parts))

    # -- people (local, encrypted) ----------------------------------------
    def _need_store(self) -> bool:
        if self.store is None:
            QMessageBox.warning(self, "Training records", "The local encrypted store is not "
                                f"available: {self.store_error or 'no keyring'}")
            return False
        return True

    def _people(self):
        if not self._need_store():
            return
        dlg = people_ui.PeopleDialog(self.svc, self.store, self)
        dlg.exec()
        if dlg.changed:
            self._after_change()

    def _expiry(self):
        if self._need_store():
            people_ui.ExpiryDialog(self.svc, self.store, self).exec()

    def _backup(self):
        if self._need_store():
            people_ui.backup_dialog(self, self.store)

    def _restore(self):
        if not self._need_store():
            return
        restored = people_ui.restore_dialog(self, self.store)
        if restored is not None:
            self.store = restored
            self.refresh()

    def startup_checks(self):
        """Revoke lapsed training and nag about backups. Called once shown."""
        if self.store is None:
            return
        from ..local.training import sync_all
        self.store.record_launch()
        try:
            if sync_all(self.svc, self.store):
                self._after_change()
        except (RepoError, ConfigError) as exc:
            self.statusBar().showMessage(f"Could not update permissions: {exc}", 10_000)
        why = self.store.backup_reminder()
        if why:
            box = QMessageBox(QMessageBox.Icon.Warning, "Back up training records",
                              f"{why}\n\nTraining records exist only on this computer. If it "
                              "is lost without a backup, they are gone.", parent=self)
            now = box.addButton("Back up now…", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is now:
                people_ui.backup_dialog(self, self.store)

    # -- misc -------------------------------------------------------------
    def _reload(self):
        try:
            self.svc.reload()
        except ConfigError as exc:
            QMessageBox.warning(self, "Invalid file", str(exc))
        self.requests.set_service(self.svc)
        self._populate_sidebar()
        self.refresh()

    def _switch_facility(self):
        dlg = SetupDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.repo is not None:
            self.settings.setValue("repo_path", str(dlg.repo.path))
            self.repo = dlg.repo
            self.svc = FacilityService(self.repo)
            self.requests.set_service(self.svc)
            self.setWindowTitle(f"{__app_name__} v{__version__} — {self.svc.cfg.facility.name}")
            self._populate_sidebar()
            self.refresh()
            self.sync()

    def _toggle_dark(self, on: bool):
        self.dark = on
        self.settings.setValue("dark", on)
        self.colours = theme.apply(QApplication.instance(), on)
        self.refresh()

    def _about(self):
        QMessageBox.about(
            self, "About KherveLAB",
            f"<b>{__app_name__} v{__version__}</b><br>Instrument booking for shared research "
            "facilities, kept in a Git repository.<br><br>Facility repository:<br>"
            f"<code>{html.escape(str(self.repo.path))}</code><br><br>"
            "© 2026 Gwilherm Kerherve — GPL v3")

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(5000)
        super().closeEvent(event)

