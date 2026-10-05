from __future__ import annotations

import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from khervelab.core.facility import FacilityService
from khervelab.local.store import LocalStore, Person, Training
from khervelab.local.training import (export_person_pdf, new_person, reminder_mailto,
                                      sync_all, sync_permissions)


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "local", Fernet.generate_key())
    yield s
    s.close()


@pytest.fixture
def svc(facility):
    return FacilityService(facility)


def test_personal_columns_are_encrypted_on_disk(store):
    store.save_person(Person(None, "u-0009", "Ada Lovelace", "ada@imperial.ac.uk",
                             "Materials", "Surfaces", "Prof X"))
    store.db.commit()
    raw = store.db_path.read_bytes()
    assert b"Ada Lovelace" not in raw and b"ada@imperial" not in raw and b"Prof X" not in raw
    assert store.people()[0].name == "Ada Lovelace"


def test_wrong_key_cannot_read(store, tmp_path):
    store.save_person(Person(None, "u-0009", "Ada"))
    store.close()
    other = LocalStore(tmp_path / "local", Fernet.generate_key())
    with pytest.raises(ValueError, match="wrong key"):
        other.people()


def test_training_drives_permissions_and_no_name_reaches_git(svc, store):
    p = new_person(svc, store, "Grace Hopper", "GH group", email="grace@imperial.ac.uk",
                   supervisor="Dr Smith")
    store.save_training(Training(None, p.id, "xps", "trained", date.today(), "G. Kerherve",
                                 refresher_months=12, notes="Competent on charge neutraliser"))
    assert sync_permissions(svc, store, p) is True
    assert svc.users[p.user_id].permissions == ("xps",)
    assert svc.repo.git.head.commit.message.strip() == f"permissions {p.user_id}"
    assert sync_permissions(svc, store, p) is False
    history = subprocess.run(["git", "-C", str(svc.repo.path), "log", "-p", "--all"],
                             capture_output=True, text=True, check=True).stdout
    for secret in ("Grace", "Hopper", "grace@", "Smith", "Kerherve", "neutraliser"):
        assert secret not in history


def test_expired_training_is_revoked(svc, store):
    p = new_person(svc, store, "Old Timer", "OT")
    store.save_training(Training(None, p.id, "xps", "trained", date(2024, 1, 10),
                                 refresher_months=12))
    store.save_training(Training(None, p.id, "bet", "trained", date.today()))
    assert sync_all(svc, store) == 1
    assert svc.users[p.user_id].permissions == ("bet",)


def test_lapsing_dashboard(store):
    p = store.save_person(Person(None, "u-0001", "Soon", "soon@x.ac.uk"))
    today = date(2026, 10, 5)
    store.save_training(Training(None, p.id, "xps", "trained", expiry=today + timedelta(days=20)))
    store.save_training(Training(None, p.id, "bet", "trained", expiry=today + timedelta(days=200)))
    (lapse,) = store.lapsing(90, today)
    assert lapse.training.instrument == "xps" and lapse.days_left == 20


def test_mailto_draft(svc, store):
    p = store.save_person(Person(None, "u-0001", "Soon", "soon@x.ac.uk"))
    store.save_training(Training(None, p.id, "xps", "trained",
                                 expiry=date.today() + timedelta(days=5)))
    url = reminder_mailto(store.lapsing(), "Materials", svc)
    assert url.startswith("mailto:?bcc=soon%40x.ac.uk") and "High-throughput%20XPS" in url


def test_evidence_encrypted(store, tmp_path):
    p = store.save_person(Person(None, "u-0001", "A"))
    t = store.save_training(Training(None, p.id, "xps"))
    f = tmp_path / "form.txt"
    f.write_text("signed competency form")
    ev = store.add_evidence(t.id, f)
    assert b"competency" not in (store.home / "evidence" / ev.stored_name).read_bytes()
    assert store.read_evidence(store.evidence(t.id)[0]) == b"signed competency form"
    assert store.evidence(t.id)[0].filename == "form.txt"


def test_backup_restore_round_trip(store, tmp_path):
    p = store.save_person(Person(None, "u-0001", "Backed Up", "b@x.ac.uk"))
    t = store.save_training(Training(None, p.id, "xps"))
    f = tmp_path / "e.pdf"
    f.write_bytes(b"%PDF evidence")
    store.add_evidence(t.id, f)
    bk = store.backup(tmp_path / "backup.zip", "correct horse")
    assert store.backup_reminder() == ""
    keys = []
    with pytest.raises(ValueError, match="wrong passphrase"):
        LocalStore.restore(bk, "nope nope nope", tmp_path / "r1", store_key=keys.append)
    restored = LocalStore.restore(bk, "correct horse", tmp_path / "r2", store_key=keys.append)
    assert restored.people()[0].email == "b@x.ac.uk"
    (ev,) = restored.evidence(restored.trainings()[0].id)
    assert restored.read_evidence(ev) == b"%PDF evidence"
    assert keys == [store.key]


def test_backup_reminders(store):
    assert store.backup_reminder() == ""  # nothing to lose yet
    store.save_person(Person(None, "u-0001", "A"))
    assert "never" in store.backup_reminder()
    store.set_meta("last_backup", (datetime.now(timezone.utc) - timedelta(days=45)).isoformat())
    assert "45 days" in store.backup_reminder()
    store.set_meta("last_backup", datetime.now(timezone.utc).isoformat())
    store.set_meta("launches", "9")
    assert store.backup_reminder() == ""
    store.record_launch()
    assert "Routine" in store.backup_reminder()


def test_person_pdf(svc, store, tmp_path):
    p = new_person(svc, store, "Pdf Person", "PP", email="pp@x.ac.uk")
    store.save_training(Training(None, p.id, "xps", "trained", date.today(), "Assessor"))
    out = export_person_pdf(svc, store, p, tmp_path / "record.pdf")
    assert out.read_bytes()[:4] == b"%PDF"
