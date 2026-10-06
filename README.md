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

After that, KherveLAB opens with **nobody logged in**:

- **Looking:** the schedule is on screen for anyone to check what's free.
- **Logging in:** **Log in** and **Create account** sit at the top right of
  the schedule.
- **Starting a booking first:** if you start a booking or open one before
  logging in, KherveLAB asks you to log in and then carries on.
- **Logged in:** the same corner shows your name (click it for your account)
  and **Log out** (Ctrl+Shift+L).
- **Changing user:** logging in or out keeps the instrument tab, date and view
  you were on.
- **Disabled accounts:** if the lab manager disables an account while it is
  logged in, it is logged out at the next refresh.

All data is kept in one SQLite file: `~/KherveLAB-data/lab.db`, or a folder of
your choice with `--data FOLDER`.

## The schedule

- **Tabs:** *All instruments*, then one tab per instrument, each with its own
  calendar. Each instrument tab shows your rate, whether your bookings are
  approved at once, the opening hours and the allowed booking length.
- **Views:** Day, Week and Month (Ctrl+1/2/3); ◀ ▶ and Today, or ←/→ and T.
  On the *All instruments* tab, the day view puts the instruments side by
  side, one column each.
- **Booking:** a plain click never books; to book, either **drag across
  empty time**, or **press and hold** on a slot for about half a second until
  it lights up, then let go. The booking window shows the
  hours, the cost at your rate, whether the booking will be approved at once,
  and anything that breaks the instrument's rules.
- **Changing a booking:** drag it to move it, drag its bottom edge to resize
  it, and double-click it for details, editing or cancelling. A small slip of
  the mouse while clicking a booking opens it rather than moving it. If you change a
  booking on an instrument that needs approval, it goes back to the lab
  manager.
- **How bookings look:** a pending request is drawn dashed and pale. Closed
  hours are shaded. Other people's purposes and costs stay private.
- **My bookings** (Ctrl+B) lists your bookings with their status, cost and
  this month's total. **Lab ▸ My statement** saves your own PDF, Excel or CSV
  statement.

## Slot colours, problems and out of order

The calendar is never blank. On an instrument's own tab, and in the All
instruments day view, every bookable slot is drawn as a box (e.g. 08:00–12:30
and 12:30–17:00 for 4.5 h slots from 08:00, or the whole evening or weekend
when that is one booking). Each box is:

- **grey** when free;
- **blue** when booked (pale and dashed while waiting for approval);
- **yellow** when a problem has been reported for that time;
- **red** when the instrument is out of order.

Closed time is hatched. The colours are set in **Lab settings ▸ Calendar
colours**, and each instrument's info line shows a legend and its current
status.

Reporting and resolving:

- **Report a problem…** (toolbar, any logged-in user) records either a
  **problem** (still usable, take care) or **out of order** (an accident, the
  machine cannot be used), from a time until fixed or until a set time, with
  details.
- **Out of order** stops new bookings for that time. The lab manager can
  still book, e.g. for the repair. Existing bookings in that time get a red
  outline.
- **Problem** slots can still be booked.
- **Manage ▸ Problems and out of order** lists reports. **Fixed now** ends
  one; **Remove report** deletes a mistake.

## Sessions

An instrument is booked in one of two ways:

- **Free time.** People choose a start and an end, in slots such as 30
  minutes, between the opening hours.
- **Fixed sessions.** People book whole sessions that the lab manager
  defines. For example, a NAP-XPS might run three sessions on weekdays:
  - Morning, 08:00–12:30 (4.5 h)
  - Afternoon, 12:30–17:00 (4.5 h)
  - Evening and overnight, 17:00–08:00 (15 h)

To set up sessions, go to **Instruments ▸ How it is booked ▸ Fixed
sessions**. You can start from a template and edit it:

- two day sessions and an evening run;
- a full day and overnight;
- 24-hour runs;
- half days.

For each session you set:

- **Name.**
- **Start and end time.** An end at or before the start runs into the next
  day.
- **Days it runs on.**
- **A fixed price for each user category**, so internal and external users
  can pay different prices. If you leave a price empty, that category pays
  the instrument's hourly rate × the session's length.

How sessions look and behave:

- **On the instrument's calendar,** each session is a labelled band, and
  everything outside the sessions is shaded closed.
- **Booking:** dragging across the calendar opens the booking window with
  every session your drag touched already ticked. The window lists the
  sessions on the chosen days with your price for each, greys out the ones
  already taken, and shows the total.
- **Each session is its own booking,** so it is approved and billed on its
  own.
- **Moving:** dragging a session booking moves it to the session it is
  dropped on.
- **The lab manager** can still block free-form time on a session instrument,
  for example for maintenance.

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
  - **How it is booked:** free time or fixed sessions (see Sessions).
  - **Hourly rate for each user category.** The defaults are Internal,
    External academic and Industry; you can change them in Lab settings.
  - **When and how it is booked** (free-time instruments): three periods,
    each with its own start and finish. An end at or before the start runs
    into the next day.
    - **Daytime (Mon–Fri)**, e.g. 08:00–17:00. It is booked in the slot
      length you set (30 min, 4.5 h, up to 24 h, in minutes or hours), or
      around the clock.
    - **Evening (Mon–Fri)**, e.g. 17:00 → 08:00. It is either closed, booked
      as **one booking for the whole evening**, booked in **the same slots
      as the daytime**, or booked in **its own slot length**.
    - **Weekend**, with the same four choices. It runs either **each day**
      (Saturday and Sunday separately, e.g. 08:00 → 08:00), or as **the whole
      weekend from Saturday to Monday**. For example, Saturday 08:00 →
      Monday 08:00 is 48 h, following a Friday evening of 17:00 → Saturday
      08:00. Weekend slots go up to 72 h, so a whole weekend can be one
      booking or, say, two 24 h slots.

    How the periods work:
    - **Slot grid:** slots are counted from the start of each period, and a
      booking is a whole number of slots. A 4.5 h daytime slot from 08:00
      gives 08:00, 12:30 and 17:00.
    - **One-booking periods:** an evening or weekend day set to one booking
      is taken whole.
    - **Crossing periods:** a booking can run from one period into the next,
      for example the afternoon slot plus the whole evening, or Friday
      evening into the weekend.
    - **Shortest and longest** apply to daytime bookings.
    - **Book ahead:** how many days ahead people can book.

    On the calendar, the evening and weekend bands are labelled with how
    they are booked, and dotted lines mark where long slots meet. Dragging
    snaps to the slots, and a click inside a one-booking period takes the
    whole period.

    With fixed sessions, only "how far ahead" applies.
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

- A booking keeps the price in force when it was made (a session price or an
  hourly rate), so changing prices never rewrites past charges.
- Only approved bookings are charged: a session at its fixed price, anything
  else by the time actually elapsed.
- Statements name each session.

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
