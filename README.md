# KherveLAB

Instrument booking and facility management for shared research labs, part of the
Kherve Tools suite. Shared state lives in a Git repository of plain YAML files.
No server to maintain: every booking change is one readable commit, and the
history is the audit trail.

It follows the phases of the KherveLab build plan.

## Running

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python KherveLAB.py
```

On first launch, choose one of these:

- **Create a new facility.** Copies the bundled template, which holds the full
  Department of Materials instrument catalogue, into a new Git repository. A
  remote URL is optional.
- **Open an existing working copy.**
- **Clone a facility repository** from a URL.

You can also pass a facility folder on the command line: `python KherveLAB.py ~/KherveLAB/facility`.

## What it does

- **Week view**: days across, 30-minute rows, with the current time marked.
  Bookings for several instruments sit side by side.
- **Day view**: one column per selected instrument, so you can see what is running today.
- **Month view**: a density overview. Click a day to open it in Day view.
- **Creating and editing**: drag on an empty slot to create a booking. Drag a
  booking to move it (also to another day, or to another instrument in Day view),
  drag its bottom edge to resize it, and double-click it to edit. Right-click a
  booking to delete it.
- **Rules engine** (`khervelab/core/schedule.py`): checks bookable hours,
  minimum and maximum duration, slot snapping, overlap, the advance window,
  the per-user concurrent limit and training permissions. Only hard clashes
  block a booking. Warnings can be overridden, and an override is recorded in
  the booking file (`override: [permission]`).
- **Recurring bookings** are expanded into individual files in one commit.
  They are never stored as a rule.
- **Sync**: pulls and pushes every 5 minutes, and 3 seconds after a change.
  When offline, commits queue locally and the status bar shows how many are
  waiting.
- **Conflicts**: if two people edit the same slot file, you get a dialog that
  shows both versions side by side. If two different files overlap after a
  merge, you get a dialog to keep one of them. In both cases the
  earliest-created booking is the default.
- **Keyboard**: ←/→ move the date, T jumps to today, and 1/2/3 switch views
  (when the calendar has focus). Ctrl+N creates a new booking and Ctrl+R syncs.

## Published calendar (Phase 2)

The app regenerates a static site in `docs/` and commits it. This happens 3
seconds after any change, or when you click **Publish now**.

To serve the site, turn on GitHub Pages for the facility repository with
**Deploy from a branch ▸ `main` ▸ `/docs`**. GitHub Pages can only serve a
branch from its root or from `/docs`, which is why the folder is not called
`site/`.

The site contains:

- `index.html`, the whole facility, with instrument filters;
- `<instrument>/index.html`, a bookmarkable page for each instrument;
- `bookings.json`, the published data;
- `calendar.ics` for the whole facility and one for each instrument. Subscribe
  to them in Outlook with **Add calendar ▸ Subscribe from web**.

FullCalendar 6 (MIT) is vendored, and the data is also written to
`assets/data.js`. The page therefore works from GitHub Pages and from a local
`file://` path, with no CDN and no server.

Only the instrument, the time, the anonymous display string and the kind of
booking are published. Notes and samples never are.

## Booking requests (Phase 3)

To turn requests on:

1. Set `github: owner/repo` in `facility.yaml`.
2. Store a fine-grained token with **Issues: read and write** permission
   (File ▸ GitHub token…). It is kept in the system keychain.

On the published calendar, **Request a slot** opens a pre-filled GitHub issue
form (`.github/ISSUE_TEMPLATE/booking-request.yml`). Selecting a time on an
instrument's page pre-fills the date, the start and the duration.

The app polls open issues labelled `booking-request` every 5 minutes. It parses
each one, accepting hand-edited bodies too, and runs the booking rules against
the live calendar. The **Requests** panel then lets you:

- **Approve**: books the slot, comments the confirmed slot and closes the issue;
- **Decline**: comments your reason and closes the issue;
- **Propose another time**: comments the first free slot of the same length.

GitHub users are mapped to anonymous user ids through the `github` field of
`users/*.yaml`. An unknown author prompts you to create a user.

