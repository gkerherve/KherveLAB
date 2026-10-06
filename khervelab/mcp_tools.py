"""The tools Claude uses to run the lab over MCP, as the lab manager.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Every tool goes through logic, ppms and analytics like the app does, so
Claude is held to the same rules as a manager at the keyboard. There is
deliberately no tool that deletes a user, an instrument or a booking:
instruments are retired, accounts disabled and bookings cancelled, all of
which can be undone in the app. Each change is appended to mcp-log.jsonl
in the data folder.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import analytics, db, logic, ppms

NOTE = "Recorded by Claude (MCP)"


class ToolError(ValueError):
    pass


# -- helpers ----------------------------------------------------------------------------

def _row(r) -> dict:
    return {k: r[k] for k in r.keys()} if r is not None else None


def _when(v, what: str) -> datetime:
    try:
        return logic.parse(str(v))
    except (ValueError, TypeError):
        raise ToolError(f"{what}: cannot read {v!r}; use YYYY-MM-DDTHH:MM (lab time)")


def _day(v, what: str) -> date:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise ToolError(f"{what}: cannot read {v!r}; use YYYY-MM-DD")


def instrument(conn, ref):
    if ref in (None, ""):
        raise ToolError("which instrument?")
    if isinstance(ref, int) or str(ref).isdigit():
        r = conn.execute("SELECT * FROM instruments WHERE id=?", (int(ref),)).fetchone()
    else:
        r = conn.execute("SELECT * FROM instruments WHERE lower(name)=lower(?)",
                         (str(ref).strip(),)).fetchone()
    if r is None:
        names = [x[0] for x in conn.execute("SELECT name FROM instruments ORDER BY name")]
        raise ToolError(f"no instrument {ref!r}; the instruments are: {', '.join(names)}")
    return r


def user(conn, ref):
    if ref in (None, ""):
        raise ToolError("which user?")
    if isinstance(ref, int) or str(ref).isdigit():
        rows = conn.execute("SELECT * FROM users WHERE id=?", (int(ref),)).fetchall()
    else:
        s = str(ref).strip()
        rows = (conn.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (s,)).fetchall()
                or conn.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND email!=''",
                                (s,)).fetchall()
                or conn.execute("SELECT * FROM users WHERE lower(full_name)=lower(?)",
                                (s,)).fetchall())
    if len(rows) > 1:
        raise ToolError(f"{ref!r} matches several users ({', '.join(r['username'] for r in rows)});"
                        " use the username")
    if not rows:
        raise ToolError(f"no user {ref!r}; search with list_users")
    return rows[0]


def _user_out(u) -> dict:
    d = _row(u)
    d.pop("password_hash", None)
    d["password_set"] = bool(u["password_hash"])
    return d


def _booking_out(conn, b) -> dict:
    return {"id": b["id"], "instrument": b["iname"], "user": b["username"],
            "full_name": b["full_name"], "start": b["start"], "end": b["end"],
            "status": b["status"], "purpose": b["purpose"], "note": b["note"],
            "cost": logic.cost(conn, b) if b["status"] == "approved" else None,
            "price_basis": logic.price_label(conn, b)}


_HHMM = re.compile(r"^([01]\d|2[0-4]):[0-5]\d$")
_INSTRUMENT_FIELDS = {
    "name": str, "description": str, "location": str, "colour": str, "active": int,
    "approval": str, "booking_mode": str, "slot_minutes": int, "min_minutes": int,
    "max_minutes": int, "max_days_ahead": int, "open_time": str, "close_time": str,
    "evening_mode": str, "evening_start": str, "evening_end": str, "evening_slot": int,
    "weekend_mode": str, "weekend_start": str, "weekend_end": str, "weekend_slot": int,
    "weekend_span": str,
}


def _check_instrument(changes: dict) -> dict:
    out = {}
    for k, v in changes.items():
        if k not in _INSTRUMENT_FIELDS:
            raise ToolError(f"unknown instrument field {k!r}; allowed: "
                            f"{', '.join(_INSTRUMENT_FIELDS)}")
        try:
            v = _INSTRUMENT_FIELDS[k](v)
        except (TypeError, ValueError):
            raise ToolError(f"{k}: {v!r} is not valid")
        if k == "approval" and v not in logic.APPROVAL_LABELS:
            raise ToolError(f"approval must be one of {', '.join(logic.APPROVAL_LABELS)}")
        if k == "booking_mode" and v not in ("free", "sessions"):
            raise ToolError("booking_mode must be free or sessions")
        if k.endswith("_mode") and k != "booking_mode" and v not in logic.PERIOD_MODES:
            raise ToolError(f"{k} must be one of {', '.join(logic.PERIOD_MODES)}")
        if k == "weekend_span" and v not in ("daily", "whole"):
            raise ToolError("weekend_span must be daily or whole")
        if (k.endswith("_time") or k.endswith("_start") or k.endswith("_end")) \
                and not _HHMM.match(v):
            raise ToolError(f"{k} must be HH:MM")
        if k in ("slot_minutes", "min_minutes", "evening_slot") and not 5 <= v <= 1440:
            raise ToolError(f"{k} must be 5 to 1440 minutes")
        if k == "weekend_slot" and not 5 <= v <= 4320:
            raise ToolError("weekend_slot must be 5 to 4320 minutes (72 h)")
        if k == "max_minutes" and not 5 <= v <= 10080:
            raise ToolError("max_minutes must be 5 to 10080")
        if k == "max_days_ahead" and not 0 <= v <= 3650:
            raise ToolError("max_days_ahead must be 0 to 3650")
        if k == "active" and v not in (0, 1):
            raise ToolError("active must be 0 or 1")
        if k == "colour" and not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ToolError("colour must be #RRGGBB")
        out[k] = v.strip() if isinstance(v, str) else v
    return out


# -- tools --------------------------------------------------------------------------------

def lab_overview(conn, me, a):
    insts = conn.execute("SELECT * FROM instruments WHERE active=1 ORDER BY name").fetchall()
    return {
        "lab": db.setting(conn, "lab_name"), "currency": db.setting(conn, "currency"),
        "timezone": db.setting(conn, "timezone"), "now": logic.fmt(logic.now_local(conn)),
        "categories": db.categories(conn),
        "new_accounts_need_approval": db.setting(conn, "account_approval") == "1",
        "acting_as": me["username"],
        "instruments": [{"id": i["id"], "name": i["name"], "approval": i["approval"],
                         "booking_mode": i["booking_mode"],
                         "rates": logic.rates(conn, i["id"])} for i in insts],
        "users": {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM users GROUP BY status")},
        "pending_bookings": conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE status='pending'").fetchone()[0],
    }


def list_instruments(conn, me, a):
    q = "SELECT * FROM instruments" + ("" if a.get("include_retired") else " WHERE active=1")
    out = []
    for i in conn.execute(q + " ORDER BY name"):
        d = _row(i)
        d["rates"] = logic.rates(conn, i["id"])
        d["periods"] = logic.describe_periods(i) if i["booking_mode"] == "free" else ""
        d["sessions"] = [{"name": s["name"], "start": s["start_time"], "end": s["end_time"],
                          "days": s["days"]} for s in logic.sessions(conn, i["id"])]
        d["trained_users"] = [r[0] for r in conn.execute(
            "SELECT u.username FROM authorised a JOIN users u ON u.id=a.user_id "
            "WHERE a.instrument_id=? ORDER BY u.username", (i["id"],))]
        out.append(d)
    return out


def list_users(conn, me, a):
    q, args = "SELECT * FROM users WHERE 1=1", []
    if a.get("search"):
        q += " AND (username LIKE ? OR full_name LIKE ? OR email LIKE ? OR group_name LIKE ?)"
        args += [f"%{a['search']}%"] * 4
    for k in ("role", "status", "category"):
        if a.get(k):
            q += f" AND {k}=?"
            args.append(a[k])
    rows = conn.execute(q + " ORDER BY full_name LIMIT ?", (*args, int(a.get("limit", 200))))
    return [_user_out(u) for u in rows]


def list_bookings(conn, me, a):
    start = _day(a.get("from") or date.today().isoformat(), "from")
    end = _day(a.get("to") or (start + timedelta(days=7)).isoformat(), "to")
    q = ("SELECT b.*, i.name AS iname, u.username, u.full_name FROM bookings b "
         "JOIN instruments i ON i.id=b.instrument_id JOIN users u ON u.id=b.user_id "
         "WHERE b.start >= ? AND b.start < ?")
    args: list = [start.isoformat(), (end + timedelta(days=1)).isoformat()]
    if a.get("instrument"):
        q += " AND b.instrument_id=?"
        args.append(instrument(conn, a["instrument"])["id"])
    if a.get("user"):
        q += " AND b.user_id=?"
        args.append(user(conn, a["user"])["id"])
    if a.get("status"):
        q += " AND b.status=?"
        args.append(a["status"])
    rows = conn.execute(q + " ORDER BY b.start LIMIT ?", (*args, int(a.get("limit", 500))))
    return [_booking_out(conn, b) for b in rows]


def pending_requests(conn, me, a):
    rows = conn.execute(
        "SELECT b.*, i.name AS iname, u.username, u.full_name FROM bookings b "
        "JOIN instruments i ON i.id=b.instrument_id JOIN users u ON u.id=b.user_id "
        "WHERE b.status='pending' ORDER BY b.start")
    return {"bookings": [_booking_out(conn, b) for b in rows],
            "accounts": [_user_out(u) for u in conn.execute(
                "SELECT * FROM users WHERE status='pending' ORDER BY created")]}


def quote_booking(conn, me, a):
    inst, u = instrument(conn, a.get("instrument")), user(conn, a.get("user"))
    return logic.quote(conn, inst, u, _when(a.get("start"), "start"),
                       _when(a.get("end"), "end"), actor=me)


def finance_report(conn, me, a):
    s, e = _day(a.get("from"), "from"), _day(a.get("to"), "to")
    if e < s:
        raise ToolError("to is before from")
    an = analytics.build(conn, s, e, user(conn, a["user"])["id"] if a.get("user") else None,
                         instrument(conn, a["instrument"])["id"] if a.get("instrument") else None)
    return {"period": an.period(), "currency": an.currency,
            "headline": {k.label: {"value": k.value, "note": k.note} for k in an.kpis},
            "instruments": an.instruments, "groups": an.groups,
            "charts": {k: {"title": c.title, "kind": c.kind, "labels": c.labels,
                           "series": [{"name": n, "values": v} for n, v in c.series]}
                       for k, c in an.charts.items() if k != "heatmap"}}


def list_issues(conn, me, a):
    s = _when(a["from"] + "T00:00", "from") if a.get("from") else datetime(2000, 1, 1)
    e = _when(a["to"] + "T23:59", "to") if a.get("to") else datetime(2100, 1, 1)
    ids = [instrument(conn, a["instrument"])["id"]] if a.get("instrument") else \
        [r[0] for r in conn.execute("SELECT id FROM instruments")]
    out = []
    for iid in ids:
        out += [_row(r) for r in logic.issues(conn, iid, s, e)]
    return sorted(out, key=lambda r: r["start"])


def _ppms_sources(conn, a) -> list[ppms.Source]:
    files = a.get("files") or []
    if not files:
        raise ToolError("give files: [{path, kind?, mapping?}]")
    out = []
    for f in files:
        f = {"path": f} if isinstance(f, str) else f
        p = Path(str(f.get("path", ""))).expanduser()
        if not p.is_file():
            raise ToolError(f"no file at {p}")
        t = ppms.read_table(p)
        kind = f.get("kind") or ppms.guess_kind(t.headers)
        if kind not in ppms.KINDS:
            raise ToolError(f"{p.name}: cannot tell what it holds; give kind as one of "
                            f"{', '.join(ppms.ORDER)}. Its columns: {t.headers}")
        mapping = ppms.guess_mapping(kind, t.headers)
        mapping.update(f.get("mapping") or {})
        bad = [c for c in mapping.values() if c and c not in t.headers]
        if bad:
            raise ToolError(f"{p.name}: no column {bad[0]!r}; its columns: {t.headers}")
        missing = [x.key for x in ppms.KINDS[kind][1] if x.required and not mapping.get(x.key)]
        if missing:
            raise ToolError(f"{p.name}: map these fields to a column: {missing}. "
                            f"Its columns: {t.headers}")
        out.append(ppms.Source(kind, t, mapping, dict(f.get("systems") or {})))
    return out


def preview_ppms_import(conn, me, a):
    sources = _ppms_sources(conn, a)
    s = ppms.run(conn, sources, dry_run=True, dayfirst=a.get("dayfirst"))
    return {"files": [{"path": x.table.path, "kind": x.kind, "rows": len(x.table.rows),
                       "mapping": x.mapping, "columns": x.table.headers,
                       "ppms_systems": x.system_names(), "systems": x.systems}
                      for x in sources],
            "would_do": s.counts, "skipped_rows": s.problems}


def import_ppms(conn, me, a):
    sources = _ppms_sources(conn, a)
    main = next(r for r in conn.execute("PRAGMA database_list") if r[1] == "main")[2]
    backup = Path(main).parent / f"lab-before-ppms-import-{datetime.now():%Y%m%d-%H%M%S}.db"
    db.backup(conn, backup)
    s = ppms.run(conn, sources, dry_run=False, dayfirst=a.get("dayfirst"))
    return {"done": s.counts, "skipped_rows": s.problems, "backup": str(backup)}


def add_instrument(conn, me, a):
    name = str(a.get("name") or "").strip()
    if not name:
        raise ToolError("name is required")
    if conn.execute("SELECT 1 FROM instruments WHERE lower(name)=lower(?)", (name,)).fetchone():
        raise ToolError(f"there is already an instrument called {name!r}")
    fields = _check_instrument({k: v for k, v in a.items() if k != "name"})
    iid = logic.add_instrument(conn, name, fields.pop("description", ""),
                               fields.pop("approval", "manual"), fields.pop("colour", "#1f6feb"),
                               fields.pop("open_time", "08:00"), fields.pop("close_time", "18:00"),
                               0, fields.pop("max_minutes", 480))
    if fields:
        conn.execute(f"UPDATE instruments SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                     (*fields.values(), iid))
    return {"id": iid, "name": name}


def update_instrument(conn, me, a):
    inst = instrument(conn, a.get("instrument"))
    changes = _check_instrument(a.get("changes") or {})
    if not changes:
        raise ToolError("nothing to change")
    conn.execute(f"UPDATE instruments SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?",
                 (*changes.values(), inst["id"]))
    return _row(conn.execute("SELECT * FROM instruments WHERE id=?", (inst["id"],)).fetchone())


def set_rates(conn, me, a):
    cats = db.categories(conn)
    done = []
    for r in a.get("rates") or []:
        inst = instrument(conn, r.get("instrument"))
        cat = r.get("category")
        if cat not in cats:
            raise ToolError(f"unknown category {cat!r}; the categories are {cats} "
                            "(add one with update_settings)")
        rate = float(r.get("rate"))
        if rate < 0:
            raise ToolError("a rate cannot be negative")
        conn.execute("INSERT OR REPLACE INTO rates (instrument_id, category, rate) "
                     "VALUES (?, ?, ?)", (inst["id"], cat, rate))
        done.append({"instrument": inst["name"], "category": cat, "rate": rate})
    return {"set": done}


def add_users(conn, me, a):
    made = []
    for spec in a.get("users") or []:
        role = spec.get("role", "user")
        if role not in logic.ROLE_LABELS:
            raise ToolError(f"role must be one of {', '.join(logic.ROLE_LABELS)}")
        status = spec.get("status", "active")
        if spec.get("password"):
            uid = logic.create_user(conn, spec.get("username", ""), spec["password"],
                                    spec.get("full_name", ""), spec.get("email", ""),
                                    spec.get("group", ""), spec.get("category", ""), role,
                                    status)
        else:     # claimed at first log-in with the email on record, like a PPMS import
            username = str(spec.get("username") or "").strip()
            full = str(spec.get("full_name") or "").strip()
            if not username or not full:
                raise ToolError("username and full_name are required")
            if conn.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)",
                            (username,)).fetchone():
                raise ToolError(f"the username {username!r} is taken")
            cats = db.categories(conn)
            cat = spec.get("category") if spec.get("category") in cats else cats[0]
            uid = conn.execute(
                "INSERT INTO users (username, password_hash, full_name, email, group_name, "
                "category, role, status, created) VALUES (?, '', ?, ?, ?, ?, ?, ?, ?)",
                (username, full, str(spec.get("email") or "").strip(),
                 str(spec.get("group") or "").strip(), cat, role, status,
                 logic.fmt(logic.now_local(conn)))).lastrowid
        made.append(_user_out(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()))
    return {"created": made}


def update_user(conn, me, a):
    u = user(conn, a.get("user"))
    changes = dict(a.get("changes") or {})
    allowed = {"full_name", "email", "group_name", "category", "role", "status"}
    if set(changes) - allowed:
        raise ToolError(f"can change only {', '.join(sorted(allowed))}")
    if "role" in changes and changes["role"] not in logic.ROLE_LABELS:
        raise ToolError(f"role must be one of {', '.join(logic.ROLE_LABELS)}")
    if "status" in changes and changes["status"] not in ("pending", "active", "disabled"):
        raise ToolError("status must be pending, active or disabled")
    if "category" in changes and changes["category"] not in db.categories(conn):
        raise ToolError(f"category must be one of {db.categories(conn)}")
    if u["id"] == me["id"] and (changes.get("role", "admin") != "admin"
                                or changes.get("status", "active") != "active"):
        raise ToolError("Claude cannot demote or disable the account it acts as")
    if not changes:
        raise ToolError("nothing to change")
    conn.execute(f"UPDATE users SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?",
                 (*changes.values(), u["id"]))
    return _user_out(conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone())


def set_training(conn, me, a):
    done = []
    for t in a.get("training") or []:
        u, inst = user(conn, t.get("user")), instrument(conn, t.get("instrument"))
        if t.get("trained", True):
            conn.execute("INSERT OR IGNORE INTO authorised (user_id, instrument_id) VALUES (?, ?)",
                         (u["id"], inst["id"]))
        else:
            conn.execute("DELETE FROM authorised WHERE user_id=? AND instrument_id=?",
                         (u["id"], inst["id"]))
        done.append({"user": u["username"], "instrument": inst["name"],
                     "trained": bool(t.get("trained", True))})
    return {"set": done}


def book(conn, me, a):
    inst, u = instrument(conn, a.get("instrument")), user(conn, a.get("user"))
    made, errors = logic.book_range(conn, inst["id"], u["id"], _when(a.get("start"), "start"),
                                    _when(a.get("end"), "end"), a.get("purpose", ""),
                                    actor_id=me["id"])
    if not made:
        raise ToolError("; ".join(errors) or "nothing could be booked")
    return {"booked": [{"id": c.id, "status": c.status} for c in made], "problems": errors}


def decide_booking(conn, me, a):
    logic.decide(conn, int(a.get("booking_id")), me["id"], bool(a.get("approve")),
                 a.get("note", ""))
    return {"booking_id": int(a["booking_id"]),
            "status": "approved" if a.get("approve") else "rejected"}


def cancel_booking(conn, me, a):
    logic.cancel(conn, int(a.get("booking_id")), me, a.get("note", ""))
    return {"booking_id": int(a["booking_id"]), "status": "cancelled"}


def record_past_bookings(conn, me, a):
    out = {"recorded": 0, "already_there": 0, "overlapping": 0}
    for n, b in enumerate(a.get("bookings") or [], start=1):
        try:
            inst, u = instrument(conn, b.get("instrument")), user(conn, b.get("user"))
            amount = b.get("amount")
            result = ppms.record_history(
                conn, inst["id"], u["id"], _when(b.get("start"), "start"),
                _when(b.get("end"), "end"), b.get("status", "approved"),
                None if amount in (None, "") else float(amount), b.get("purpose", ""),
                note=NOTE)
        except (ValueError, TypeError) as exc:
            raise ToolError(f"booking {n}: {exc} (nothing was recorded)")
        out["already_there" if result == "exists" else "recorded"] += 1
        out["overlapping"] += result == "overlap"
    return out


def report_issue(conn, me, a):
    inst = instrument(conn, a.get("instrument"))
    kind = a.get("kind", "problem")
    start = _when(a["start"], "start") if a.get("start") else logic.now_local(conn)
    end = _when(a["end"], "end") if a.get("end") else None
    iid = logic.report_issue(conn, inst["id"], kind, start, end, a.get("note", ""), me["id"])
    return {"issue_id": iid}


def resolve_issue(conn, me, a):
    logic.resolve_issue(conn, int(a.get("issue_id")),
                        _when(a["when"], "when") if a.get("when") else None)
    return {"issue_id": int(a["issue_id"]), "resolved": True}


def update_settings(conn, me, a):
    done = {}
    for k in ("lab_name", "currency"):
        if a.get(k):
            db.set_setting(conn, k, str(a[k]).strip())
            done[k] = a[k]
    if a.get("timezone"):
        try:
            ZoneInfo(a["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise ToolError(f"unknown time zone {a['timezone']!r}")
        db.set_setting(conn, "timezone", a["timezone"])
        done["timezone"] = a["timezone"]
    if a.get("categories"):
        cats = [str(c).strip() for c in a["categories"] if str(c).strip()]
        gone = [c for c in db.categories(conn) if c not in cats and conn.execute(
            "SELECT 1 FROM users WHERE category=?", (c,)).fetchone()]
        if gone:
            raise ToolError(f"users are still in {gone}; move them first")
        db.set_setting(conn, "categories", "\n".join(cats))
        done["categories"] = cats
    for k in ("account_approval", "show_names"):
        if k in a:
            db.set_setting(conn, k, "1" if a[k] else "0")
            done[k] = bool(a[k])
    return {"changed": done}


# -- catalogue ----------------------------------------------------------------------------

def _s(props: dict, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


STR, INT, NUM, BOOL = ({"type": "string"}, {"type": "integer"}, {"type": "number"},
                       {"type": "boolean"})
REF = {"type": ["string", "integer"]}
WHEN = {"type": "string", "description": "lab-local time YYYY-MM-DDTHH:MM"}
DAY = {"type": "string", "description": "YYYY-MM-DD"}
_INST_PROPS = {k: (INT if t is int else STR) for k, t in _INSTRUMENT_FIELDS.items()}

TOOLS = [
    (lab_overview, True, "Start here: the lab's name, currency, time zone, rate categories, "
     "instruments with their rates, user counts and pending requests.", _s({})),
    (list_instruments, True, "Every instrument with all its settings, rates, periods, "
     "sessions and trained users.", _s({"include_retired": BOOL})),
    (list_users, True, "Find users by name, username, email or group; filter by role, status "
     "or category.", _s({"search": STR, "role": STR, "status": STR, "category": STR,
                         "limit": INT})),
    (list_bookings, True, "Bookings starting in a date range (default: the next 7 days), "
     "optionally for one instrument, user or status, with their cost.",
     _s({"from": DAY, "to": DAY, "instrument": REF, "user": REF, "status": STR, "limit": INT})),
    (pending_requests, True, "Bookings and new accounts waiting for the lab manager.", _s({})),
    (quote_booking, True, "What booking a time would mean for a user, without booking: the "
     "slots or sessions it snaps to, the cost, rule problems, and whether it is approved at "
     "once.", _s({"instrument": REF, "user": REF, "start": WHEN, "end": WHEN},
                 ["instrument", "user", "start", "end"])),
    (finance_report, True, "Finance and usage for a period: headline figures, revenue by "
     "month, instrument, category, group and user, utilisation, downtime, and the instrument "
     "and group tables.", _s({"from": DAY, "to": DAY, "instrument": REF, "user": REF},
                             ["from", "to"])),
    (list_issues, True, "Problems and out-of-order reports, optionally for one instrument or "
     "period.", _s({"instrument": REF, "from": DAY, "to": DAY})),
    (preview_ppms_import, True, "Dry run of a PPMS import from exported CSV/Excel files on "
     "this computer: what each file was recognised as, the column matching, and what would "
     "be created or skipped. Nothing is saved.",
     _s({"files": {"type": "array", "items": {"type": ["string", "object"]},
                   "description": "paths, or {path, kind, mapping: {field: column}, "
                                  "systems: {PPMS system name: instrument name}}"},
         "dayfirst": BOOL}, ["files"])),
    (import_ppms, False, "Import PPMS export files for real (after preview_ppms_import). A "
     "backup is saved first; files already imported are skipped.",
     _s({"files": {"type": "array", "items": {"type": ["string", "object"]}},
         "dayfirst": BOOL}, ["files"])),
    (add_instrument, False, "Add an instrument. Any instrument field can be given (approval, "
     "open_time, close_time, slot_minutes, evening_mode, weekend_mode, ...).",
     _s(_INST_PROPS, ["name"])),
    (update_instrument, False, "Change an instrument's settings. Retire it with "
     "changes={active: 0}.", _s({"instrument": REF, "changes": _s(_INST_PROPS)},
                               ["instrument", "changes"])),
    (set_rates, False, "Set hourly rates: a list of {instrument, category, rate}.",
     _s({"rates": {"type": "array", "items": _s({"instrument": REF, "category": STR,
                                                 "rate": NUM})}}, ["rates"])),
    (add_users, False, "Create accounts: a list of {username, full_name, email, group, "
     "category, role, status, password}. Without a password the person chooses one at first "
     "log-in by giving the email on record.",
     _s({"users": {"type": "array", "items": _s({
         "username": STR, "full_name": STR, "email": STR, "group": STR, "category": STR,
         "role": STR, "status": STR, "password": STR})}}, ["users"])),
    (update_user, False, "Change a user's full_name, email, group_name, category, role "
     "(user, superuser, admin) or status (pending, active, disabled).",
     _s({"user": REF, "changes": _s({"full_name": STR, "email": STR, "group_name": STR,
                                     "category": STR, "role": STR, "status": STR})},
        ["user", "changes"])),
    (set_training, False, "Mark users trained (or not) on instruments: a list of {user, "
     "instrument, trained}.", _s({"training": {"type": "array", "items": _s({
         "user": REF, "instrument": REF, "trained": BOOL})}}, ["training"])),
    (book, False, "Book for a user as the lab manager: approved at once, at the user's "
     "price, held only to the hard rules. Session instruments book every session in the "
     "range.", _s({"instrument": REF, "user": REF, "start": WHEN, "end": WHEN, "purpose": STR},
                  ["instrument", "user", "start", "end"])),
    (decide_booking, False, "Approve or reject a pending booking.",
     _s({"booking_id": INT, "approve": BOOL, "note": STR}, ["booking_id", "approve"])),
    (cancel_booking, False, "Cancel a booking.", _s({"booking_id": INT, "note": STR},
                                                    ["booking_id"])),
    (record_past_bookings, False, "Record bookings that already happened elsewhere (e.g. read "
     "from a PPMS screen): no booking rules, and amount is what was charged (leave it out to "
     "charge the hourly rate). Exact repeats are skipped.",
     _s({"bookings": {"type": "array", "items": _s({
         "instrument": REF, "user": REF, "start": WHEN, "end": WHEN, "amount": NUM,
         "status": STR, "purpose": STR})}}, ["bookings"])),
    (report_issue, False, "Report a problem (kind=problem) or out of order (kind=down) on an "
     "instrument, from start (default now) until end (default: until fixed).",
     _s({"instrument": REF, "kind": STR, "start": WHEN, "end": WHEN, "note": STR},
        ["instrument"])),
    (resolve_issue, False, "Mark an issue fixed (now, or at when).",
     _s({"issue_id": INT, "when": WHEN}, ["issue_id"])),
    (update_settings, False, "Change lab settings: lab_name, currency, timezone, categories "
     "(the full list), account_approval, show_names.",
     _s({"lab_name": STR, "currency": STR, "timezone": STR,
         "categories": {"type": "array", "items": STR}, "account_approval": BOOL,
         "show_names": BOOL})),
]
BY_NAME = {f.__name__: (f, ro) for f, ro, _, _ in TOOLS}


def catalogue() -> list[dict]:
    return [{"name": f.__name__, "description": d, "inputSchema": schema,
             "annotations": {"readOnlyHint": ro, "destructiveHint": False}}
            for f, ro, d, schema in TOOLS]


# These open their own BEGIN IMMEDIATE (logic.book, ppms.run).
OWN_TRANSACTION = {"book", "import_ppms", "preview_ppms_import"}


def call(conn, me, name: str, args: dict, log: Path | None = None):
    """Run one tool. A tool's changes are committed together, or not at all
    (the connection is in autocommit mode, so the transaction is explicit)."""
    if name not in BY_NAME:
        raise ToolError(f"no tool {name!r}")
    fn, read_only = BY_NAME[name]
    wrap = not read_only and name not in OWN_TRANSACTION
    if wrap:
        conn.execute("BEGIN IMMEDIATE")
    try:
        result = fn(conn, me, args or {})
        if wrap:
            conn.execute("COMMIT")
    except Exception as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        if isinstance(exc, ToolError):
            raise
        if isinstance(exc, (ValueError, KeyError, TypeError)):     # logic's own refusals
            raise ToolError(str(exc) or exc.__class__.__name__) from exc
        raise
    if not read_only and log is not None:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                 "as": me["username"], "tool": name, "args": args},
                                default=str) + "\n")
    return result
