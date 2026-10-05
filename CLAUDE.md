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
- `khervelab/templates/` and `khervelab/static/`: the web pages.

## Rules

- Booking creation runs the clash check and the insert inside
  `BEGIN IMMEDIATE`, so two people cannot take one slot.
- A booking stores the hourly rate in force when it was made, so later rate
  changes never alter past charges.
- Times are lab-local wall clock (`YYYY-MM-DDTHH:MM`). Durations for billing
  go through `logic.hours`, which counts real elapsed time across clock
  changes.
- The lab manager is held only to the hard rules (no overlap, end after
  start). A manager booking for someone else (`actor_id`) is approved and
  charged at that person's rate.
- Other users' booking purposes and costs are visible only to their owner
  and the manager.
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
