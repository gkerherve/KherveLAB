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
instruments day view, every bookable slot is a raised card with rounded
corners and a soft shadow. Examples are 09:00–11:00, 11:00–13:00, … for 2 h
slots, or the whole evening or weekend when that is one booking. A card is:

- **near-white** when free;
- **blue** when booked (pale with a dashed edge while waiting for approval);
- **amber** when a problem has been reported for that time;
- **red** when the instrument is out of order.

Closed time is a flat, calm grey-blue background behind the cards, with no
lines across it. The hours are marked beside the time labels. All five
colours (free, closed, booked, problem and out of order) are set in **Lab
settings ▸ Calendar colours**. Each instrument tab shows a legend under its
info line, and its current status.

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

## Booking for someone else: lab manager and super users

Someone can come to the lab manager, or to a **super user**, and have time
booked for them. There are three roles, set in **Users**:

| Role | Can |
|---|---|
| **User** | book and manage their own bookings |
| **Super user** | do everything a user can, **for anyone**: book, move, cancel and reassign others' bookings (not yet started), and see anyone's bookings. Bookings follow that person's rules, approval and price. No management rights. |
| **Lab manager** | everything, including instruments, users, settings and reports. A manager's booking for someone is approved at once and held only to the hard rules (no overlap), at that person's price. |

In the app:

- **Booking for someone:** the booking window has a searchable **For**
  list (type part of a name). The price and the approval line follow the
  person chosen.
- **Changing who a booking belongs to:** in a booking's details, **Booked
  for** gives it to someone else. It is re-priced at their category.
- **Someone's bookings…** (toolbar), or **Users ▸ Bookings…**, opens one
  person's bookings to open, change or cancel.

On the web pages it is the same:

- **Booking:** a **For** list in the booking window. Changing it
  re-prices and re-checks for that person.
- **A booking's popup:** **Booked for … Give to** reassigns it.
- **My bookings:** has a **Whose bookings** picker.

## The lab manager

The manager sees everything the users see, plus the following:

- **Requests**: a panel of the bookings and new accounts waiting for approval.
  Approve them, or reject them with a reason.
- **Instruments**: **Add instrument** and **Remove instrument…**.
  - **Removing an instrument with no bookings** deletes it.
  - **Removing one with bookings** gives a choice. **Retire** hides it and
    stops all booking, but keeps its bookings and charges in reports.
    **Delete with its bookings** removes them too, and asks for a second
    confirmation.

  For each instrument you set:
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
- **Reports** (Ctrl+E): finance and usage for any period (last month, this
  year, a custom range…). You can filter by user or instrument.
  - **Overview:** the headline figures (revenue, hours booked and the share
    of bookable time, active and new users, money waiting for approval,
    cancellations, out-of-order hours), with the key charts.
  - **Finance:** revenue by month (bar), by instrument and by user (bars),
    by user category, by group and by price basis (session price or hourly
    rate) (doughnuts), plus a table per group.
  - **Usage:** hours booked by month (line), a weekday × hour heat map of
    when the lab is used, the daytime/evening/weekend split per instrument
    (stacked bars), what happened to requests (approved, pending, rejected,
    cancelled), and how far ahead people book.
  - **Instruments:** utilisation (booked ÷ bookable hours), downtime and
    problems per instrument, and a table with bookings, users, hours,
    revenue and downtime.
  - **By user** and **Bookings:** the statement tables.

  Save formats:
  - **Lab report PDF**: the headline figures, every chart and the tables;
  - **Lab report Excel**: a sheet per chart with its numbers and a native
    Excel chart, ready to reuse;
  - **PDF statement**: one page per user, ready to send, plus a summary;
  - **Excel** and **CSV** of the bookings.

  The web reports page shows the same figures and charts and offers the
  same downloads. Revenue only counts approved bookings, at the price fixed
  when each was made.
- **Lab settings**: name, currency, time zone, rate categories, whether
  accounts need approval, and whether users see who booked a slot. Back up the
  database from here.
