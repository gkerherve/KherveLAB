# CLAUDE.md — working conventions for KherveLAB

KherveLAB is a small web app for booking the instruments of **one lab**:

- **Where it runs:** one computer in the lab runs the server
  (`python KherveLAB.py`), and people use it in a browser on the local
  network.
- **Approval:** each instrument is set to auto, trained-only auto or manual
  approval.
- **Costs:** every instrument has hourly rates per user category, and the
  manager exports usage and cost statements (PDF, Excel, CSV).

Keep it simple. The department-wide design (Git-backed repository, published
site, GitHub requests, logbook, data index) was deliberately dropped; it is
kept at the git tag `v0.7-department`. Do not bring those parts back unless
the user asks.

Stack:

- Python 3.10+
- Flask, served by waitress
- SQLite (one file, `lab.db`)
- reportlab and openpyxl for exports
- FullCalendar, vendored

## Layout

- `khervelab/db.py`: schema, settings and backup.
- `khervelab/logic.py`: users, booking rules, approval, cancellation and
  hours. No web code here.
- `khervelab/reports.py`: usage and cost reports, and the CSV, Excel and PDF
  exports.
- `khervelab/web.py`: the Flask routes. Every POST needs the CSRF token, and
  each request opens its own SQLite connection.
- `khervelab/server.py`: the launcher. It prints the LAN address and serves
  with waitress.
- `khervelab/templates/` and `khervelab/static/`: pages and styles.

## Rules

- Booking creation runs the clash check and the insert inside
  `BEGIN IMMEDIATE`, so two people cannot take one slot.
- A booking stores the hourly rate in force when it was made, so later rate
  changes never alter past charges.
- Times are lab-local wall clock (`YYYY-MM-DDTHH:MM`). Durations for billing
  go through `logic.hours`, which counts real elapsed time across clock
  changes.
- The lab manager is held only to the hard rules (no overlap, end after
  start).

## Branching, version, tests

- Work on `dev`. Commit and push after every change, and add specific files.
  Merge into `main` only when asked.
- `khervelab/__init__.py` holds `__version__ = "<major>.<minor>"`. Bump the
  minor version in the same commit as any change the user can see.
- Run `.venv/bin/python -m pytest -q` before committing. Logic and report
  changes come with tests.
