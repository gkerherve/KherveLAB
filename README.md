# KherveLAB

Instrument booking for a single research lab. One computer in the lab runs
KherveLAB. People open it in a web browser, either on that computer or from
their own machine at its network address. They log in with their own account
and book time on an instrument. Depending on how the lab manager set up that
instrument, the booking is approved at once or waits for the manager. Each
instrument has hourly rates, so the manager can produce a usage and cost
statement for any user and period whenever one is asked for.

It is part of the Kherve Tools suite. An earlier, department-wide design is
kept under the git tag `v0.7-department`.

## Start it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python KherveLAB.py
```

KherveLAB prints two addresses and opens a browser on the first:

```
  on this PC:     http://localhost:8080
  on the network: http://192.168.1.23:8080   (give this address to users)
```

The first visit asks you to create the lab manager's account. It can also add
some starting instruments for you.

Options:

- `--port 8080`: the port to serve on.
- `--data FOLDER`: where the data lives. The default is `~/KherveLAB-data`.
- `--host 127.0.0.1`: accept connections from this computer only.
- `--no-browser`: do not open a browser window at start.

All data is kept in one SQLite file, `lab.db`, in the data folder. **Settings
▸ Download a backup** saves a copy of it.

## For users

1. **Create an account** with your name, email, group and rate category. When
   the lab manager has switched account approval on, you can log in only once
   they approve you.
2. **Book**: open an instrument, drag across its calendar, add a purpose and
   press **Book** (or **Request approval**). The form shows your hourly rate
   and the estimated cost before you book.
3. **My bookings** lists your bookings with their status and cost, and this
   month's total. You can cancel a booking until it starts.

## For the lab manager

- **Requests** shows the bookings and new accounts waiting for you. Approve or
  reject each one, with an optional note to the user.
- **Instruments & rates**: for each instrument you set:
  - **Approval**: one of three modes.
    - *Automatic*: every booking is approved at once.
    - *Automatic for trained users*: people you have marked as trained book at
      once; everyone else waits for you.
    - *Manual*: every booking waits for you.
  - **Hourly rate for each user category.** The default categories are
    Internal, External academic and Industry; you can rename them in Settings.
    A booking keeps the rate in force when it was made, so changing a rate
    never rewrites past charges.
  - **Booking rules**: slot length, shortest and longest booking, how far
    ahead people can book, opening hours (or around the clock, for overnight
    runs) and weekends.
  - **Trained users.**
- **Users**: approve, disable or edit accounts, change a user's rate category,
  mark training, reset a password, or make someone a co-manager.
- **All bookings**: filter by date, instrument, user and status, and cancel
  any booking.
- **Reports**: usage and costs for any period. You can filter by user or
  instrument, and see totals by user, group and instrument. Download formats:
  - **PDF**: one statement page per user, ready to send, plus a summary page;
  - **Excel**;
  - **CSV**.

  Only approved bookings are charged, by the time actually elapsed (a booking
  across a clock change is billed for the real hours).
- **Settings**: lab name, currency, time zone, rate categories, whether new
  accounts need approval, and whether users see who booked each slot. Backups
  are downloaded from here too.

The lab manager is held only to the hard rules (no overlaps). This lets them
block an instrument for maintenance, or book at any hour.

## Security notes

- **Passwords** are stored as salted hashes.
- **Sessions** are signed cookies that last 12 hours.
- **Forms** carry a token against cross-site request forgery.
- **Network**: KherveLAB is meant for a lab's local network. Do not expose it
  directly to the internet. If people need it from outside, put it behind the
  institution's VPN.

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

## Licence

GPL v3 or later. © 2026 Gwilherm Kerherve. FullCalendar (MIT) is vendored in
`khervelab/static/vendor/`.
