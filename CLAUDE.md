# CLAUDE.md — working conventions for KherveLAB

KherveLAB is a **desktop app (PyQt6)** for booking the instruments of **one
lab**:

- **Where it runs:** on the lab computer. Users log into the app, and the
  schedule has an "All instruments" tab plus one calendar tab per
  instrument.
- **Approval:** each instrument is set to auto, trained-only auto or manual
  approval.
- **Costs:** every instrument has hourly rates per user category, and the
  manager exports usage and cost statements (PDF, Excel, CSV).
- **Web pages:** an optional Flask web interface on the same database can be
  started from the app's Manage menu, or run headless with `--serve`. The app
  must never require the browser.

Keep it simple. The department-wide design (Git-backed repository, published
site, GitHub requests, logbook, data index) was deliberately dropped; it is
kept at the git tag `v0.7-department`. Do not bring those parts back unless
the user asks.

Stack:

- Python 3.10+
- PyQt6
- SQLite (one file, `lab.db`)
- Flask, served by waitress (headless) or werkzeug (in-app)
- reportlab and openpyxl for exports

## Layout

- `khervelab/db.py`: schema, settings and backup.
- `khervelab/logic.py`: users, booking rules, approval, cancellation and
  hours. No web code here.
- `khervelab/reports.py`: usage and cost reports, and the CSV, Excel and PDF
  exports.
- `khervelab/gui/`: the desktop app.
  - `app.py`: startup, setup and login.
  - `main_window.py`: the schedule tabs.
  - `calendar.py`: the QGraphicsView week, day and month views, with drag to
    create, move and resize.
  - `dialogs.py`: booking and account dialogs.
  - `panels.py`: requests, my bookings and reports.
  - `admin.py`: instruments, users, settings and the in-app network server.
- `khervelab/web.py`: the optional Flask routes. Every POST needs the CSRF
  token, and each request opens its own SQLite connection.
- `khervelab/server.py`: the entry point. It opens the GUI by default;
  `--serve` runs the web pages only.
- `khervelab/templates/` and `khervelab/static/`: the web pages. The
  instrument calendar is `static/khervecal.js`. It draws `/api/calendar`
  (built from `logic.slots`) and fills its booking window from
  `/api/quote` (`logic.quote`). Never duplicate a booking rule in the
  JavaScript: the web must follow the app's settings through `logic`.
  Instruments are edited only in the app; the web instrument pages are
  read-only.

## Rules

- Booking creation runs the clash check and the insert inside
  `BEGIN IMMEDIATE`, so two people cannot take one slot.
- A booking stores the price in force when it was made, so later price
  changes never alter past charges:
  - its hourly `rate`;
  - for a session booking, also `session_id` and a fixed `price` (NULL means
    charge hours × rate).

  `logic.cost` is the only place a charge is computed.
- An instrument's `booking_mode` is `free` or `sessions`. In session mode,
  users may only book exact session occurrences (`logic.occurrences`): a
  session belongs to the day it starts, and an end at or before the start is
  the next day. `logic.book_range` books every free session in a range.
- A free-time instrument has three periods (`logic.periods`):
  - daytime (Mon–Fri, `open_time`–`close_time`, `slot_minutes`);
  - evening (Mon–Fri);
  - weekend: each Sat and Sun, or with `weekend_span='whole'` one window
    from Saturday `weekend_start` to Monday `weekend_end`. Weekend slots go
    up to 72 h; daytime and evening slots up to 24 h.

  Evening and weekend each have a `*_mode` of `closed`, `block` (taken
  whole), `daytime` (daytime slot length) or `own` (`*_slot`). Windows wrap
  past midnight when end <= start, and later windows are trimmed so that
  none overlap.
- `logic.period_errors` validates a free-time booking:
  - it must cover contiguous periods;
  - its start and end must sit on the grid of their period, counted from
    the period start; a run across same-slot periods only needs a whole
    number of slots;
  - block periods are taken whole;
  - shortest and longest apply to daytime-only bookings.

  `snap_start` and `snap_end` drive drag snapping, and
  `logic.normalise_range` applies them the same way in the GUI and on the
  web.
- Lengths are stored in minutes and shown with `logic.fmt_duration`. Slot
  and shortest are at most 24 h.
- New columns go into `db.MIGRATIONS` so that existing `lab.db` files gain
  them at start-up.
- Times are lab-local wall clock (`YYYY-MM-DDTHH:MM`). Durations for billing
  go through `logic.hours`, which counts real elapsed time across clock
  changes.
- The lab manager is held only to the hard rules (no overlap, end after
  start). A manager booking for someone else (`actor_id`) is approved and
  charged at that person's rate.
- Other users' booking purposes and costs are visible only to their owner
  and the manager.
- The GUI starts with nobody logged in (`main_window.GUEST`): the schedule
  is read-only, and booking actions ask to log in and then continue
  (`switchUser(user, then)`). `gui/app.py` builds one `MainWindow` per user
  and carries the view across (`view_state` / `restore_view`).
- `issues` rows (`problem` or `down`, `end` NULL = until fixed) colour the
  calendar. A `down` issue blocks non-manager bookings in `logic.check`.
  `logic.slots` lists every bookable slot, and `CalendarView._draw_slots`
  draws them as raised cards (`paint_card`: rounded, soft shadow, gentle
  gradient), which bookings share. Closed time is a flat light background
  with no grid lines; hour lines remain only in columns without slot cards. Changed
  defaults go into `db.REPLACED_DEFAULTS`, so that labs still on the old
  default follow the new one. Bookings use the `colour_booked` setting; the
  colours are the `colour_*` settings.
- A booking is never created by a plain click. In `CalendarView`, creation
  needs a drag past `QApplication.startDragDistance()` or a press held for
  `HOLD_MS` (which arms the slot). Move and resize also ignore movement
  below that threshold.
- The account controls sit in the tab bar's corner widget. Do not put an
  expanding spacer in the toolbar: it caused a layout-dependent segfault in
  `CalendarView.drawForeground` during the tests.
- The GUI and the web pages share `lab.db` (WAL mode); the GUI refreshes
  every 30 s.

## Branching, version, tests

- Work on `dev`. Commit and push after every change, and add specific files.
  Merge into `main` only when asked.
- `khervelab/__init__.py` holds `__version__ = "<major>.<minor>"`. Bump the
  minor version in the same commit as any change the user can see.
- Run `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` before
  committing. Logic and report
  changes come with tests.
