"""The schedule: week, day and month views for one instrument or all of them.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Built on QGraphicsView with one rect item per booking segment: the scene
gives hit-testing, drag and resize for far less than a hand-painted widget.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QGraphicsRectItem, QGraphicsScene,
                             QGraphicsSimpleTextItem, QGraphicsView, QMenu)

from .. import db, logic
from .theme import Colours, readable_text

WEEK, DAY, MONTH = "week", "day", "month"
GUTTER, HEADER = 56, 46
ROW = 22                      # pixels per 30 minutes
PPM = ROW / 30.0
DAY_H = 1440 * PPM
EDGE = 6


@dataclass
class Column:
    day: date
    instrument: int           # instrument id this column books on


@dataclass
class Segment:
    booking: dict
    col: int
    m0: int
    m1: int
    is_last: bool
    lane: int = 0
    lanes: int = 1


def _clip(t: QGraphicsSimpleTextItem, width: float) -> None:
    text = t.text()
    while text and t.boundingRect().width() > width - 6:
        text = text[:-1]
        t.setText(text + "…")


def paint_card(painter: QPainter, rect: QRectF, colour: QColor, *, dashed: bool = False,
               outline: QColor | None = None, raised: bool = True) -> None:
    """A rounded card with a soft shadow and a gentle top-to-bottom shading,
    so slots and bookings read as objects sitting on the calendar."""
    radius = min(6.0, rect.height() / 3, rect.width() / 3)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if raised:
        for spread, alpha in ((3.0, 14), (1.5, 26)):          # soft drop shadow
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 30, 60, alpha))
            painter.drawRoundedRect(rect.adjusted(0, spread, 0, spread), radius, radius)
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, colour.lighter(112))
    grad.setColorAt(1.0, colour.darker(104))
    painter.setBrush(QBrush(grad))
    edge = outline or colour.darker(125)
    painter.setPen(QPen(edge, 2.5 if outline else 1, Qt.PenStyle.DashLine if dashed
                        else Qt.PenStyle.SolidLine))
    painter.drawRoundedRect(rect, radius, radius)
    if raised and rect.height() > 6:                           # a thin top highlight
        painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
        painter.drawLine(rect.topLeft() + QPointF(radius, 1), rect.topRight() + QPointF(-radius, 1))
    painter.restore()


class SlotCard(QGraphicsRectItem):
    """A bookable slot drawn as a card (its brush colour is its state)."""

    def paint(self, painter, option, widget=None):
        paint_card(painter, self.rect(), self.brush().color())


class BookingItem(QGraphicsRectItem):
    def __init__(self, seg: Segment, rect: QRectF, colour: str, title: str, subtitle: str,
                 tip: str, editable: bool, stripe: str | None = None,
                 outline: str | None = None):
        super().__init__(rect)
        self.seg, self.editable = seg, editable
        c = QColor(colour)
        pending = seg.booking["status"] == "pending"
        if pending:
            self.setBrush(QBrush(c.lighter(150)))
            self.setPen(QPen(c.darker(130), 1.5, Qt.PenStyle.DashLine))
            text_colour = QColor("#1f2328")
        else:
            self.setBrush(QBrush(c))
            self.setPen(QPen(c.darker(150), 2 if seg.booking.get("mine") else 1))
            text_colour = QColor(readable_text(colour))
        self._card = (c.lighter(150) if pending else c, pending,
                      QColor(outline) if outline else None)
        if outline:                                   # booked during a reported issue
            self.setPen(QPen(QColor(outline), 3))
        if stripe:                                    # which instrument, on the all view
            bar = QGraphicsRectItem(QRectF(rect.x() + 2, rect.y() + 4, 3, rect.height() - 8),
                                    self)
            bar.setBrush(QBrush(QColor(stripe)))
            bar.setPen(QPen(Qt.PenStyle.NoPen))
            rect = rect.adjusted(4, 0, 0, 0)
        self.setToolTip(tip)
        self.setZValue(10)
        self.setAcceptHoverEvents(True)
        if rect.height() >= 14 and rect.width() >= 24:
            f = QFont()
            f.setPointSizeF(8.5)
            f.setBold(True)
            t = QGraphicsSimpleTextItem(title, self)
            t.setFont(f)
            t.setBrush(text_colour)
            t.setPos(rect.x() + 7, rect.y() + 3)
            _clip(t, rect.width())
            if rect.height() >= 30 and subtitle:
                f2 = QFont()
                f2.setPointSizeF(8)
                s = QGraphicsSimpleTextItem(subtitle, self)
                s.setFont(f2)
                s.setBrush(text_colour)
                s.setPos(rect.x() + 7, rect.y() + 16)
                _clip(s, rect.width())

    def paint(self, painter, option, widget=None):
        colour, pending, outline = self._card
        paint_card(painter, self.rect(), colour, dashed=pending, outline=outline)

    def hoverMoveEvent(self, event):
        if not self.editable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        near = self.seg.is_last and self.rect().bottom() - event.pos().y() <= EDGE
        self.setCursor(Qt.CursorShape.SizeVerCursor if near else Qt.CursorShape.OpenHandCursor)


class CalendarView(QGraphicsView):
    createRequested = pyqtSignal(int, object, object)          # instrument, start, end
    moveRequested = pyqtSignal(int, object, object, int)       # booking, start, end, instrument
    openRequested = pyqtSignal(int)                             # booking id
    dayActivated = pyqtSignal(object)
    hint = pyqtSignal(str)                                     # a tip for the status bar

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setMouseTracking(True)
        self.conn = None
        self.me = None
        self.instruments: list = []       # rows shown (one for an instrument tab)
        self.single = False               # True on an instrument's own tab
        self.anchor = date.today()
        self.mode = WEEK
        self.colours = Colours.light()
        self.columns: list[Column] = []
        self.colw = 120.0
        self._drag: dict | None = None
        self._ghost = None
        self._month_cells: list[tuple[QRectF, date]] = []
        self._scrolled = False
        self.palette_cfg: dict[str, str] = {}

    # -- state -----------------------------------------------------------------
    def set_state(self, conn, me, instruments: list, single: bool, anchor: date, mode: str,
                  colours: Colours) -> None:
        self.conn, self.me, self.instruments, self.single = conn, me, instruments, single
        self.anchor, self.mode, self.colours = anchor, mode, colours
        self.rebuild()

    def visible_range(self) -> tuple[date, date]:
        if self.mode == DAY:
            return self.anchor, self.anchor
        if self.mode == WEEK:
            s = self.anchor - timedelta(days=self.anchor.weekday())
            return s, s + timedelta(days=6)
        first = self.anchor.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return first, nxt - timedelta(days=1)

    def _inst(self, iid: int):
        return next((i for i in self.instruments if i["id"] == iid), None)

    @staticmethod
    def _at(d: date, minutes: float) -> datetime:
        return datetime(d.year, d.month, d.day) + timedelta(minutes=minutes)

    def _col_at(self, x: float) -> int | None:
        if x < GUTTER or not self.columns:
            return None
        i = int((x - GUTTER) // self.colw)
        return i if 0 <= i < len(self.columns) else None

    def _minutes_at(self, y: float) -> float:
        return min(1440.0, max(0.0, (y - HEADER) / PPM))

    def _slot(self, col: int) -> int:
        inst = self._inst(self.columns[col].instrument)
        return inst["slot_minutes"] if inst else 30

    def _snap_create(self, col: int, start: datetime, t: datetime | None) -> tuple:
        """Start (and end) of a new booking dragged on a column, following the
        instrument's daytime, evening and weekend periods."""
        inst = self._inst(self.columns[col].instrument)
        if inst is None or inst["booking_mode"] == "sessions":
            g = 30
            s0 = start.replace(minute=start.minute // g * g)
            e0 = max(t or s0, s0 + timedelta(minutes=g))
            return s0, e0
        s0 = logic.snap_start(inst, start)
        if t is None:
            t = s0 + timedelta(minutes=1)
        return s0, logic.snap_end(inst, s0, max(t, s0 + timedelta(minutes=1)))

    # -- data ------------------------------------------------------------------
    def bookings(self, start: datetime, end: datetime) -> list[dict]:
        if not self.instruments:
            return []
        ids = [i["id"] for i in self.instruments]
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            "SELECT b.*, u.full_name, u.group_name FROM bookings b JOIN users u "
            f"ON u.id=b.user_id WHERE b.instrument_id IN ({marks}) "
            "AND b.status IN ('pending','approved') AND b.start < ? AND b.end > ? "
            "ORDER BY b.start", (*ids, logic.fmt(end), logic.fmt(start))).fetchall()
        acting = logic.acts_for_others(self.me)
        show = db.setting(self.conn, "show_names") == "1" or acting
        now = logic.fmt(logic.now_local(self.conn))
        out = []
        for r in rows:
            d = dict(r)
            d["mine"] = r["user_id"] == self.me["id"]
            d["who"] = "You" if d["mine"] else (r["full_name"] if show else "Booked")
            d["editable"] = self.me["role"] == "admin" or ((d["mine"] or acting)
                                                           and r["start"] > now)
            out.append(d)
        return out

    # -- build -----------------------------------------------------------------
    def rebuild(self) -> None:
        sc = self.scene()
        sc.clear()
        if self.conn is not None:
            self.palette_cfg = {k: db.setting(self.conn, f"colour_{v}") for k, v in
                                (("free", "free"), ("closed", "closed"),
                                 ("booked", "booked"),
                                 ("problem", "problem"), ("down", "down"))}
        self._ghost = None
        self.setBackgroundBrush(QColor(self.colours.background))
        if self.conn is None:
            return
        if self.mode == MONTH:
            self._build_month()
            return
        avail = max(200, self.viewport().width() - GUTTER - 2)
        first, last = self.visible_range()
        if self.mode == WEEK or self.single:
            iid = self.instruments[0]["id"] if self.single and self.instruments else 0
            days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
            self.columns = [Column(d, iid) for d in days]
            self.colw = max(110.0, avail / len(days))
        else:
            self.columns = [Column(self.anchor, i["id"]) for i in self.instruments]
            self.colw = max(150.0, avail / max(1, len(self.columns)))
        sc.setSceneRect(0, 0, GUTTER + self.colw * max(1, len(self.columns)), HEADER + DAY_H)
        self._draw_grid()
        self._draw_bookings()
        self._draw_now()
        if not self._scrolled and self.isVisible():
            # deferred: the first layouts happen before the view has its real height,
            # and a scroll set then is clamped away
            self._scrolled = True
            QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(int(7 * 60 * PPM)))

    def _draw_grid(self) -> None:
        sc, c = self.scene(), self.colours
        today = date.today()
        none = QPen(Qt.PenStyle.NoPen)
        hour = QPen(QColor(c.grid_hour))
        faint = QPen(QColor(c.grid))
        for i, col in enumerate(self.columns):
            x = GUTTER + i * self.colw
            inst = self._inst(col.instrument) if (self.single or self.mode == DAY) else None
            if inst is not None:
                self._draw_slots(inst, col.day, x)
                continue
            # columns without slot cards (all instruments, by week) keep hour lines
            if col.day == today and self.mode == WEEK:
                sc.addRect(QRectF(x, HEADER, self.colw, DAY_H), none, QColor(c.today)).setZValue(-3)
            for h in range(25):
                y = HEADER + h * 60 * PPM
                sc.addLine(x, y, x + self.colw, y, faint).setZValue(-1)
        for i in range(len(self.columns) + 1):
            x = GUTTER + i * self.colw
            sc.addLine(x, HEADER, x, HEADER + DAY_H, hour).setZValue(-1)
        for h in range(25):                           # hour ticks beside the time labels
            y = HEADER + h * 60 * PPM
            sc.addLine(GUTTER - 6, y, GUTTER, y, hour).setZValue(-1)

    def _draw_slots(self, inst, day: date, x: float) -> None:
        """An empty calendar is not blank: every bookable slot is a box, grey
        when free and yellow or red when a problem or an out-of-order period
        touches it (its exact span is marked on the slot's left edge).
        Bookings are drawn on top. Outside the slots is closed."""
        sc, c, pal = self.scene(), self.colours, self.palette_cfg
        none = QPen(Qt.PenStyle.NoPen)
        # closed: a flat, calm background; the bookable slots sit on it as cards
        sc.addRect(QRectF(x, HEADER, self.colw, DAY_H), none,
                   QColor(pal.get("closed", "#e4e8ee"))).setZValue(-2.6)
        if day == date.today():                       # today: a faint tint behind the cards
            tint = QColor(c.today)
            tint.setAlpha(110)
            sc.addRect(QRectF(x, HEADER, self.colw, DAY_H), none, tint).setZValue(-2.5)
        d0, d1 = self._at(day, 0), self._at(day, 1440)
        small = QFont()
        small.setPointSizeF(7.5)
        day_issues = logic.issues(self.conn, inst["id"], d0, d1)
        for s0, s1, label in logic.slots(self.conn, inst, d0, d1):
            a, b = max(s0, d0), min(s1, d1)
            m0 = (a - d0).total_seconds() / 60
            m1 = (b - d0).total_seconds() / 60
            r = QRectF(x + 5, HEADER + m0 * PPM + 2, self.colw - 10, (m1 - m0) * PPM - 5)
            kinds = {i["kind"] for i in day_issues
                     if logic.parse(i["start"]) < s1 and
                     (i["end"] is None or logic.parse(i["end"]) > s0)}
            state = "down" if "down" in kinds else "problem" if "problem" in kinds else "free"
            fill = QColor(pal[state])
            box = SlotCard(r)
            box.setBrush(QBrush(fill))
            box.setPen(QPen(Qt.PenStyle.NoPen))
            box.setZValue(-0.9)
            sc.addItem(box)
            if r.height() >= 13 and s0 >= d0:
                span = (f"{s0:%H:%M}–{s1:%H:%M}" if s1 - s0 < timedelta(days=1)
                        else f"{s0:%a %H:%M} → {s1:%a %H:%M}")
                if label not in ("Daytime",):
                    span = f"{label} {span}"
                if state == "down":
                    span += " · out of order"
                elif state == "problem":
                    span += " · problem"
                t = sc.addSimpleText(span, small)
                t.setBrush(QColor("#ffffff" if state == "down" else c.muted
                                  if state == "free" else "#1f2328"))
                _clip(t, self.colw - 10)
                t.setPos(r.x() + 7, r.y() + 3)
                t.setZValue(-0.8)
        for i in day_issues:                          # the exact time of each issue
            a = max(logic.parse(i["start"]), d0)
            b = min(logic.parse(i["end"]) if i["end"] else d1, d1)
            if b <= a:
                continue
            y0 = HEADER + (a - d0).total_seconds() / 60 * PPM
            y1 = HEADER + (b - d0).total_seconds() / 60 * PPM
            bar = sc.addRect(QRectF(x + 1, y0, 4, y1 - y0), none,
                             QColor(pal["down" if i["kind"] == "down" else "problem"]))
            bar.setZValue(-0.7)
            bar.setToolTip(f"{logic.ISSUE_LABELS[i['kind']]}: {i['note'] or 'no details'}")

    def _segments(self) -> list[Segment]:
        first, last = self.columns[0].day, self.columns[-1].day
        segs: list[Segment] = []
        for b in self.bookings(self._at(first, 0), self._at(last, 1440)):
            bs, be = logic.parse(b["start"]), logic.parse(b["end"])
            for ci, col in enumerate(self.columns):
                if (self.mode == DAY and not self.single) and col.instrument != b["instrument_id"]:
                    continue
                d0, d1 = self._at(col.day, 0), self._at(col.day, 1440)
                if not (bs < d1 and d0 < be):
                    continue
                s, e = max(bs, d0), min(be, d1)
                m0 = 0 if s <= d0 else s.hour * 60 + s.minute
                m1 = 1440 if e >= d1 else e.hour * 60 + e.minute
                segs.append(Segment(b, ci, m0, max(m1, m0 + 10), is_last=be <= d1))
        by_col: dict[int, list[Segment]] = {}
        for s in segs:
            by_col.setdefault(s.col, []).append(s)
        for group in by_col.values():      # overlapping segments sit side by side
            group.sort(key=lambda s: (s.m0, s.m1))
            cluster: list[Segment] = []
            ends: list[int] = []
            cluster_end = -1
            for s in group:
                if s.m0 >= cluster_end and cluster:
                    for x in cluster:
                        x.lanes = len(ends)
                    cluster, ends = [], []
                for li, le in enumerate(ends):
                    if le <= s.m0:
                        s.lane, ends[li] = li, s.m1
                        break
                else:
                    s.lane = len(ends)
                    ends.append(s.m1)
                cluster.append(s)
                cluster_end = max(cluster_end, s.m1)
            for x in cluster:
                x.lanes = len(ends)
        return segs

    def _draw_bookings(self) -> None:
        cur = db.setting(self.conn, "currency")
        for seg in self._segments():
            b = seg.booking
            inst = self._inst(b["instrument_id"])
            colour = self.palette_cfg.get("booked", "#1f6feb")
            stripe = inst["colour"] if (inst and not self.single) else None
            state = logic.issue_state(self.conn, b["instrument_id"], logic.parse(b["start"]),
                                      logic.parse(b["end"]))
            outline = self.palette_cfg.get(state) if state != "ok" else None
            w = (self.colw - 6) / seg.lanes
            x = GUTTER + seg.col * self.colw + 3 + seg.lane * w
            rect = QRectF(x + 2, HEADER + seg.m0 * PPM + 2, w - 6, (seg.m1 - seg.m0) * PPM - 5)
            s, e = logic.parse(b["start"]), logic.parse(b["end"])
            # a run of a day or more shows its days: 08:00–08:00 would read as nothing
            when = (f"{s:%H:%M}–{e:%H:%M}" if e - s < timedelta(days=1)
                    else f"{s:%a %H:%M}–{e:%a %H:%M}")
            pend = " (pending)" if b["status"] == "pending" else ""
            if self.mode == WEEK and not self.single and len(self.instruments) > 1:
                # who first: the instrument already shows in the card's colour
                title, sub = b["who"], f"{inst['name'] if inst else '?'} · {when}{pend}"
            else:
                private = b["mine"] or logic.acts_for_others(self.me)
                title, sub = f"{when} {b['who']}", ((b["purpose"] or "") if private else "") + pend
            tip = (f"<b>{inst['name'] if inst else ''}</b><br>{s:%a %d %b %H:%M} – "
                   f"{e:%a %d %b %H:%M}<br>{b['who']}"
                   + (f" ({b['group_name']})" if b["group_name"] and b["who"] != "Booked" else "")
                   + f"<br>Status: {b['status']}"
                   + (f"<br>{b['purpose']}" if b["purpose"] and (
                       b["mine"] or logic.acts_for_others(self.me)) else "")
                   + (f"<br>Cost: {cur}{logic.cost(self.conn, b):,.2f}"
                      if b["mine"] or logic.acts_for_others(self.me) else ""))
            if outline:
                tip += f"<br><b>{'Out of order' if state == 'down' else 'Problem reported'}</b>"
            self.scene().addItem(BookingItem(seg, rect, colour, title, sub, tip,
                                             b["editable"], stripe, outline))

    def _draw_now(self) -> None:
        now = logic.now_local(self.conn)
        for i, col in enumerate(self.columns):
            if col.day == now.date():
                y = HEADER + (now.hour * 60 + now.minute) * PPM
                x = GUTTER + i * self.colw
                pen = QPen(QColor(self.colours.now_line), 2)
                self.scene().addLine(x, y, x + self.colw, y, pen).setZValue(20)

    def _build_month(self) -> None:
        sc, c = self.scene(), self.colours
        self.columns = []
        first, last = self.visible_range()
        start = first - timedelta(days=first.weekday())
        weeks = (last - start).days // 7 + 1
        w = max(700, self.viewport().width() - 2)
        h = max(480, self.viewport().height() - 2)
        cw, ch = w / 7, (h - 24) / weeks
        sc.setSceneRect(0, 0, w, h)
        self._month_cells = []
        bold = QFont()
        bold.setBold(True)
        small = QFont()
        small.setPointSizeF(8)
        for i, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            t = sc.addSimpleText(name, bold)
            t.setBrush(QColor(c.header_text))
            t.setPos(i * cw + 6, 4)
        books = self.bookings(self._at(start, 0), self._at(start + timedelta(days=weeks * 7), 0))
        today = date.today()
        for wk in range(weeks):
            for d in range(7):
                day = start + timedelta(days=wk * 7 + d)
                r = QRectF(d * cw, 24 + wk * ch, cw, ch)
                bg = c.today if day == today else (c.background if day.month == first.month
                                                   else c.closed)
                sc.addRect(r, QPen(QColor(c.grid_hour)), QColor(bg))
                self._month_cells.append((r, day))
                n = sc.addSimpleText(str(day.day), bold)
                n.setBrush(QColor(c.text if day.month == first.month else "#8c959f"))
                n.setPos(r.x() + 5, r.y() + 3)
                d0, d1 = self._at(day, 0), self._at(day, 1440)
                items = [b for b in books if logic.parse(b["start"]) < d1
                         and logic.parse(b["end"]) > d0]
                room = max(0, int((ch - 22) // 15))
                for k, b in enumerate(items[:room]):
                    inst = self._inst(b["instrument_id"])
                    colour = (self.palette_cfg.get("booked", "#1f6feb") if self.single else
                              inst["colour"] if inst else "#888888")
                    bar = QRectF(r.x() + 4, r.y() + 20 + k * 15, cw - 8, 13)
                    pending = b["status"] == "pending"
                    item = sc.addRect(bar, QPen(QColor(colour), 1, Qt.PenStyle.DashLine if pending
                                                else Qt.PenStyle.SolidLine),
                                      QColor(colour).lighter(140) if pending else QColor(colour))
                    s = logic.parse(b["start"])
                    label = (f"{s:%H:%M} " + (b["who"] if self.single else
                                               f"{inst['name'] if inst else ''} · {b['who']}"))
                    t = QGraphicsSimpleTextItem(label, item)
                    t.setFont(small)
                    t.setBrush(QColor("#1f2328" if pending else readable_text(colour)))
                    t.setPos(bar.x() + 3, bar.y())
                    _clip(t, bar.width())
                if len(items) > room:
                    more = sc.addSimpleText(f"+{len(items) - room}", small)
                    more.setBrush(QColor(c.muted))
                    more.setPos(r.right() - 26, r.y() + 4)

    # -- fixed header and time gutter -----------------------------------------------
    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if self.mode == MONTH or not self.columns or self.conn is None:
            return
        c = self.colours
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        painter.save()
        painter.fillRect(QRectF(vis.left(), vis.top(), GUTTER, vis.height()), QColor(c.header))
        f = QFont()
        f.setPointSizeF(8)
        painter.setFont(f)
        painter.setPen(QColor(c.header_text))
        for hour in range(24):
            y = HEADER + hour * 60 * PPM
            if y - 6 < vis.top() + HEADER:
                continue
            painter.drawText(QRectF(vis.left(), y - 7, GUTTER - 6, 14),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{hour:02d}:00")
        painter.fillRect(QRectF(vis.left(), vis.top(), vis.width(), HEADER), QColor(c.header))
        painter.setPen(QPen(QColor(c.grid_hour)))
        painter.drawLine(QPointF(vis.left(), vis.top() + HEADER),
                         QPointF(vis.right(), vis.top() + HEADER))
        bold = QFont()
        bold.setBold(True)
        bold.setPointSizeF(9)
        today = date.today()
        by_day = self.mode == WEEK or self.single
        for i, col in enumerate(self.columns):
            x = GUTTER + i * self.colw
            if x + self.colw < vis.left() + GUTTER or x > vis.right():
                continue
            r = QRectF(x + 4, vis.top() + 3, self.colw - 8, HEADER - 6)
            if by_day:
                top, bottom, colour = f"{col.day:%a}", f"{col.day:%d %b}", None
            else:
                inst = self._inst(col.instrument)
                top, bottom = inst["name"], inst["location"] or inst["description"]
                colour = inst["colour"]
            if colour:
                painter.fillRect(QRectF(x + 2, vis.top() + 4, 4, HEADER - 8), QColor(colour))
            painter.setPen(QColor(c.now_line if by_day and col.day == today else c.header_text))
            painter.setFont(bold)
            fm = painter.fontMetrics()
            painter.drawText(r.adjusted(4, 0, 0, -HEADER / 2 + 2),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(top, Qt.TextElideMode.ElideRight, int(r.width() - 4)))
            painter.setFont(f)
            fm = painter.fontMetrics()
            painter.drawText(r.adjusted(4, HEADER / 2 - 2, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(bottom or "", Qt.TextElideMode.ElideRight,
                                           int(r.width() - 4)))
        painter.fillRect(QRectF(vis.left(), vis.top(), GUTTER, HEADER), QColor(c.header))
        painter.restore()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self.viewport().update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.rebuild()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.rebuild()

    # -- interaction --------------------------------------------------------------
    def _item_at(self, vp) -> BookingItem | None:
        for it in self.items(vp):
            while it is not None and not isinstance(it, BookingItem):
                it = it.parentItem()
            if isinstance(it, BookingItem):
                return it
        return None

    @staticmethod
    def _snap(minutes: float, g: int) -> int:
        return int(round(minutes / g) * g)

    # A booking is never made by a plain click: the pointer must be dragged
    # past the drag distance, or held still for HOLD_MS, which arms the slot
    # (it lights up) and books on release.
    HOLD_MS = 600

    def mousePressEvent(self, event) -> None:
        if self.conn is None:
            return
        vp = event.position().toPoint()
        pos = self.mapToScene(vp)
        if self.mode == MONTH:
            if event.button() == Qt.MouseButton.LeftButton:
                for r, d in self._month_cells:
                    if r.contains(pos):
                        self.dayActivated.emit(d)
                        return
            return
        if event.button() != Qt.MouseButton.LeftButton or vp.y() < HEADER or vp.x() < GUTTER:
            return super().mousePressEvent(event)
        col = self._col_at(pos.x())
        if col is None:
            return
        item = self._item_at(vp)
        minutes = self._minutes_at(pos.y())
        if item is not None:
            if not item.editable:
                self._drag = {"mode": "open", "item": item, "col": col, "moved": False,
                              "press": vp}
                return
            resize = item.seg.is_last and item.rect().bottom() - pos.y() <= EDGE
            self._drag = {"mode": "resize" if resize else "move", "item": item, "col": col,
                          "m": minutes, "moved": False, "press": vp}
        else:
            if not self.columns[col].instrument:
                return
            day = self.columns[col].day
            s0, e0 = self._snap_create(col, self._at(day, minutes), None)
            drag = {"mode": "create", "col": col, "s": s0, "e": e0, "moved": False,
                    "armed": False, "press": vp}
            self._drag = drag
            QTimer.singleShot(self.HOLD_MS, lambda: self._held(drag))

    def _held(self, drag: dict) -> None:
        """Press-and-hold arms a booking of the slot under the pointer."""
        if self._drag is drag and not drag["armed"]:
            drag["armed"] = True
            self._show_ghost()

    def _past_threshold(self, d: dict, vp) -> bool:
        return (vp - d["press"]).manhattanLength() >= QApplication.startDragDistance()

    def mouseMoveEvent(self, event) -> None:
        if not self._drag or self._drag["mode"] == "open":
            return super().mouseMoveEvent(event)
        vp = event.position().toPoint()
        d = self._drag
        if not d["moved"] and not self._past_threshold(d, vp):
            return                                  # a click's jitter is not a drag
        pos = self.mapToScene(vp)
        d["moved"] = True
        m = self._minutes_at(pos.y())
        if d["mode"] == "create":
            d["armed"] = True
            col = self._col_at(pos.x())
            day = self.columns[d["col"] if col is None else col].day
            d["e"] = self._snap_create(d["col"], d["s"], self._at(day, m))[1]
        else:
            d["m_now"] = m
            col = self._col_at(pos.x())
            if col is not None and d["mode"] == "move":
                d["col_now"] = col
        self._show_ghost()

    def mouseReleaseEvent(self, event) -> None:
        if not self._drag:
            return super().mouseReleaseEvent(event)
        d, self._drag = self._drag, None
        if self._ghost is not None:
            self.scene().removeItem(self._ghost)
            self._ghost = None
        if d["mode"] == "open":
            self.openRequested.emit(d["item"].seg.booking["id"])
            return
        col = self.columns[d["col"]]
        if d["mode"] == "create":
            if d["armed"]:
                self.createRequested.emit(col.instrument, d["s"], d["e"])
            else:
                self.hint.emit("To book, drag across the time you want, or press and hold "
                               "on a slot.")
            return
        if not d["moved"]:
            self.openRequested.emit(d["item"].seg.booking["id"])
            return
        b = d["item"].seg.booking
        start, end, iid = self._dragged(d)
        if (logic.fmt(start), logic.fmt(end), iid) != (b["start"], b["end"], b["instrument_id"]):
            self.moveRequested.emit(b["id"], start, end, iid)

    def _dragged(self, d) -> tuple[datetime, datetime, int]:
        b = d["item"].seg.booking
        g = self._slot(d["col"])
        delta = self._snap(d.get("m_now", d["m"]) - d["m"], g)
        s, e = logic.parse(b["start"]), logic.parse(b["end"])
        if d["mode"] == "resize":
            end = e + timedelta(minutes=delta)
            return s, max(end, s + timedelta(minutes=g)), b["instrument_id"]
        src, dst = self.columns[d["col"]], self.columns[d.get("col_now", d["col"])]
        shift = timedelta(days=(dst.day - src.day).days, minutes=delta)
        iid = dst.instrument if (self.mode == DAY and not self.single) else b["instrument_id"]
        return s + shift, e + shift, iid

    def _show_ghost(self) -> None:
        d = self._drag
        if not d or d["mode"] == "open" or (d["mode"] == "create" and not d["armed"]):
            return
        if d["mode"] == "create":
            col = d["col"]
            day = self.columns[col].day
            d0 = self._at(day, 0)
            m0 = max(0.0, (d["s"] - d0).total_seconds() / 60)
            m1 = min(1440.0, (d["e"] - d0).total_seconds() / 60)
            m1 = max(m1, m0 + 15)
        else:
            if not d["moved"]:
                return
            start, end, _ = self._dragged(d)
            col = d.get("col_now", d["col"]) if d["mode"] == "move" else d["item"].seg.col
            day = self.columns[col].day
            d0, d1 = self._at(day, 0), self._at(day, 1440)
            s, e = max(start, d0), min(end, d1)
            m0 = 0 if start <= d0 else s.hour * 60 + s.minute
            m1 = 1440 if end >= d1 else e.hour * 60 + e.minute
            m1 = max(m1, m0 + 15)
        rect = QRectF(GUTTER + col * self.colw + 3, HEADER + m0 * PPM, self.colw - 6,
                      (m1 - m0) * PPM)
        if self._ghost is None:
            self._ghost = self.scene().addRect(rect, QPen(QColor("#0969da"), 2,
                                                          Qt.PenStyle.DashLine),
                                               QColor(9, 105, 218, 50))
            self._ghost.setZValue(30)
        else:
            self._ghost.setRect(rect)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.mode == MONTH:
            return self.mousePressEvent(event)
        item = self._item_at(event.position().toPoint())
        if item is not None:
            self._drag = None
            self.openRequested.emit(item.seg.booking["id"])

    def contextMenuEvent(self, event) -> None:
        if self.mode == MONTH:
            return
        item = self._item_at(event.pos())
        if item is None:
            return
        menu = QMenu(self)
        a = menu.addAction("Details…")
        if menu.exec(event.globalPos()) is a:
            self.openRequested.emit(item.seg.booking["id"])
