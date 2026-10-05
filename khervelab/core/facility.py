"""Facility service: booking mutations, each one a single readable commit.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from . import logbook, schedule
from .config import FacilityConfig, booking_from_dict, booking_yaml, load_config, user_to_dict
from .models import Booking, Instrument, User
from .repo import ConflictError, FacilityRepo
from .yamlio import dump_yaml, loads_yaml


class BookingRejected(ValueError):
    def __init__(self, violations: list[schedule.Violation]):
        self.violations = violations
        super().__init__("; ".join(v.message for v in violations))


class FacilityService:
    def __init__(self, repo: FacilityRepo):
        self.repo = repo
        self.cfg: FacilityConfig = load_config(repo.path)

    # -- convenience ------------------------------------------------------
    @property
    def tz(self):
        return self.cfg.facility.tz

    @property
    def instruments(self) -> dict[str, Instrument]:
        return self.cfg.instruments

    @property
    def users(self) -> dict[str, User]:
        return self.cfg.users

    def reload(self) -> None:
        self.cfg = load_config(self.repo.path)

    def bookings(self, instrument: str | None = None) -> list[Booking]:
        out = [b for b in self.cfg.bookings.values()
               if instrument is None or b.instrument == instrument]
        return sorted(out, key=lambda b: (b.start, b.instrument))

    def bookings_between(self, start: datetime, end: datetime,
                         instruments: set[str] | None = None) -> list[Booking]:
        return [b for b in self.bookings()
                if b.start < end and start < b.end
                and (instruments is None or b.instrument in instruments)]

    def bookings_on(self, day: date, instruments: set[str] | None = None) -> list[Booking]:
        s = datetime(day.year, day.month, day.day, tzinfo=self.tz)
        return self.bookings_between(s, s + timedelta(days=1), instruments)

    def check(self, b: Booking, ignore: Booking | None = None,
              now: datetime | None = None) -> list[schedule.Violation]:
        inst = self.instruments.get(b.instrument)
        if inst is None:
            return [schedule.Violation("instrument", schedule.BLOCK,
                                       f"unknown instrument {b.instrument}")]
        return schedule.check(b, inst, self.bookings(b.instrument), self.tz,
                              now or datetime.now(timezone.utc),
                              user=self.users.get(b.user), ignore=ignore,
                              downtime=logbook.downtime(self.cfg.logs, inst.id, self.tz))

    def path_of(self, b: Booking) -> str:
        return b.path(self.tz).as_posix()

    # -- mutations --------------------------------------------------------
    def _prepare(self, b: Booking, ignore: Booking | None, override: bool,
                 now: datetime | None) -> Booking:
        inst = self.instruments[b.instrument]
        b = schedule.snap_booking(b, inst, self.tz)
        violations = [v for v in self.check(b, ignore=ignore, now=now) if v.rule != "granularity"]
        if schedule.blocking(violations):
            raise BookingRejected(schedule.blocking(violations))
        warnings = [v for v in violations if not v.blocking]
        if warnings and not override:
            raise BookingRejected(warnings)
        if warnings:
            b = replace(b, override=tuple(sorted(set(b.override) | {v.rule for v in warnings})))
        return b

    def add_booking(self, b: Booking, override: bool = False,
                    now: datetime | None = None) -> Booking:
        if b.created is None:
            b = replace(b, created=datetime.now(timezone.utc).astimezone(self.tz)
                        .replace(microsecond=0))
        b = self._prepare(b, None, override, now)
        rel = self.path_of(b)
        if (self.repo.path / rel).exists():
            raise BookingRejected([schedule.Violation(
                "overlap", schedule.BLOCK, f"a booking already starts at that time ({rel})")])
        self.repo.write_file(rel, booking_yaml(b))
        self.repo.commit(f"book {b.label(self.tz)}", [rel])
        self.cfg.bookings[rel] = b
        return b

    def add_recurring(self, b: Booking, every_days: int, count: int,
                      override: bool = False, now: datetime | None = None) -> list[Booking]:
        """All occurrences in one commit, or none if any is rejected."""
        created = datetime.now(timezone.utc).astimezone(self.tz).replace(microsecond=0)
        items = schedule.expand_recurring(replace(b, created=b.created or created),
                                          every_days, count, self.tz)
        prepared, paths = [], []
        for item in items:
            p = self._prepare(item, None, override, now)
            for q in prepared:
                if p.overlaps(q):
                    raise BookingRejected([schedule.Violation(
                        "overlap", schedule.BLOCK, "occurrences overlap each other")])
            prepared.append(p)
        for p in prepared:
            rel = self.path_of(p)
            self.repo.write_file(rel, booking_yaml(p))
            paths.append(rel)
        first = prepared[0]
        self.repo.commit(f"book {first.label(self.tz)} (+{len(prepared) - 1} repeats, "
                         f"every {every_days} d)", paths)
        for rel, p in zip(paths, prepared):
            self.cfg.bookings[rel] = p
        return prepared

    def update_booking(self, old: Booking, new: Booking, override: bool = False,
                       now: datetime | None = None) -> Booking:
        new = replace(new, created=old.created)
        new = self._prepare(new, old, override, now)
        old_rel, new_rel = self.path_of(old), self.path_of(new)
        if new_rel != old_rel and (self.repo.path / new_rel).exists():
            raise BookingRejected([schedule.Violation(
                "overlap", schedule.BLOCK, f"a booking already starts at that time ({new_rel})")])
        if new_rel != old_rel:
            self.repo.delete_file(old_rel)
        self.repo.write_file(new_rel, booking_yaml(new))
        verb = "move" if (old.start, old.end, old.instrument) != (new.start, new.end, new.instrument) \
            else "edit"
        msg = f"{verb} {old.label(self.tz)}" + (f" -> {new.label(self.tz)}" if verb == "move" else "")
        self.repo.commit(msg, sorted({old_rel, new_rel}))
        self.cfg.bookings.pop(old_rel, None)
        self.cfg.bookings[new_rel] = new
        return new

    def delete_booking(self, b: Booking) -> None:
        rel = self.path_of(b)
        self.repo.delete_file(rel)
        self.repo.commit(f"cancel {b.label(self.tz)}", [rel])
        self.cfg.bookings.pop(rel, None)

    def save_user(self, u: User, message: str | None = None) -> None:
        rel = f"users/{u.id}.yaml"
        self.repo.write_file(rel, dump_yaml(user_to_dict(u)))
        self.repo.commit(message or f"user {u.id}", [rel])
        self.cfg.users[u.id] = u

    def next_user_id(self) -> str:
        n = 1
        while f"u-{n:04d}" in self.users:
            n += 1
        return f"u-{n:04d}"

    # -- publishing -------------------------------------------------------
    def publish(self, statuses=None) -> bool:
        """Regenerate docs/ and commit it if anything changed. Returns
        whether a commit was made."""
        from ..publish.build import SITE_DIR, build_site
        head = self.repo.git.head.commit
        as_of = datetime.fromtimestamp(head.committed_date, timezone.utc)
        if head.message.startswith("publish calendar"):
            parents = head.parents
            as_of = datetime.fromtimestamp(parents[0].committed_date, timezone.utc) \
                if parents else as_of
        build_site(self.cfg, self.repo.path / SITE_DIR, as_of,
                   statuses if statuses is not None else self.statuses())
        changed = self.repo.git.git.status("--porcelain", "--", SITE_DIR).strip()
        if not changed:
            return False
        self.repo.commit("publish calendar", [SITE_DIR])
        return True

    def statuses(self) -> dict:
        from ..publish.build import InstrumentStatus
        out = {}
        for inst in self.instruments.values():
            st = logbook.instrument_status(inst, self.cfg.logs)
            out[inst.id] = InstrumentStatus(st.available, st.text)
        return out

    # -- samples ----------------------------------------------------------
    @property
    def samples(self):
        return self.cfg.samples

    def add_sample(self, name: str, composition: str = "", preparation: str = "", owner: str = "",
                   parent: str = "", notes: str = "", derived_from_file: str = "",
                   created: date | None = None):
        from . import samples as sm
        created = created or datetime.now(self.tz).date()
        if parent and parent not in self.samples:
            raise ValueError(f"unknown parent sample {parent}")
        sid = sm.next_sample_id(self.samples, created.year)
        s = sm.Sample(sid, name, composition, preparation, owner, parent, created, notes,
                      derived_from_file)
        rel = sm.sample_path(sid)
        self.repo.write_file(rel, sm.sample_yaml(s))
        self.repo.commit(f"sample {sid}" + (f" from {parent}" if parent else "")
                         + f": {name[:50]}", [rel])
        self.cfg.samples[sid] = s
        return s

    def update_sample(self, s) -> None:
        from . import samples as sm
        rel = sm.sample_path(s.id)
        self.repo.write_file(rel, sm.sample_yaml(s))
        self.repo.commit(f"edit sample {s.id}", [rel])
        self.cfg.samples[s.id] = s

    def bookings_for_sample(self, sid: str) -> list[Booking]:
        return [b for b in self.bookings() if sid in b.samples]

    # -- logbook ----------------------------------------------------------
    def add_log(self, entry: "logbook.LogEntry") -> "logbook.LogEntry":
        if entry.instrument not in self.instruments:
            raise ValueError(f"unknown instrument {entry.instrument}")
        rel = logbook.log_path(entry.instrument, entry.time, self.tz)
        self.repo.write_file(rel, logbook.month_file_text(self.repo.read_file(rel), entry))
        self.repo.commit(logbook.commit_message(entry), [rel])
        self.cfg.logs = sorted([*self.cfg.logs, entry], key=lambda e: e.time)
        return entry

    def log(self, instrument: str, type: str, text: str = "", user: str = "",
            when: datetime | None = None, **kw) -> "logbook.LogEntry":
        when = when or datetime.now(self.tz).replace(second=0, microsecond=0)
        if type == "fault":
            kw.setdefault("status", "open")
            kw.setdefault("fault", logbook.new_id())
        return self.add_log(logbook.LogEntry(id=logbook.new_id(), instrument=instrument, type=type,
                                             time=when, user=user, text=text, **kw))

    def update_fault(self, fault: "logbook.Fault", status: str, text: str = "",
                     blocking: bool | None = None, back=None, user: str = ""):
        return self.log(fault.instrument, "fault_update", text, user=user, fault=fault.id,
                        status=status, blocking=fault.blocking if blocking is None else blocking,
                        back=back)

    # -- sync -------------------------------------------------------------
    def sync(self):
        st = self.repo.sync()
        self.reload()
        return st

    def resolve_conflicts(self, choices: dict[str, str]) -> None:
        """Settle the files a pull could not merge: path -> 'ours' | 'theirs'.

        Re-runs the merge, takes the chosen side for each conflicted path and
        lets Git merge everything else as usual, then commits once."""
        up = f"origin/{self.repo.branch}"
        g = self.repo.git.git
        try:
            g.merge("--no-edit", "--no-commit", up)
        except Exception:
            pass
        unmerged = g.diff("--name-only", "--diff-filter=U").splitlines()
        missing = [p for p in unmerged if p not in choices]
        if missing:
            g.merge("--abort")
            raise ValueError(f"no choice given for {', '.join(missing)}")
        for rel in unmerged:
            side = choices[rel]
            if side not in ("ours", "theirs"):
                g.merge("--abort")
                raise ValueError(side)
            text = self.repo._stage(2 if side == "ours" else 3, rel)
            if text is None:
                g.rm("-q", "--cached", "--ignore-unmatch", "--", rel)
                self.repo.delete_file(rel)
            else:
                self.repo.write_file(rel, text if text.endswith("\n") else text + "\n")
                g.add("--", rel)
        summary = ", ".join(f"{p}: keep {c}" for p, c in sorted(choices.items()) if p in unmerged)
        g.commit("--no-verify", "-m", f"resolve {summary}" if summary else "merge")
        self.reload()

    def find_clashes(self) -> list[tuple[Booking, Booking]]:
        """Overlapping bookings on one instrument — what a clean merge of two
        different slot files can still leave behind."""
        out = []
        for inst in self.instruments:
            items = self.bookings(inst)
            for i, a in enumerate(items):
                for b in items[i + 1:]:
                    if b.start >= a.end:
                        break
                    if a.overlaps(b):
                        out.append((a, b))
        return out

    @staticmethod
    def parse_conflict_side(text: str | None, path: str) -> Booking | None:
        if text is None:
            return None
        return booking_from_dict(loads_yaml(text, path), path)


__all__ = ["FacilityService", "BookingRejected", "ConflictError"]
