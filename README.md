# KherveLAB

A desktop app for booking the instruments of one research lab. It runs on the
lab computer:

- **Accounts:** people log in with their own account and book time from the
  schedule.
- **Approval:** depending on how the lab manager set up each instrument, a
  booking is approved at once or waits for the manager.
- **Costs:** each instrument has hourly rates, so the manager can produce a
  usage and cost statement for any user and period.

If wanted, the app can also share booking pages on the lab network, so people
can book from their own computer's browser into the same schedule. This is
optional: the app is the main way in.

It is part of the Kherve Tools suite. An earlier, department-wide design is
kept under the git tag `v0.7-department`.

## Start it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python KherveLAB.py
```

On first launch KherveLAB asks for:

- the lab's name;
- the lab manager's account;
- optionally, a set of starting instruments (XPS, NAP-XPS, TGA/DSC,
  dilatometer, BET, glovebox). You can rename, edit or remove them, and add
  your own.

After that, every launch opens a login window. **Create an account…** in that
window lets new people register.

All data is kept in one SQLite file: `~/KherveLAB-data/lab.db`, or a folder of
your choice with `--data FOLDER`.

## The schedule

- **Tabs:** *All instruments*, then one tab per instrument, each with its own
  calendar. Each instrument tab shows your rate, whether your bookings are
  approved at once, the opening hours and the allowed booking length.
- **Views:** Day, Week and Month (Ctrl+1/2/3); ◀ ▶ and Today, or ←/→ and T.
  On the *All instruments* tab, the day view puts the instruments side by
  side, one column each.
- **Booking:** drag across empty time to book. The booking window shows the
  hours, the cost at your rate, whether the booking will be approved at once,
  and anything that breaks the instrument's rules.
- **Changing a booking:** drag it to move it, drag its bottom edge to resize
  it, and double-click it for details, editing or cancelling. If you change a
  booking on an instrument that needs approval, it goes back to the lab
  manager.
- **How bookings look:** a pending request is drawn dashed and pale. Closed
  hours are shaded. Other people's purposes and costs stay private.
- **My bookings** (Ctrl+B) lists your bookings with their status, cost and
  this month's total. **Lab ▸ My statement** saves your own PDF, Excel or CSV
  statement.

## The lab manager

The manager sees everything the users see, plus the following:

- **Requests**: a panel of the bookings and new accounts waiting for approval.
  Approve them, or reject them with a reason.
- **Instruments**: for each instrument you set:
  - **Approval mode:**
    - *Automatic*: every booking is approved at once.
    - *Automatic for trained users*: trained users book at once; everyone else
      waits for you.
    - *Manual*: every booking waits for you.
  - **Hourly rate for each user category.** The defaults are Internal,
    External academic and Industry; you can change them in Lab settings.
  - **Booking rules**: slot, shortest and longest booking, how far ahead,
    opening hours or around the clock, and weekends.
  - **Trained users.**
- **Users**: approve, disable or edit accounts, set rate categories and
  training, reset passwords, or add a co-manager.
- **Reports**: usage and costs for any period. You can filter by user or
  instrument. Save formats:
  - **PDF**: one statement page per user, ready to send, plus a summary;
  - **Excel**;
  - **CSV**.
- **Lab settings**: name, currency, time zone, rate categories, whether
  accounts need approval, and whether users see who booked a slot. Back up the
  database from here.
- **Booking from other computers** (Manage menu): starts or stops the booking
  web pages on the lab network and shows the address to hand out. People log
  in with the same accounts, and their bookings appear in the app within 30
  seconds.

The manager is held only to the hard rules (no overlaps). This lets them book
for maintenance at any time, or book on someone's behalf: that booking is
approved and charged at the person's rate.

Billing rules:

- A booking keeps the hourly rate in force when it was made, so changing a
  rate never rewrites past charges.
- Only approved bookings are charged, by the time actually elapsed.

## Without a screen

On a computer with no display, `python KherveLAB.py --serve [--port 8080]`
runs only the web pages, served by waitress.

## Tests

```bash
.venv/bin/pip install pytest pytest-qt
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

## Licence

GPL v3 or later. © 2026 Gwilherm Kerherve. FullCalendar (MIT), used by the
optional web pages, is vendored in `khervelab/static/vendor/`.
