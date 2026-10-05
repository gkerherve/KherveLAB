"""Bridge between local training records and the repository's permission lists.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from ..core.facility import FacilityService
from ..core.models import User
from .store import LocalStore, Person


def sync_permissions(svc: FacilityService, store: LocalStore, person: Person,
                     today: date | None = None) -> bool:
    """Write the person's active permissions to users/<id>.yaml. The public
    side learns only that a boolean changed. Returns whether it committed."""
    perms = store.permissions(person.id, today)
    user = svc.users.get(person.user_id)
    if user is None:
        raise KeyError(f"no user {person.user_id} in the repository")
    if tuple(sorted(user.permissions)) == perms:
        return False
    svc.save_user(replace(user, permissions=perms), message=f"permissions {user.id}")
    return True


def sync_all(svc: FacilityService, store: LocalStore, today: date | None = None) -> int:
    """Revoke lapsed training everywhere; returns how many users changed."""
    n = 0
    for p in store.people():
        if p.user_id in svc.users and sync_permissions(svc, store, p, today):
            n += 1
    return n


def new_person(svc: FacilityService, store: LocalStore, name: str, display: str, email: str = "",
               department: str = "", group: str = "", supervisor: str = "",
               github: str = "") -> Person:
    """Create the anonymous repository user and the local person in one go."""
    uid = svc.next_user_id()
    svc.save_user(User(uid, display, github=github))
    return store.save_person(Person(None, uid, name, email, department, group, supervisor))


def reminder_mailto(lapses, facility: str, svc: FacilityService) -> str:
    """A mailto: draft to everyone whose training lapses soon (BCC)."""
    emails = sorted({l.person.email for l in lapses if l.person.email})
    lines = ["Dear colleague,", "", f"Your training on the following {facility} instruments "
             "expires soon. Please book a refresher with the facility manager:", ""]
    for l in lapses:
        inst = svc.instruments.get(l.training.instrument)
        lines.append(f"- {inst.name if inst else l.training.instrument}: {l.expiry:%d %B %Y}"
                     f" ({l.person.name})")
    lines += ["", "Best regards"]
    return (f"mailto:?bcc={quote(','.join(emails))}"
            f"&subject={quote('Instrument training refresher due')}"
            f"&body={quote(chr(10).join(lines))}")


def export_person_pdf(svc: FacilityService, store: LocalStore, person: Person, target: Path) -> Path:
    """One person's full record, for an audit or a leaving researcher."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    st = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(target), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=f"Training record — {person.name}")
    story = [Paragraph(f"Training record: {person.name}", st["Title"]),
             Paragraph(f"{svc.cfg.facility.name} · generated {datetime.now():%d %B %Y %H:%M}",
                       st["Normal"]), Spacer(1, 6 * mm)]
    info = [["Name", person.name], ["Email", person.email], ["Department", person.department],
            ["Group", person.group], ["Supervisor", person.supervisor],
            ["Facility user id", person.user_id]]
    t = Table(info, colWidths=[40 * mm, 120 * mm])
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    story += [t, Spacer(1, 6 * mm), Paragraph("Training", st["Heading2"])]
    rows = [["Instrument", "Status", "Trained", "Assessor", "Expires", "Evidence"]]
    for tr in sorted(store.trainings(person.id), key=lambda x: x.instrument):
        inst = svc.instruments.get(tr.instrument)
        exp = tr.effective_expiry()
        rows.append([Paragraph(inst.name if inst else tr.instrument, st["BodyText"]), tr.status,
                     tr.date_trained.isoformat() if tr.date_trained else "",
                     tr.assessor, exp.isoformat() if exp else "",
                     Paragraph(", ".join(e.filename for e in store.evidence(tr.id)) or "—",
                               st["BodyText"])])
    tt = Table(rows, colWidths=[48 * mm, 22 * mm, 22 * mm, 26 * mm, 22 * mm, 34 * mm], repeatRows=1)
    tt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f3f6")),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    story.append(tt)
    notes = [tr for tr in store.trainings(person.id) if tr.notes]
    if notes:
        story += [Spacer(1, 6 * mm), Paragraph("Assessment notes", st["Heading2"])]
        for tr in notes:
            story.append(Paragraph(f"<b>{tr.instrument}</b>: {tr.notes}", st["BodyText"]))
    doc.build(story)
    return target