- **Import from PPMS** (Manage menu): bring a lab's users, instruments,
  prices, training and booking history over from PPMS. In PPMS, export
  whichever you have as CSV or Excel: systems, users, prices, rights,
  bookings or usage (with the amount charged), and incidents. Then add the
  files in the import window:
  - **Matching:** KherveLAB recognises each file and its columns, and shows
    a preview. You can correct any match, and choose whether dates are day
    first.
  - **Dry run:** **Check** shows exactly what the import would create and
    which rows it would skip, and why. Nothing is saved until **Import**.
  - **Safe:** a backup of the lab is saved just before importing, and
    importing the same files again skips what is already there.
  - **Instruments:** say which of your instruments each PPMS system is
    (e.g. "XPS / Bay 2" → XPS), or let it create a new one.
  - **The detailed list of sessions** (Reports ▸ detailed list in PPMS)
    imports on its own. It names people rather than giving logins, so:
    - accounts are made from the names, with the group (PI) and
      affiliation;
    - a users export imported later gives those accounts their PPMS login
      and email, so people can claim them;
    - a shared session is one booking per person, each with its own
      account and charge;
    - a cancellation PPMS still charged counts as revenue (marked
      "Cancelled late, charged").

    The totals match PPMS's own.
  - **Any dates:** history from any time imports, past or future, whatever
    the date the lab was set up in KherveLAB. Imported accounts are dated
    from their first PPMS booking, and importing again corrects accounts
    imported before this rule. In Reports, **All time** covers everything.
  - **What comes across:**
    - bookings keep the amount PPMS charged, and the reports count them;
    - a project or account code goes into the booking's purpose;
    - PPMS user types are matched to your rate categories (an unknown type
      becomes a new category);
    - autonomous and superuser rights become "trained";
    - new instruments start as trained-only auto, round the clock, so set
      their hours and rules afterwards.
  - **Passwords** cannot come from PPMS. The first time an imported person
    logs in (in the app or on the web), they give the email address on
    record and choose a password. People without an email address need the
    lab manager to set one in **Users**.
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

## The web pages follow the app exactly

When the web pages are shared (Manage ▸ Booking from other computers), each
instrument's page draws **the same calendar as the app**:

- **Same look:** the same raised cards, colours and closed background,
  with Week and Day views (Day on a phone).
- **Same slots:** the slots come from the app's own rules, through
  `/api/calendar`.
- **Same gestures:** drag across the slots you want, or press and hold
  one until it lights up. A plain click does nothing.
- **Same checks:** the booking window is filled from the same rules as the
  app's booking window (`/api/quote`). It shows the slots or sessions, the
  price, whether approval is needed, and any rule a choice breaks
  (including out of order), so the web can only book what the instrument
  settings allow.
- **Same snapping:** a booking lands exactly where the app would put it.

Instruments are set up only in the app. The web shows each instrument's
settings read-only, so the app and the web can never drift apart.

## Let Claude run the lab (MCP)

KherveLAB has an MCP server, so Claude Desktop or Claude Code can act as a
lab manager. For example, you can paste screenshots of PPMS pages (user
lists, price tables, booking histories) and ask Claude to enter what they
show.

To set it up, open **Manage ▸ Connect to Claude…**, choose which manager
Claude acts as, and then either:

- click **Add to Claude Desktop**, then restart Claude Desktop; or
- copy the **Claude Code** command and run it once in a terminal.

Claude then has tools to:

- **Look:** the lab overview, instruments, users, bookings, pending
  requests, problems, a booking quote, and the finance and usage report.
- **Set up:** add and change instruments (retire one with `active=0`), set
  rates, add and change users, mark training, and change lab settings.
- **Run the lab:** book for someone, approve or reject requests, cancel
  bookings, and report and resolve problems.
- **Move from PPMS:**
  - `record_past_bookings` takes bookings read from a screen, with the
    amount charged;
  - `preview_ppms_import` and `import_ppms` handle export files.

Its limits:

- **Same rules as the app:** there are no tools that delete anything.
- **Users without a password:** they choose one at their first log-in.
- **Changes are logged:** every change is appended to `mcp-log.jsonl` in
  the data folder.
- **Approval:** Claude asks you before each change unless you allow a tool
  permanently.

It needs no extra packages and no running app: it works on `lab.db`
directly, and an open app shows the changes within 30 seconds. To start it
by hand:

```bash
.venv/bin/python -m khervelab.mcp_server --data ~/KherveLAB-data --as manager
```

## Without a screen

On a computer with no display, `python KherveLAB.py --serve [--port 8080]`
runs only the web pages, served by waitress.

## Tests

```bash
.venv/bin/pip install pytest pytest-qt
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

## Licence

GPL v3 or later. © 2026 Gwilherm Kerherve.
