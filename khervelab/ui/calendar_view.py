"""The calendar grid: week, day (instruments side by side) and month views.

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
from datetime import date, datetime, time, timedelta

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
                             QGraphicsView, QMenu)

from ..core.facility import FacilityService
from ..core.models import DAYS, Booking, Instrument
from .theme import LIGHT, CalendarColours, readable_text

WEEK, DAY, MONTH = "week", "day", "month"
GUTTER = 56
HEADER = 46
ROW = 22                 # pixels per 30 minutes
PPM = ROW / 30.0         # pixels per minute
DAY_H = 1440 * PPM
EDGE = 6                 # resize grip height


@dataclass
class Column:
    day: date
    instrument: str | None  # None in week view: all visible instruments share it


@dataclass
class Segment:
    booking: Booking
    col: int
    m0: int
    m1: int
    is_last: bool
    lane: int = 0
    lanes: int = 1


class BookingItem(QGraphicsRectItem):
    def __init__(self, seg: Segment, rect: QRectF, colour: str, title: str, subtitle: str,
                 tooltip: str):
        super().__init__(rect)
        self.seg = seg
        c = QColor(colour)
        if seg.booking.kind in ("maintenance", "blocked"):
            self.setBrush(QBrush(c.darker(130), Qt.BrushStyle.BDiagPattern))
            self.setPen(QPen(c.darker(130), 1.5))
            text_colour = QColor("#24292f")
        else:
            self.setBrush(QBrush(c))
            self.setPen(QPen(c.darker(140), 1))
            text_colour = QColor(readable_text(colour))
        if seg.booking.kind == "training":
            self.setPen(QPen(c.darker(160), 2, Qt.PenStyle.DashLine))
        self.setToolTip(tooltip)
        self.setZValue(10)
        self.setAcceptHoverEvents(True)
        if rect.height() >= 14 and rect.width() >= 24:
            t = QGraphicsSimpleTextItem(title, self)
            f = QFont()
            f.setPointSizeF(8.5)
            f.setBold(True)
            t.setFont(f)
            t.setBrush(text_colour)
            t.setPos(rect.x() + 4, rect.y() + 1)
            self._clip(t, rect)
            if rect.height() >= 30 and subtitle:
                s = QGraphicsSimpleTextItem(subtitle, self)
                f2 = QFont()
                f2.setPointSizeF(8)
                s.setFont(f2)
                s.setBrush(text_colour)
                s.setPos(rect.x() + 4, rect.y() + 14)
                self._clip(s, rect)

    @staticmethod
    def _clip(t: QGraphicsSimpleTextItem, rect: QRectF) -> None:
        text = t.text()
        while text and t.boundingRect().width() > rect.width() - 6:
            text = text[:-1]
            t.setText(text + "…")

    def hoverMoveEvent(self, event):
        near = self.seg.is_last and self.rect().bottom() - event.pos().y() <= EDGE
        self.setCursor(Qt.CursorShape.SizeVerCursor if near else Qt.CursorShape.OpenHandCursor)


class CalendarView(QGraphicsView):
    createRequested = pyqtSignal(str, object, object)          # instrument, start, end
    moveRequested = pyqtSignal(object, object, object, str)    # booking, start, end, instrument
    editRequested = pyqtSignal(object)
    deleteRequested = pyqtSignal(object)
    dayActivated = pyqtSignal(object)                          # date

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setMouseTracking(True)
        self.svc: FacilityService | None = None
        self.instruments: list[Instrument] = []
        self.current: str | None = None
        self.anchor = date.today()
        self.mode = WEEK
        self.colours: CalendarColours = LIGHT
        self.columns: list[Column] = []
        self.colw = 120.0
        self._drag: dict | None = None
        self._ghost: QGraphicsRectItem | None = None
        self._month_cells: list[tuple[QRectF, date]] = []
        self._scrolled_once = False

    # -- public -----------------------------------------------------------
    def set_state(self, svc: FacilityService, instruments: list[Instrument], current: str | None,
                  anchor: date, mode: str, colours: CalendarColours) -> None:
        self.svc, self.instruments, self.current = svc, instruments, current
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

    # -- geometry ---------------------------------------------------------
    @property
    def tz(self):
        return self.svc.tz

    def _local(self, d: date, minutes: float) -> datetime:
        naive = datetime(d.year, d.month, d.day) + timedelta(minutes=minutes)
        return naive.replace(tzinfo=self.tz)

    def _col_at(self, x: float) -> int | None:
        if x < GUTTER or not self.columns:
            return None
        i = int((x - GUTTER) // self.colw)
        return i if 0 <= i < len(self.columns) else None

    def _minutes_at(self, y: float) -> float:
        return min(1440.0, max(0.0, (y - HEADER) / PPM))

    def _inst_for_col(self, col: int) -> Instrument | None:
        iid = self.columns[col].instrument or self.current
        return self.svc.instruments.get(iid) if (self.svc and iid) else None

    def _granularity(self, col: int) -> int:
        inst = self._inst_for_col(col)
        return inst.slot_granularity_minutes if inst else 30

    # -- build ------------------------------------------------------------
    def rebuild(self) -> None:
        sc = self.scene()
        sc.clear()
        self._ghost = None
        self.setBackgroundBrush(QColor(self.colours.background))
        if self.svc is None:
            return
        if self.mode == MONTH:
            self._build_month()
            return
        avail = max(200, self.viewport().width() - GUTTER - 2)
        if self.mode == WEEK:
            s, _ = self.visible_range()
            self.columns = [Column(s + timedelta(days=i), None) for i in range(7)]
            self.colw = max(110.0, avail / 7)
        else:
            self.columns = [Column(self.anchor, i.id) for i in self.instruments]
            self.colw = max(150.0, avail / max(1, len(self.columns)))
        width = GUTTER + self.colw * max(1, len(self.columns))
        sc.setSceneRect(0, 0, width, HEADER + DAY_H)
        self._draw_grid()
        self._draw_bookings()
        self._draw_now()
        if not self._scrolled_once:
            self._scrolled_once = True
            self.verticalScrollBar().setValue(int(7 * 60 * PPM))

    def _draw_grid(self) -> None:
        sc, c = self.scene(), self.colours
        today = date.today()
        no_pen = QPen(Qt.PenStyle.NoPen)
        for i, col in enumerate(self.columns):
            x = GUTTER + i * self.colw
            if col.day == today and self.mode == WEEK:
                sc.addRect(QRectF(x, HEADER, self.colw, DAY_H), no_pen, QColor(c.today)).setZValue(-3)
            inst = self._inst_for_col(i)
            if inst and inst.bookable_hours:
                ranges = inst.bookable_hours.get(DAYS[col.day.weekday()], ())
                for slot in range(48):
                    m0 = slot * 30
                    t0 = time(m0 // 60, m0 % 60)
                    t1 = None if m0 + 30 == 1440 else time((m0 + 30) // 60, (m0 + 30) % 60)
                    if not any(r.contains(t0, t1) for r in ranges):
                        sc.addRect(QRectF(x, HEADER + m0 * PPM, self.colw, ROW), no_pen,
                                   QColor(c.closed)).setZValue(-2)
        grid, hour = QPen(QColor(c.grid)), QPen(QColor(c.grid_hour))
        right = GUTTER + self.colw * len(self.columns)
        for slot in range(49):
            y = HEADER + slot * ROW
            sc.addLine(GUTTER, y, right, y, hour if slot % 2 == 0 else grid).setZValue(-1)
        for i in range(len(self.columns) + 1):
            x = GUTTER + i * self.colw
            sc.addLine(x, HEADER, x, HEADER + DAY_H, hour).setZValue(-1)

    def _segments(self) -> list[Segment]:
        segs: list[Segment] = []
        if not self.columns:
            return segs
        first, last = self.columns[0].day, self.columns[-1].day
        start = self._local(first, 0)
        end = self._local(last, 1440)
        wanted = {i.id for i in self.instruments}
        if self.mode == WEEK and self.current:
            wanted.add(self.current)
        for b in self.svc.bookings_between(start, end, wanted):
            for ci, col in enumerate(self.columns):
                if col.instrument and col.instrument != b.instrument:
                    continue
                d0, d1 = self._local(col.day, 0), self._local(col.day, 1440)
                if not (b.start < d1 and d0 < b.end):
                    continue
                s = max(b.start, d0).astimezone(self.tz)
                e = min(b.end, d1).astimezone(self.tz)
                m0 = 0 if s <= d0 else s.hour * 60 + s.minute
                m1 = 1440 if e >= d1 else e.hour * 60 + e.minute
                segs.append(Segment(b, ci, m0, max(m1, m0 + 10), is_last=b.end <= d1))
        # lanes: overlapping segments in one column sit side by side
        by_col: dict[int, list[Segment]] = {}
        for s in segs:
            by_col.setdefault(s.col, []).append(s)
        for group in by_col.values():
            group.sort(key=lambda s: (s.m0, s.m1))
            cluster: list[Segment] = []
            lane_ends: list[int] = []
            cluster_end = -1
            for s in group:
                if s.m0 >= cluster_end and cluster:
                    for x in cluster:
                        x.lanes = len(lane_ends)
                    cluster, lane_ends = [], []
                for li, le in enumerate(lane_ends):
                    if le <= s.m0:
                        s.lane = li
                        lane_ends[li] = s.m1
                        break
                else:
                    s.lane = len(lane_ends)
                    lane_ends.append(s.m1)
                cluster.append(s)
                cluster_end = max(cluster_end, s.m1)
            for x in cluster:
                x.lanes = len(lane_ends)
        return segs

    def _draw_bookings(self) -> None:
        for seg in self._segments():
            b = seg.booking
            inst = self.svc.instruments.get(b.instrument)
            colour = inst.colour if inst else "#888888"
            lane_w = (self.colw - 6) / seg.lanes
            x = GUTTER + seg.col * self.colw + 3 + seg.lane * lane_w
            rect = QRectF(x, HEADER + seg.m0 * PPM + 1, lane_w - 2, (seg.m1 - seg.m0) * PPM - 2)
            user = self.svc.users.get(b.user)
            who = user.display if user else b.user
            s, e = b.start.astimezone(self.tz), b.end.astimezone(self.tz)
            when = f"{s:%H:%M}–{e:%H:%M}"
            if self.mode == WEEK and len(self.instruments) > 1:
                title = f"{inst.name if inst else b.instrument}"
                subtitle = f"{when} · {who}"
            else:
                title = f"{when} {who}"
                subtitle = b.kind if b.kind != "measurement" else ", ".join(b.samples)
            tip = [f"<b>{inst.name if inst else b.instrument}</b>",
                   f"{s:%a %d %b %H:%M} – {e:%a %d %b %H:%M}", f"{who} ({b.user})",
                   f"Kind: {b.kind}"]
            if b.samples:
                tip.append("Samples: " + ", ".join(b.samples))
            if b.notes:
                tip.append(b.notes)
            if b.override:
                tip.append("Overrides: " + ", ".join(b.override))
            self.scene().addItem(BookingItem(seg, rect, colour, title, subtitle, "<br>".join(tip)))

    def _draw_now(self) -> None:
        now = datetime.now(self.tz)
        for i, col in enumerate(self.columns):
            if col.day == now.date():
                y = HEADER + (now.hour * 60 + now.minute) * PPM
                x = GUTTER + i * self.colw
                pen = QPen(QColor(self.colours.now_line), 2)
                self.scene().addLine(x, y, x + self.colw, y, pen).setZValue(20)
                self.scene().addEllipse(x - 4, y - 4, 8, 8, pen,
                                        QBrush(QColor(self.colours.now_line))).setZValue(20)

    def _build_month(self) -> None:
        sc, c = self.scene(), self.colours
        self.columns = []
        first, last = self.visible_range()
        grid_start = first - timedelta(days=first.weekday())
        weeks = ((last - grid_start).days // 7) + 1
        w = max(700, self.viewport().width() - 2)
        h = max(480, self.viewport().height() - 2)
        cw, ch = w / 7, (h - 24) / weeks
        sc.setSceneRect(0, 0, w, h)
        self._month_cells = []
        hdr = QFont()
        hdr.setBold(True)
        for i, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            t = sc.addSimpleText(name, hdr)
            t.setBrush(QColor(c.header_text))
            t.setPos(i * cw + 6, 4)
        wanted = {i.id for i in self.instruments}
        today = date.today()
        small = QFont()
        small.setPointSizeF(8)
        for wk in range(weeks):
            for d in range(7):
                day = grid_start + timedelta(days=wk * 7 + d)
                r = QRectF(d * cw, 24 + wk * ch, cw, ch)
                bg = c.today if day == today else (c.background if day.month == first.month
                                                   else c.closed)
                sc.addRect(r, QPen(QColor(c.grid_hour)), QColor(bg))
                self._month_cells.append((r, day))
                num = sc.addSimpleText(str(day.day), hdr)
                num.setBrush(QColor(c.text if day.month == first.month else "#8c959f"))
                num.setPos(r.x() + 5, r.y() + 3)
                items = self.svc.bookings_on(day, wanted)
                max_bars = max(0, int((ch - 22) // 15))
                for k, b in enumerate(items[:max_bars]):
                    inst = self.svc.instruments.get(b.instrument)
                    colour = inst.colour if inst else "#888888"
                    bar = QRectF(r.x() + 4, r.y() + 20 + k * 15, cw - 8, 13)
                    item = sc.addRect(bar, QPen(Qt.PenStyle.NoPen), QColor(colour))
                    s = b.start.astimezone(self.tz)
                    label = f"{s:%H:%M} {inst.name if inst else b.instrument}"
                    t = QGraphicsSimpleTextItem(label, item)
                    t.setFont(small)
                    t.setBrush(QColor(readable_text(colour)))
                    t.setPos(bar.x() + 3, bar.y())
                    BookingItem._clip(t, bar)
                    item.setToolTip(f"{label}<br>{b.user} · {b.kind}")
                if len(items) > max_bars:
                    more = sc.addSimpleText(f"+{len(items) - max_bars} more", small)
                    more.setBrush(QColor("#57606a"))
                    more.setPos(r.right() - 60, r.y() + 4)

    # -- fixed header and time gutter ---------------------------------------
    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if self.mode == MONTH or not self.columns or self.svc is None:
            return
        c = self.colours
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        painter.save()
        # gutter
        painter.fillRect(QRectF(vis.left(), vis.top(), GUTTER, vis.height()), QColor(c.header))
        painter.setPen(QColor(c.header_text))
        f = QFont()
        f.setPointSizeF(8)
        painter.setFont(f)
        for hour in range(24):
            y = HEADER + hour * 60 * PPM
            if y - 6 < vis.top() + HEADER:
                continue
            painter.drawText(QRectF(vis.left(), y - 7, GUTTER - 6, 14),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{hour:02d}:00")
        # header
        painter.fillRect(QRectF(vis.left(), vis.top(), vis.width(), HEADER), QColor(c.header))
        painter.setPen(QPen(QColor(c.grid_hour)))
        painter.drawLine(QPointF(vis.left(), vis.top() + HEADER),
                         QPointF(vis.right(), vis.top() + HEADER))
        bold = QFont()
        bold.setBold(True)
        bold.setPointSizeF(9)
        today = date.today()
        for i, col in enumerate(self.columns):
            x = GUTTER + i * self.colw
            if x + self.colw < vis.left() + GUTTER or x > vis.right():
                continue
            r = QRectF(x + 4, vis.top() + 3, self.colw - 8, HEADER - 6)
            if self.mode == WEEK:
                top, bottom = f"{col.day:%a}", f"{col.day:%d %b}"
                colour = None
            else:
                inst = self.svc.instruments.get(col.instrument)
                top = inst.name if inst else col.instrument
                bottom = inst.category if inst else ""
                colour = inst.colour if inst else None
            if colour:
                painter.fillRect(QRectF(x + 2, vis.top() + 4, 4, HEADER - 8), QColor(colour))
            painter.setPen(QColor(c.now_line if col.day == today and self.mode == WEEK
                                  else c.header_text))
            painter.setFont(bold)
            painter.drawText(r.adjusted(4, 0, 0, -HEADER / 2 + 2),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(top, Qt.TextElideMode.ElideRight,
                                                              int(r.width() - 4)))
            painter.setFont(f)
            painter.drawText(r.adjusted(4, HEADER / 2 - 2, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(bottom, Qt.TextElideMode.ElideRight,
                                                              int(r.width() - 4)))
        painter.fillRect(QRectF(vis.left(), vis.top(), GUTTER, HEADER), QColor(c.header))
        painter.restore()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self.viewport().update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.rebuild()

    # -- interaction ------------------------------------------------------
    def _in_chrome(self, viewport_pos) -> bool:
        return viewport_pos.y() < HEADER or viewport_pos.x() < GUTTER

    def _item_at(self, viewport_pos) -> BookingItem | None:
        for it in self.items(viewport_pos):
            while it is not None and not isinstance(it, BookingItem):
                it = it.parentItem()
            if isinstance(it, BookingItem):
                return it
        return None

    def _snap(self, minutes: float, g: int) -> int:
        return int(round(minutes / g) * g)

    def mousePressEvent(self, event) -> None:
        if self.svc is None:
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
        if event.button() != Qt.MouseButton.LeftButton or self._in_chrome(vp):
            return super().mousePressEvent(event)
        col = self._col_at(pos.x())
        if col is None:
            return
        item = self._item_at(vp)
        g = self._granularity(col)
        minutes = self._minutes_at(pos.y())
        if item is not None:
            r = item.rect()
            resize = item.seg.is_last and r.bottom() - pos.y() <= EDGE
            self._drag = {"mode": "resize" if resize else "move", "item": item, "col": col,
                          "m": minutes, "moved": False}
        else:
            if self.mode == WEEK and not self.current:
                return
            m0 = int(minutes // g * g)
            self._drag = {"mode": "create", "col": col, "m0": m0, "m1": m0 + g, "moved": False}
        self._show_ghost()

    def mouseMoveEvent(self, event) -> None:
        if not self._drag:
            return super().mouseMoveEvent(event)
        pos = self.mapToScene(event.position().toPoint())
        d = self._drag
        d["moved"] = True
        g = self._granularity(d["col"])
        m = self._minutes_at(pos.y())
        col = self._col_at(pos.x())
        if d["mode"] == "create":
            end = max(d["m0"] + g, self._snap(m, g))
            d["m1"] = min(1440, end)
        elif d["mode"] == "resize":
            d["m_now"] = m
        else:
            d["m_now"] = m
            if col is not None:
                d["col_now"] = col
        self._show_ghost()

    def mouseReleaseEvent(self, event) -> None:
        if not self._drag:
            return super().mouseReleaseEvent(event)
        d, self._drag = self._drag, None
        if self._ghost is not None:
            self.scene().removeItem(self._ghost)
            self._ghost = None
        col = self.columns[d["col"]]
        if d["mode"] == "create":
            iid = col.instrument or self.current
            if iid:
                self.createRequested.emit(iid, self._local(col.day, d["m0"]),
                                          self._local(col.day, d["m1"]))
            return
        if not d["moved"]:
            return
        new = self._dragged_times(d)
        if new is None:
            return
        b: Booking = d["item"].seg.booking
        start, end, iid = new
        if (start, end, iid) != (b.start, b.end, b.instrument):
            self.moveRequested.emit(b, start, end, iid)

    def _dragged_times(self, d) -> tuple[datetime, datetime, str] | None:
        b: Booking = d["item"].seg.booking
        g = self._granularity(d["col"])
        delta = self._snap(d.get("m_now", d["m"]) - d["m"], g)
        if d["mode"] == "resize":
            end = b.end + timedelta(minutes=delta)
            if end <= b.start:
                end = b.start + timedelta(minutes=g)
            return b.start, end, b.instrument
        col_now = d.get("col_now", d["col"])
        src, dst = self.columns[d["col"]], self.columns[col_now]
        day_shift = (dst.day - src.day).days
        iid = dst.instrument or b.instrument
        s = b.start.astimezone(self.tz).replace(tzinfo=None) + timedelta(days=day_shift,
                                                                          minutes=delta)
        e = b.end.astimezone(self.tz).replace(tzinfo=None) + timedelta(days=day_shift,
                                                                        minutes=delta)
        return s.replace(tzinfo=self.tz), e.replace(tzinfo=self.tz), iid

    def _show_ghost(self) -> None:
        d = self._drag
        if not d:
            return
        if d["mode"] == "create":
            col, m0, m1 = d["col"], d["m0"], d["m1"]
        else:
            if not d["moved"]:
                return
            seg = d["item"].seg
            start, end, _ = self._dragged_times(d)
            col = d.get("col_now", d["col"]) if d["mode"] == "move" else seg.col
            day = self.columns[col].day
            d0 = self._local(day, 0)
            s = max(start, d0).astimezone(self.tz)
            e = min(end, self._local(day, 1440)).astimezone(self.tz)
            m0 = 0 if start <= d0 else s.hour * 60 + s.minute
            m1 = 1440 if e >= self._local(day, 1440) else e.hour * 60 + e.minute
            if m1 <= m0:
                m1 = m0 + 15
        rect = QRectF(GUTTER + col * self.colw + 3, HEADER + m0 * PPM, self.colw - 6,
                      (m1 - m0) * PPM)
        if self._ghost is None:
            self._ghost = self.scene().addRect(rect, QPen(QColor("#0969da"), 2, Qt.PenStyle.DashLine),
                                               QColor(9, 105, 218, 50))
            self._ghost.setZValue(30)
        else:
            self._ghost.setRect(rect)

    def mouseDoubleClickEvent(self, event) -> None:
        vp = event.position().toPoint()
        if self.mode == MONTH:
            return self.mousePressEvent(event)
        item = self._item_at(vp)
        if item is not None:
            self._drag = None
            self.editRequested.emit(item.seg.booking)

    def contextMenuEvent(self, event) -> None:
        if self.mode == MONTH:
            return
        item = self._item_at(event.pos())
        if item is None:
            return
        menu = QMenu(self)
        edit = menu.addAction("Edit…")
        delete = menu.addAction("Delete…")
        chosen = menu.exec(event.globalPos())
        if chosen is edit:
            self.editRequested.emit(item.seg.booking)
        elif chosen is delete:
            self.deleteRequested.emit(item.seg.booking)
