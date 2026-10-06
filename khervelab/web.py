"""The web application: one lab, its users, instruments, bookings and costs.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import io
import secrets
import tempfile
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

from . import __version__, db, logic, reports



def create_app(data_dir: Path | str) -> Flask:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lab.db"
    conn = db.connect(db_path)
    db.init(conn)
    secret = db.setting(conn, "secret_key")
    conn.close()

    app = Flask(__name__)
    app.secret_key = secret
    app.config.update(DB_PATH=str(db_path), DATA_DIR=str(data_dir),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))

    # -- request plumbing -------------------------------------------------------
    @app.before_request
    def _open():
        g.db = db.connect(app.config["DB_PATH"])
        g.user = None
        uid = session.get("uid")
        if uid:
            u = g.db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if u and u["status"] == "active":
                g.user = u
            else:
                session.pop("uid", None)
        if request.endpoint == "static":
            return None
        if request.method == "POST":
            token = request.form.get("csrf") or request.headers.get("X-CSRF")
            if not token or token != session.get("csrf"):
                abort(400, "Your form expired; reload the page and try again.")
        has_admin = g.db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone()
        if not has_admin and request.endpoint != "setup":
            return redirect(url_for("setup"))
        return None

    @app.teardown_request
    def _close(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.context_processor
    def _globals():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        pending = 0
        if g.get("user") is not None and g.user["role"] == "admin":
            pending = g.db.execute(
                "SELECT (SELECT COUNT(*) FROM bookings WHERE status='pending') + "
                "(SELECT COUNT(*) FROM users WHERE status='pending')").fetchone()[0]
        return {"csrf_token": session["csrf"], "me": g.get("user"), "pending_count": pending,
                "lab_name": db.setting(g.db, "lab_name"), "currency": db.setting(g.db, "currency"),
                "version": __version__, "approval_labels": logic.APPROVAL_LABELS}

    def login_required(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if g.user is None:
                return redirect(url_for("login", next=request.path))
            return view(*a, **kw)
        return wrapper

    def admin_required(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if g.user is None:
                return redirect(url_for("login", next=request.path))
            if g.user["role"] != "admin":
                abort(403)
            return view(*a, **kw)
        return wrapper

    def instrument_or_404(iid: int):
        inst = g.db.execute("SELECT * FROM instruments WHERE id=?", (iid,)).fetchone()
        if inst is None:
            abort(404)
        return inst

    def money(x: float) -> str:
        return f"{db.setting(g.db, 'currency')}{x:,.2f}"
    app.jinja_env.filters["money"] = money
    app.jinja_env.filters["when"] = lambda s: s.replace("T", " ") if s else ""

    # -- setup and accounts ----------------------------------------------------------
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if g.db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            return redirect(url_for("home"))
        if request.method == "POST":
            f = request.form
            if f.get("password") != f.get("password2"):
                flash("The passwords differ.", "error")
                return render_template("setup.html", examples=logic.EXAMPLES, form=f)
            try:
                db.set_setting(g.db, "lab_name", f.get("lab_name", "").strip() or "My lab")
                db.set_setting(g.db, "currency", f.get("currency", "£").strip() or "£")
                uid = logic.create_user(g.db, f.get("username", ""), f.get("password", ""),
                                        f.get("full_name", ""), f.get("email", ""),
                                        role="admin", status="active")
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template("setup.html", examples=logic.EXAMPLES, form=f)
            for ex in logic.EXAMPLES:
                if f.get(f"ex_{ex[0]}"):
                    logic.add_instrument(g.db, *ex)
            session.clear()
            session["uid"] = uid
            flash("Your lab is ready. Add or edit instruments and set their hourly rates.", "ok")
            return redirect(url_for("admin_instruments"))
        return render_template("setup.html", examples=logic.EXAMPLES, form={})

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            u = logic.authenticate(g.db, request.form.get("username", ""),
                                   request.form.get("password", ""))
            if u is None:
                flash("Wrong username or password.", "error")
            elif u["status"] == "pending":
                flash("Your account is waiting for the administrator's approval.", "error")
            elif u["status"] == "disabled":
                flash("Your account is disabled; contact the lab manager.", "error")
            else:
                session.clear()
                session.permanent = True
                session["uid"] = u["id"]
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//")
                                else url_for("home"))
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        cats = db.categories(g.db)
        if request.method == "POST":
            f = request.form
            if f.get("password") != f.get("password2"):
                flash("The passwords differ.", "error")
                return render_template("register.html", form=f, categories=cats)
            try:
                logic.create_user(g.db, f.get("username", ""), f.get("password", ""),
                                  f.get("full_name", ""), f.get("email", ""),
                                  f.get("group_name", ""), f.get("category", ""))
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template("register.html", form=f, categories=cats)
            if db.setting(g.db, "account_approval") == "1":
                flash("Account created. The lab manager will approve it; you can then log in.",
                      "ok")
            else:
                flash("Account created; you can log in now.", "ok")
            return redirect(url_for("login"))
        return render_template("register.html", form={}, categories=cats)

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        if request.method == "POST":
            f = request.form
            if f.get("action") == "password":
                if not logic.authenticate(g.db, g.user["username"], f.get("current", "")):
                    flash("The current password is wrong.", "error")
                elif f.get("password") != f.get("password2"):
                    flash("The new passwords differ.", "error")
                else:
                    try:
                        logic.set_password(g.db, g.user["id"], f.get("password", ""))
                        flash("Password changed.", "ok")
                    except ValueError as exc:
                        flash(str(exc), "error")
            else:
                g.db.execute("UPDATE users SET full_name=?, email=?, group_name=? WHERE id=?",
                             (f.get("full_name", "").strip() or g.user["full_name"],
                              f.get("email", "").strip(), f.get("group_name", "").strip(),
                              g.user["id"]))
                flash("Details saved.", "ok")
            return redirect(url_for("account"))
        return render_template("account.html")

    # -- booking -------------------------------------------------------------------------
    @app.route("/")
    @login_required
    def home():
        insts = g.db.execute("SELECT * FROM instruments WHERE active=1 ORDER BY name").fetchall()
        mine = g.db.execute(
            "SELECT b.*, i.name AS instrument FROM bookings b JOIN instruments i "
            "ON i.id=b.instrument_id WHERE b.user_id=? AND b.end >= ? "
            "AND b.status IN ('pending','approved') ORDER BY b.start LIMIT 20",
            (g.user["id"], logic.fmt(logic.now_local(g.db)))).fetchall()
        trained = {r[0] for r in g.db.execute(
            "SELECT instrument_id FROM authorised WHERE user_id=?", (g.user["id"],))}
        my_rates = {i["id"]: logic.rate_for(g.db, i["id"], g.user["category"]) for i in insts}
        return render_template("home.html", instruments=insts, mine=mine, trained=trained,
                               my_rates=my_rates)

    @app.route("/instrument/<int:iid>")
    @login_required
    def instrument(iid: int):
        inst = instrument_or_404(iid)
        my_rate = logic.rate_for(g.db, iid, g.user["category"])
        sessions = []
        for r in logic.sessions(g.db, iid) if inst["booking_mode"] == "sessions" else []:
            price = logic.session_price(g.db, r["id"], g.user["category"])
            hrs = logic.session_hours(r["start_time"], r["end_time"])
            sessions.append({"name": r["name"], "start": r["start_time"], "end": r["end_time"],
                             "days": logic.days_label(r["days"]),
                             "cost": price if price is not None else hrs * my_rate,
                             "fixed": price is not None})
        return render_template("instrument.html", inst=inst, my_rate=my_rate, sessions=sessions,
                               periods=logic.describe_periods(inst),
                               trained=logic.is_authorised(g.db, g.user["id"], iid))

    @app.route("/book", methods=["POST"])
    @login_required
    def book():
        f = request.form
        iid = int(f.get("instrument_id", 0))
        inst = instrument_or_404(iid)
        try:
            start = logic.parse(f.get("start", ""))
            end = logic.parse(f.get("end", ""))
        except ValueError:
            flash("Choose a start and an end time.", "error")
            return redirect(url_for("instrument", iid=iid))
        if inst["booking_mode"] == "free":     # to the slots or whole evening/weekend
            start = logic.snap_start(inst, start)
            end = logic.snap_end(inst, start, end)
        try:
            made, problems = logic.book_range(g.db, iid, g.user["id"], start, end,
                                              f.get("purpose", ""))
        except logic.BookingError as exc:
            flash(f"Not booked: {exc}.", "error")
            return redirect(url_for("instrument", iid=iid, date=start.date().isoformat()))
        what = (f"{len(made)} {inst['name']} sessions" if inst["booking_mode"] == "sessions"
                and len(made) > 1 else f"{inst['name']}, {start:%a %d %b %H:%M}–{end:%H:%M}"
                if inst["booking_mode"] == "free" else f"the {inst['name']} session")
        if all(m.status == "approved" for m in made):
            flash(f"Booked {what}.", "ok")
        else:
            flash(f"Request sent for {what}. It shows as pending until the lab manager "
                  "approves it.", "ok")
        for p in problems:
            flash(f"Not booked: {p}.", "error")
        return redirect(url_for("instrument", iid=iid, date=start.date().isoformat()))

    @app.route("/api/events")
    @login_required
    def events():
        iid = request.args.get("instrument", type=int)
        try:
            start = logic.parse(request.args.get("start", "")[:16])
            end = logic.parse(request.args.get("end", "")[:16])
        except ValueError:
            abort(400)
        q = ("SELECT b.*, u.full_name, i.name AS instrument, i.colour FROM bookings b "
             "JOIN users u ON u.id=b.user_id JOIN instruments i ON i.id=b.instrument_id "
             "WHERE b.status IN ('pending','approved') AND b.start < ? AND b.end > ?")
        args: list = [logic.fmt(end), logic.fmt(start)]
        if iid:
            q += " AND b.instrument_id=?"
            args.append(iid)
        show_names = db.setting(g.db, "show_names") == "1" or g.user["role"] == "admin"
        out = []
        for b in g.db.execute(q, args).fetchall():
            mine = b["user_id"] == g.user["id"]
            who = "You" if mine else (b["full_name"] if show_names else "Booked")
            title = who if iid else f"{b['instrument']} · {who}"
            if b["status"] == "pending":
                title += " (pending)"
            out.append({"id": b["id"], "title": title, "start": b["start"], "end": b["end"],
                        "color": b["colour"], "classNames": [b["status"]] + (["mine"] if mine
                                                                             else [])})
        if iid:
            for r in logic.issues(g.db, iid, start, end):
                out.append({"start": r["start"], "end": r["end"] or logic.fmt(end),
                            "display": "background",
                            "color": db.setting(g.db, "colour_down" if r["kind"] == "down"
                                                else "colour_problem"),
                            "title": ("Out of order" if r["kind"] == "down" else "Problem")
                            + (f": {r['note']}" if r["note"] else ""),
                            "classNames": ["issue"]})
            inst = g.db.execute("SELECT * FROM instruments WHERE id=?", (iid,)).fetchone()
            if inst and inst["booking_mode"] == "sessions":
                for o in logic.occurrences(g.db, iid, start, end):
                    out.append({"start": logic.fmt(o.start), "end": logic.fmt(o.end),
                                "display": "background", "color": inst["colour"],
                                "title": o.name, "classNames": ["session"]})
        return jsonify(out)

    @app.route("/bookings")
    @login_required
    def my_bookings():
        rows = g.db.execute(
            "SELECT b.*, i.name AS instrument FROM bookings b JOIN instruments i "
            "ON i.id=b.instrument_id WHERE b.user_id=? ORDER BY b.start DESC LIMIT 300",
            (g.user["id"],)).fetchall()
        now = logic.fmt(logic.now_local(g.db))
        month0 = date.today().replace(day=1)
        rep = reports.build(g.db, month0, date.today(), user_id=g.user["id"])
        return render_template("bookings.html", rows=rows, now=now,
                               cost=lambda b: logic.cost(g.db, b), month=rep)

    @app.route("/bookings/<int:bid>/cancel", methods=["POST"])
    @login_required
    def cancel(bid: int):
        try:
            logic.cancel(g.db, bid, g.user, request.form.get("note", ""))
            flash("Booking cancelled.", "ok")
        except logic.BookingError as exc:
            flash(str(exc), "error")
        return redirect(request.form.get("back") or url_for("my_bookings"))

    # -- administration --------------------------------------------------------------------
    @app.route("/admin/requests")
    @admin_required
    def admin_requests():
        bookings = g.db.execute(
            "SELECT b.*, u.full_name, u.group_name, u.category, i.name AS instrument, "
            "EXISTS(SELECT 1 FROM authorised a WHERE a.user_id=b.user_id "
            "AND a.instrument_id=b.instrument_id) AS trained FROM bookings b "
            "JOIN users u ON u.id=b.user_id JOIN instruments i ON i.id=b.instrument_id "
            "WHERE b.status='pending' ORDER BY b.start").fetchall()
        accounts = g.db.execute("SELECT * FROM users WHERE status='pending' ORDER BY created"
                                ).fetchall()
        return render_template("admin_requests.html", bookings=bookings, accounts=accounts,
                               cost=lambda b: logic.cost(g.db, b))

    @app.route("/admin/bookings/<int:bid>/decide", methods=["POST"])
    @admin_required
    def decide(bid: int):
        approve = request.form.get("decision") == "approve"
        try:
            logic.decide(g.db, bid, g.user["id"], approve, request.form.get("note", ""))
            flash("Booking approved." if approve else "Booking rejected.", "ok")
        except logic.BookingError as exc:
            flash(str(exc), "error")
        return redirect(request.form.get("back") or url_for("admin_requests"))

    @app.route("/admin/accounts/<int:uid>/decide", methods=["POST"])
    @admin_required
    def decide_account(uid: int):
        approve = request.form.get("decision") == "approve"
        g.db.execute("UPDATE users SET status=? WHERE id=? AND status='pending'",
                     ("active" if approve else "disabled", uid))
        flash("Account approved." if approve else "Account refused.", "ok")
        return redirect(url_for("admin_requests"))

    @app.route("/admin/bookings")
    @admin_required
    def admin_bookings():
        a = request.args
        start = a.get("from") or (date.today() - timedelta(days=30)).isoformat()
        end = a.get("to") or (date.today() + timedelta(days=60)).isoformat()
        q = ("SELECT b.*, u.full_name, i.name AS instrument FROM bookings b "
             "JOIN users u ON u.id=b.user_id JOIN instruments i ON i.id=b.instrument_id "
             "WHERE b.start >= ? AND b.start < ?")
        args: list = [start, (date.fromisoformat(end) + timedelta(days=1)).isoformat()]
        for key, col in (("instrument", "b.instrument_id"), ("user", "b.user_id")):
            if a.get(key, type=int):
                q += f" AND {col}=?"
                args.append(a.get(key, type=int))
        if a.get("status"):
            q += " AND b.status=?"
            args.append(a.get("status"))
        rows = g.db.execute(q + " ORDER BY b.start DESC LIMIT 1000", args).fetchall()
        return render_template("admin_bookings.html", rows=rows, f={"from": start, "to": end,
                               **a}, instruments=_all(g.db, "instruments"),
                               users=_all(g.db, "users", "full_name"),
                               cost=lambda b: logic.cost(g.db, b))

    @app.route("/admin/instruments")
    @admin_required
    def admin_instruments():
        insts = g.db.execute("SELECT * FROM instruments ORDER BY active DESC, name").fetchall()
        return render_template("admin_instruments.html", instruments=insts,
                               categories=db.categories(g.db),
                               rates={i["id"]: logic.rates(g.db, i["id"]) for i in insts})

    @app.route("/admin/instruments/new", methods=["GET", "POST"])
    @app.route("/admin/instruments/<int:iid>", methods=["GET", "POST"])
    @admin_required
    def admin_instrument(iid: int | None = None):
        inst = instrument_or_404(iid) if iid else None
        cats = db.categories(g.db)
        users = g.db.execute("SELECT * FROM users WHERE status='active' ORDER BY full_name"
                             ).fetchall()
        if request.method == "POST":
            f = request.form
            try:
                vals = _instrument_form(f)
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(request.path)
            if inst is None:
                cur = g.db.execute(
                    "INSERT INTO instruments (name, description, location, colour, active, "
                    "approval, slot_minutes, min_minutes, max_minutes, max_days_ahead, open_time, "
                    "close_time, weekends) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", vals)
                iid = cur.lastrowid
            else:
                g.db.execute(
                    "UPDATE instruments SET name=?, description=?, location=?, colour=?, active=?, "
                    "approval=?, slot_minutes=?, min_minutes=?, max_minutes=?, max_days_ahead=?, "
                    "open_time=?, close_time=?, weekends=? WHERE id=?", (*vals, iid))
            for c in cats:
                try:
                    rate = float(f.get(f"rate_{c}", "0") or 0)
                except ValueError:
                    rate = 0.0
                g.db.execute("INSERT INTO rates (instrument_id, category, rate) VALUES (?,?,?) "
                             "ON CONFLICT(instrument_id, category) DO UPDATE SET rate=excluded.rate",
                             (iid, c, max(0.0, rate)))
            g.db.execute("DELETE FROM authorised WHERE instrument_id=?", (iid,))
            for u in users:
                if f.get(f"auth_{u['id']}"):
                    g.db.execute("INSERT INTO authorised (user_id, instrument_id) VALUES (?,?)",
                                 (u["id"], iid))
            flash("Instrument saved.", "ok")
            return redirect(url_for("admin_instruments"))
        authorised = {r[0] for r in g.db.execute(
            "SELECT user_id FROM authorised WHERE instrument_id=?", (iid or 0,))}
        return render_template("admin_instrument.html", inst=inst, categories=cats,
                               rates=logic.rates(g.db, iid) if iid else {}, users=users,
                               authorised=authorised)

    @app.route("/admin/users")
    @admin_required
    def admin_users():
        users = g.db.execute("SELECT * FROM users ORDER BY status='pending' DESC, full_name"
                             ).fetchall()
        return render_template("admin_users.html", users=users)

    @app.route("/admin/users/<int:uid>", methods=["GET", "POST"])
    @admin_required
    def admin_user(uid: int):
        u = g.db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if u is None:
            abort(404)
        insts = _all(g.db, "instruments")
        if request.method == "POST":
            f = request.form
            role = f.get("role") if f.get("role") in ("user", "admin") else u["role"]
            status = f.get("status") if f.get("status") in ("pending", "active", "disabled") \
                else u["status"]
            if uid == g.user["id"] and (role != "admin" or status != "active"):
                flash("You cannot remove your own administrator access.", "error")
                return redirect(request.path)
            g.db.execute("UPDATE users SET full_name=?, email=?, group_name=?, category=?, "
                         "role=?, status=? WHERE id=?",
                         (f.get("full_name", "").strip() or u["full_name"],
                          f.get("email", "").strip(), f.get("group_name", "").strip(),
                          f.get("category", u["category"]), role, status, uid))
            g.db.execute("DELETE FROM authorised WHERE user_id=?", (uid,))
            for i in insts:
                if f.get(f"auth_{i['id']}"):
                    g.db.execute("INSERT INTO authorised (user_id, instrument_id) VALUES (?,?)",
                                 (uid, i["id"]))
            if f.get("new_password"):
                try:
                    logic.set_password(g.db, uid, f["new_password"])
                except ValueError as exc:
                    flash(str(exc), "error")
                    return redirect(request.path)
            flash("User saved.", "ok")
            return redirect(url_for("admin_users"))
        authorised = {r[0] for r in g.db.execute(
            "SELECT instrument_id FROM authorised WHERE user_id=?", (uid,))}
        return render_template("admin_user.html", u=u, instruments=insts, authorised=authorised,
                               categories=db.categories(g.db))

    @app.route("/admin/reports")
    @admin_required
    def admin_reports():
        start, end, uid, iid = _report_args()
        rep = reports.build(g.db, start, end, uid, iid)
        return render_template("admin_reports.html", rep=rep, f={
            "from": start.isoformat(), "to": end.isoformat(), "user": uid or "",
            "instrument": iid or ""}, users=_all(g.db, "users", "full_name"),
            instruments=_all(g.db, "instruments"))

    @app.route("/admin/reports/export")
    @admin_required
    def admin_report_export():
        start, end, uid, iid = _report_args()
        rep = reports.build(g.db, start, end, uid, iid)
        who = ""
        if uid:
            u = g.db.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            who = f"-{u['username']}" if u else ""
        stem = f"usage{who}-{start:%Y%m%d}-{end:%Y%m%d}"
        kind = request.args.get("format", "csv")
        if kind == "pdf":
            return send_file(io.BytesIO(reports.to_pdf(rep)), mimetype="application/pdf",
                             as_attachment=True, download_name=f"{stem}.pdf")
        if kind == "xlsx":
            return send_file(io.BytesIO(reports.to_xlsx(rep)), as_attachment=True,
                             download_name=f"{stem}.xlsx", mimetype="application/vnd."
                             "openxmlformats-officedocument.spreadsheetml.sheet")
        return send_file(io.BytesIO(reports.to_csv(rep).encode("utf-8-sig")),
                         mimetype="text/csv", as_attachment=True, download_name=f"{stem}.csv")

    def _report_args():
        a = request.args
        today = date.today()
        first = today.replace(day=1)
        last_month_end = first - timedelta(days=1)
        try:
            start = date.fromisoformat(a.get("from") or last_month_end.replace(day=1).isoformat())
            end = date.fromisoformat(a.get("to") or last_month_end.isoformat())
        except ValueError:
            abort(400)
        return start, end, a.get("user", type=int), a.get("instrument", type=int)

    @app.route("/admin/settings", methods=["GET", "POST"])
    @admin_required
    def admin_settings():
        if request.method == "POST":
            f = request.form
            tzname = f.get("timezone", "").strip() or "Europe/London"
            try:
                ZoneInfo(tzname)
            except (ZoneInfoNotFoundError, ValueError):
                flash(f"Unknown time zone {tzname}.", "error")
                return redirect(request.path)
            cats = [c.strip() for c in f.get("categories", "").splitlines() if c.strip()]
            if not cats:
                flash("Keep at least one rate category.", "error")
                return redirect(request.path)
            db.set_setting(g.db, "lab_name", f.get("lab_name", "").strip() or "My lab")
            db.set_setting(g.db, "currency", f.get("currency", "").strip() or "£")
            db.set_setting(g.db, "timezone", tzname)
            db.set_setting(g.db, "categories", "\n".join(cats))
            db.set_setting(g.db, "account_approval", "1" if f.get("account_approval") else "0")
            db.set_setting(g.db, "show_names", "1" if f.get("show_names") else "0")
            flash("Settings saved.", "ok")
            return redirect(request.path)
        return render_template("admin_settings.html", s={
            k: db.setting(g.db, k) for k in db.DEFAULT_SETTINGS},
            data_dir=app.config["DATA_DIR"])

    @app.route("/admin/backup")
    @admin_required
    def admin_backup():
        tmp = Path(tempfile.mkdtemp()) / "lab.db"
        db.backup(g.db, tmp)
        return send_file(tmp, as_attachment=True, mimetype="application/x-sqlite3",
                         download_name=f"khervelab-backup-{datetime.now():%Y%m%d-%H%M}.db")

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("message.html", title="Not allowed",
                               text="This page is for the lab manager."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("message.html", title="Not found",
                               text="That page does not exist."), 404

    return app


def _all(conn, table: str, order: str = "name"):
    return conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()


def _instrument_form(f) -> tuple:
    name = f.get("name", "").strip()
    if not name:
        raise ValueError("Give the instrument a name.")
    approval = f.get("approval", "manual")
    if approval not in logic.APPROVAL_LABELS:
        approval = "manual"

    def num(key, default, lo=1):
        try:
            return max(lo, int(f.get(key, default)))
        except ValueError:
            raise ValueError(f"{key.replace('_', ' ')} must be a whole number") from None
    slot = num("slot_minutes", 30)
    if slot > 1440:
        raise ValueError("The slot can be at most 24 hours (1440 min).")
    mn, mx = num("min_minutes", 30), num("max_minutes", 480)
    if mn > mx:
        raise ValueError("The shortest booking is longer than the longest.")
    open_t = f.get("open_time", "08:00") or "08:00"
    close_t = f.get("close_time", "20:00") or "20:00"
    if f.get("all_day"):
        open_t, close_t = "00:00", "24:00"
    try:
        if logic.minutes_of(open_t) >= logic.minutes_of(close_t):
            raise ValueError("The closing time must be after the opening time.")
    except (ValueError, IndexError) as exc:
        raise ValueError(str(exc) if "closing" in str(exc) else "Times look like 08:00.") from None
    colour = f.get("colour", "#1f6feb")
    return (name, f.get("description", "").strip(), f.get("location", "").strip(),
            colour if len(colour) == 7 and colour.startswith("#") else "#1f6feb",
            1 if f.get("active") else 0, approval, slot, mn, mx, num("max_days_ahead", 60),
            open_t, close_t, 1 if f.get("weekends") else 0)