The GitHub client uses only the standard library. ETags keep repeated polls
off the rate limit.

## Training records (Phase 4)

**People ▸ People and training…** keeps each person's personal details and
training records. All of it stays in a SQLite database on this computer:
`~/.khervelab/local.db`, or the folder set in `KHERVELAB_HOME`.

What is stored where:

- **On this computer only.** The name, email, department, group, supervisor,
  training dates, assessor, assessment notes and evidence files. Each of these
  columns is encrypted with Fernet. The key is created on first run and kept
  in the system keychain. Evidence files are stored encrypted in
  `~/.khervelab/evidence/`.
- **In the repository.** Only the anonymous id, the display string, the
  GitHub username and the list of instruments the person may book.

Saving a training record updates that list and commits it. Training that
lapses is revoked automatically at start-up. The desktop app shows real names,
because it resolves the anonymous ids locally. Nothing else can.

The **People** menu also has:

- **Training expiring soon**: everyone whose training lapses in the next 90
  days, with a reminder-email draft (all recipients in BCC);
- **Export record (PDF)**: one person's full record, for an audit or a
  leaving researcher;
- **Back up / Restore**: a zip holding the database, the evidence files and
  the key. The key is wrapped with your passphrase (PBKDF2-SHA256), so the
  backup also restores on a new machine.

The backup prompt is insistent on purpose. It appears every 10th launch, and
whenever the last backup is more than 30 days old.

## The facility repository

```
facility.yaml              name, organisation, timezone
instruments/<id>.yaml      one file per instrument: rules, colour, consumables, maintenance
users/u-0001.yaml          anonymous id, display string, permissions — no names or emails
bookings/<inst>/<YYYY>/<MM>/<inst>-<YYYY-MM-DD>-<HHMM>.yaml
```

A booking's filename encodes its instrument and its local start time. Two people
booking the same slot therefore edit the same file, and Git raises a real conflict.
A commit is refused if any staged file contains something that looks like an
email address.

## Instrument catalogue

The template has 48 instruments (`khervelab/data/facility_template/instruments/`),
generated from `tools/build_catalogue.py`.

- **Listed by the Department of Materials** (`source: materials-website`):
  - Advanced Photoelectron Spectroscopy Laboratory: XPS, NAP-XPS with UPS and LEED.
  - EM facility: Zeiss Gemini 1525 and Sigma 300, FEI Quanta ESEM, JEOL 6010LA,
    TFS Helios 5 CX, Zeiss Auriga, JEOL 2100Plus and 2100F, TFS Talos.
  - Cryo microscopy, I(CM)²: TFS Spectra 300, Helios Hydra PFIB, Cameca LEAP 5000 XR.
  - Surface analysis: IONTOF ToF-SIMS/LEIS, FIB-SIMS, Zygo NewView 200.
  - XRD: 2× Empyrean, 2× X'Pert MRD, X'Pert MPD, 2× Bruker D2.
  - Thermal analysis: Netzsch STA 449 C and F5, DIL 402 E, plus the CASC
    DTA/TGA, laser flash and dilatometer.
  - AFM: Bruker Innova, Asylum MFP-3D.
  - Royce: confocal microscope, electrochemical MS, sputter system.
- **Added** (`source: added`): BET gas sorption, Raman, FTIR, UV-Vis-NIR,
  ellipsometer, nanoindenter, universal testing machine, potentiostat/EIS,
  stylus profilometer, optical microscope, Ar glovebox, sputter coater and PIPS.

Edit the catalogue in `tools/build_catalogue.py`, then run
`python tools/build_catalogue.py`. To change a facility that is already
running, edit its own `instruments/*.yaml` files.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Everything in `khervelab/core/` is pure Python with no Qt, and is tested
headlessly. The tests cover:

- every booking rule, including both daylight-saving transitions;
- two working copies sharing one bare repository, including real conflicts.

pytest-qt tests cover the window, all three views, drag-to-create and the
booking dialog.

## Licence

GPL v3 or later. © 2026 Gwilherm Kerherve.
