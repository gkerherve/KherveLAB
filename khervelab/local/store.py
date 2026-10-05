"""Local, encrypted personal data: people, training records, evidence.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Nothing here ever enters the facility repository. Every personal column is
Fernet-encrypted with a key held in the OS keyring; the repository only
learns that a user's permission list changed.
"""

from __future__ import annotations

import base64
import io
import json
import os
import secrets
import sqlite3
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KEYRING_SERVICE = "KherveLAB"
KEYRING_USER = "local-db-key"
STATUSES = ("in training", "trained", "suspended", "revoked")
BACKUP_EVERY_LAUNCHES = 10
BACKUP_MAX_AGE_DAYS = 30


def default_home() -> Path:
    return Path(os.environ.get("KHERVELAB_HOME", Path.home() / ".khervelab"))


def keyring_key(create: bool = True) -> bytes:
    """The database key from the OS keyring, generated on first run."""
    import keyring
    k = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    if k:
        return k.encode()
    if not create:
        raise LookupError("no KherveLAB key in the keyring")
    k = Fernet.generate_key().decode()
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, k)
    return k.encode()


@dataclass
class Person:
    id: int | None
    user_id: str                # anonymous id in the repository
    name: str
    email: str = ""
    department: str = ""
    group: str = ""
    supervisor: str = ""


@dataclass
class Training:
    id: int | None
    person_id: int
    instrument: str
    status: str = "trained"
    date_trained: date | None = None
    assessor: str = ""
    expiry: date | None = None
    refresher_months: int = 0
    notes: str = ""

    def effective_expiry(self) -> date | None:
        if self.expiry:
            return self.expiry
        if self.date_trained and self.refresher_months:
            m = self.date_trained.month - 1 + self.refresher_months
            y = self.date_trained.year + m // 12
            day = min(self.date_trained.day, 28)
            return date(y, m % 12 + 1, day)
        return None

    def active(self, today: date) -> bool:
        exp = self.effective_expiry()
        return self.status == "trained" and (exp is None or exp >= today)


@dataclass
class Evidence:
    id: int | None
    training_id: int
    filename: str
    stored_name: str
    added: str = ""


@dataclass
class Lapse:
    person: Person
    training: Training
    expiry: date
    days_left: int = field(default=0)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY, user_id TEXT UNIQUE NOT NULL,
    name BLOB, email BLOB, department BLOB, grp BLOB, supervisor BLOB);
CREATE TABLE IF NOT EXISTS training (
    id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    instrument TEXT NOT NULL, status TEXT NOT NULL,
    date_trained BLOB, assessor BLOB, expiry BLOB, refresher_months INTEGER DEFAULT 0, notes BLOB);
CREATE TABLE IF NOT EXISTS training_evidence (
    id INTEGER PRIMARY KEY,
    training_id INTEGER NOT NULL REFERENCES training(id) ON DELETE CASCADE,
    filename BLOB, stored_name TEXT NOT NULL, added TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class LocalStore:
    def __init__(self, home: Path | None = None, key: bytes | None = None):
        self.home = Path(home or default_home())
        self.home.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.home, 0o700)
        except OSError:
            pass
        (self.home / "evidence").mkdir(exist_ok=True)
        self.key = key or keyring_key()
        self.fernet = Fernet(self.key)
        self.db_path = self.home / "local.db"
        self.db = sqlite3.connect(self.db_path)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # -- crypto -----------------------------------------------------------
    def _enc(self, text: str | None) -> bytes | None:
        if text is None or text == "":
            return None
        return self.fernet.encrypt(text.encode("utf-8"))

    def _dec(self, blob: bytes | None) -> str:
        if not blob:
            return ""
        try:
            return self.fernet.decrypt(blob).decode("utf-8")
        except InvalidToken:
            raise ValueError("cannot decrypt the local database: wrong key") from None

    def _enc_date(self, d: date | None) -> bytes | None:
        return self._enc(d.isoformat()) if d else None

    def _dec_date(self, blob) -> date | None:
        s = self._dec(blob)
        return date.fromisoformat(s) if s else None

    # -- people -----------------------------------------------------------
    def save_person(self, p: Person) -> Person:
        vals = (p.user_id, self._enc(p.name), self._enc(p.email), self._enc(p.department),
                self._enc(p.group), self._enc(p.supervisor))
        if p.id is None:
            cur = self.db.execute("INSERT INTO person (user_id, name, email, department, grp, "
                                  "supervisor) VALUES (?,?,?,?,?,?)", vals)
            p.id = cur.lastrowid
        else:
            self.db.execute("UPDATE person SET user_id=?, name=?, email=?, department=?, grp=?, "
                            "supervisor=? WHERE id=?", (*vals, p.id))
        self.db.commit()
        return p

    def _person(self, row) -> Person:
        return Person(row[0], row[1], self._dec(row[2]), self._dec(row[3]), self._dec(row[4]),
                      self._dec(row[5]), self._dec(row[6]))

    def people(self) -> list[Person]:
        rows = self.db.execute("SELECT id, user_id, name, email, department, grp, supervisor "
                               "FROM person").fetchall()
        return sorted((self._person(r) for r in rows), key=lambda p: p.name.lower())

    def person(self, pid: int) -> Person | None:
        r = self.db.execute("SELECT id, user_id, name, email, department, grp, supervisor "
                            "FROM person WHERE id=?", (pid,)).fetchone()
        return self._person(r) if r else None

    def person_for_user(self, user_id: str) -> Person | None:
        r = self.db.execute("SELECT id, user_id, name, email, department, grp, supervisor "
                            "FROM person WHERE user_id=?", (user_id,)).fetchone()
        return self._person(r) if r else None

    def delete_person(self, pid: int) -> None:
        for t in self.trainings(pid):
            for e in self.evidence(t.id):
                self.delete_evidence(e.id)
        self.db.execute("DELETE FROM person WHERE id=?", (pid,))
        self.db.commit()

    def names(self) -> dict[str, str]:
        """Anonymous id -> real name, for this machine's display only."""
        return {p.user_id: p.name for p in self.people() if p.name}

    # -- training ---------------------------------------------------------
    def save_training(self, t: Training) -> Training:
        vals = (t.person_id, t.instrument, t.status, self._enc_date(t.date_trained),
                self._enc(t.assessor), self._enc_date(t.expiry), int(t.refresher_months or 0),
                self._enc(t.notes))
        if t.status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        if t.id is None:
            cur = self.db.execute("INSERT INTO training (person_id, instrument, status, "
                                  "date_trained, assessor, expiry, refresher_months, notes) "
                                  "VALUES (?,?,?,?,?,?,?,?)", vals)
            t.id = cur.lastrowid
        else:
            self.db.execute("UPDATE training SET person_id=?, instrument=?, status=?, "
                            "date_trained=?, assessor=?, expiry=?, refresher_months=?, notes=? "
                            "WHERE id=?", (*vals, t.id))
        self.db.commit()
        return t

    def _training(self, r) -> Training:
        return Training(r[0], r[1], r[2], r[3], self._dec_date(r[4]), self._dec(r[5]),
                        self._dec_date(r[6]), r[7] or 0, self._dec(r[8]))

    def trainings(self, person_id: int | None = None) -> list[Training]:
        q = ("SELECT id, person_id, instrument, status, date_trained, assessor, expiry, "
             "refresher_months, notes FROM training")
        rows = self.db.execute(q + (" WHERE person_id=?" if person_id is not None else ""),
                               (() if person_id is None else (person_id,))).fetchall()
        return [self._training(r) for r in rows]

    def delete_training(self, tid: int) -> None:
        for e in self.evidence(tid):
            self.delete_evidence(e.id)
        self.db.execute("DELETE FROM training WHERE id=?", (tid,))
        self.db.commit()

    def permissions(self, person_id: int, today: date | None = None) -> tuple[str, ...]:
        today = today or date.today()
        return tuple(sorted({t.instrument for t in self.trainings(person_id) if t.active(today)}))

    def lapsing(self, within_days: int = 90, today: date | None = None) -> list[Lapse]:
        today = today or date.today()
        people = {p.id: p for p in self.people()}
        out = []
        for t in self.trainings():
            exp = t.effective_expiry()
            if t.status == "trained" and exp and exp <= today + timedelta(days=within_days):
                out.append(Lapse(people[t.person_id], t, exp, (exp - today).days))
        return sorted(out, key=lambda l: l.expiry)

    # -- evidence ---------------------------------------------------------
    def add_evidence(self, training_id: int, source: Path) -> Evidence:
        stored = f"{uuid.uuid4().hex}.bin"
        data = Path(source).read_bytes()
        (self.home / "evidence" / stored).write_bytes(self.fernet.encrypt(data))
        added = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cur = self.db.execute("INSERT INTO training_evidence (training_id, filename, stored_name, "
                              "added) VALUES (?,?,?,?)",
                              (training_id, self._enc(Path(source).name), stored, added))
        self.db.commit()
        return Evidence(cur.lastrowid, training_id, Path(source).name, stored, added)

    def evidence(self, training_id: int) -> list[Evidence]:
        rows = self.db.execute("SELECT id, training_id, filename, stored_name, added FROM "
                               "training_evidence WHERE training_id=?", (training_id,)).fetchall()
        return [Evidence(r[0], r[1], self._dec(r[2]), r[3], r[4]) for r in rows]

    def read_evidence(self, ev: Evidence) -> bytes:
        return self.fernet.decrypt((self.home / "evidence" / ev.stored_name).read_bytes())

    def delete_evidence(self, eid: int) -> None:
        r = self.db.execute("SELECT stored_name FROM training_evidence WHERE id=?", (eid,)).fetchone()
        if r:
            p = self.home / "evidence" / r[0]
            if p.exists():
                p.unlink()
        self.db.execute("DELETE FROM training_evidence WHERE id=?", (eid,))
        self.db.commit()

    # -- meta, backups ----------------------------------------------------
    def meta(self, key: str, default: str = "") -> str:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                        "SET value=excluded.value", (key, value))
        self.db.commit()

    def record_launch(self) -> int:
        n = int(self.meta("launches", "0")) + 1
        self.set_meta("launches", str(n))
        return n

    def backup_reminder(self, now: datetime | None = None) -> str:
        """Why a backup is due, or "" when it is not. Insistent on purpose:
        this data exists on one machine only."""
        now = now or datetime.now(timezone.utc)
        if not self.people():
            return ""
        last = self.meta("last_backup")
        if not last:
            return "Training records have never been backed up."
        age = (now - datetime.fromisoformat(last)).days
        if age > BACKUP_MAX_AGE_DAYS:
            return f"The last backup of training records is {age} days old."
        launches = int(self.meta("launches", "0"))
        if launches and launches % BACKUP_EVERY_LAUNCHES == 0:
            return f"Routine reminder: last backup {age} days ago."
        return ""

    def backup(self, target: Path, passphrase: str) -> Path:
        """Database, evidence and the key itself, the key wrapped with a
        passphrase so the backup restores on a new machine."""
        if len(passphrase) < 8:
            raise ValueError("use a passphrase of at least 8 characters")
        self.db.commit()
        buf = io.BytesIO()
        snap = sqlite3.connect(":memory:")
        self.db.backup(snap)
        db_bytes = snap.serialize()
        snap.close()
        salt = secrets.token_bytes(16)
        wrapped = Fernet(_derive(passphrase, salt)).encrypt(self.key)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps({
                "format": "khervelab-local-backup", "version": 1,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "salt": base64.b64encode(salt).decode(), "kdf": "pbkdf2-sha256-600000"}))
            z.writestr("key.enc", wrapped)
            z.writestr("local.db", db_bytes)
            for f in (self.home / "evidence").glob("*.bin"):
                z.write(f, f"evidence/{f.name}")
        target = Path(target)
        target.write_bytes(buf.getvalue())
        self.set_meta("last_backup", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return target

    @staticmethod
    def restore(source: Path, passphrase: str, home: Path | None = None,
                store_key=None) -> "LocalStore":
        """Restore into `home` (which must not hold a database). `store_key`
        saves the recovered key, by default into the OS keyring."""
        home = Path(home or default_home())
        if (home / "local.db").exists():
            raise FileExistsError(f"{home / 'local.db'} already exists; move it aside first")
        with zipfile.ZipFile(source) as z:
            manifest = json.loads(z.read("manifest.json"))
            if manifest.get("format") != "khervelab-local-backup":
                raise ValueError("not a KherveLAB backup")
            salt = base64.b64decode(manifest["salt"])
            try:
                key = Fernet(_derive(passphrase, salt)).decrypt(z.read("key.enc"))
            except InvalidToken:
                raise ValueError("wrong passphrase") from None
            home.mkdir(parents=True, exist_ok=True)
            (home / "evidence").mkdir(exist_ok=True)
            (home / "local.db").write_bytes(z.read("local.db"))
            for name in z.namelist():
                if name.startswith("evidence/") and name.endswith(".bin"):
                    (home / "evidence" / Path(name).name).write_bytes(z.read(name))
        if store_key is None:
            import keyring
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key.decode())
        else:
            store_key(key)
        return LocalStore(home, key)


def _derive(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
