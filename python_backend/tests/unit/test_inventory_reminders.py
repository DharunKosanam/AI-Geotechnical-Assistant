"""Phase 9 — reminder digests with a pinned clock and a fake sender. No real
email is ever sent: the sender is injected."""

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.core import config
from app.routers import inventory as inv
from app.services import inventory_reminders as rem
from models import User

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 21, 8, 0)
ITEMS = [{"id": "LL-FOS-001", "name": "Interrogator", "kind": "equipment", "qty": 1},
         {"id": "LL-SEN-004", "name": "EC5 sensor", "kind": "equipment", "qty": 5}]
USERS = [{"id": "U-4", "name": "Saeed Mahjoubi", "email": "mahjoubi@gmail.com"},
         {"id": "U-5", "name": "Yongxuan Gao", "email": "asaff@live.cn"},
         {"id": "U-15", "name": "Shane Smith", "email": ""}]


def _loan(tid, item, user, due, email="", qty=1):
    return {"id": tid, "itemId": item, "type": "checkout", "user": user, "email": email, "qty": qty,
            "ts": NOW - timedelta(days=5), "expectedReturn": due, "actualReturn": None}


def _res(rid, item, user, start, status="Approved"):
    return {"id": rid, "itemId": item, "user": user, "start": start, "end": start + timedelta(hours=8), "status": status}


class FakeSender:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def send_email(self, to, subject, html, text):
        self.sent.append((to, subject, text))
        return self.ok


class FakeReminders:
    def __init__(self):
        self.rows = []

    async def find_one(self, q, *a, **k):
        return next((r for r in self.rows if r["email"] == q["email"] and r["day"] == q["day"]), None)

    async def insert_one(self, d):
        self.rows.append(d)


# --- pure digest building --------------------------------------------------------
def test_digests_group_per_user_with_24h_half_open_windows_and_roster_emails():
    loans = [
        _loan("t1", "LL-FOS-001", "Yongxuan Gao", NOW - timedelta(days=3), email="asaff@live.cn"),   # overdue
        _loan("t2", "LL-SEN-004", "Saeed Mahjoubi", NOW + timedelta(hours=23)),                       # due soon, email via roster
        _loan("t3", "LL-SEN-004", "Saeed Mahjoubi", NOW + timedelta(hours=24)),                       # exactly 24h -> NOT due soon
        _loan("t4", "LL-SEN-004", "Shane Smith", NOW + timedelta(hours=2)),                           # no email anywhere -> skipped
    ]
    res = [
        _res("r1", "LL-FOS-001", "Saeed Mahjoubi", NOW + timedelta(hours=1)),                         # upcoming
        _res("r2", "LL-FOS-001", "Saeed Mahjoubi", NOW + timedelta(hours=30)),                        # beyond 24h
        _res("r3", "LL-FOS-001", "Yongxuan Gao", NOW + timedelta(hours=1), status="Pending"),         # not approved
        _res("r4", "LL-FOS-001", "Yongxuan Gao", NOW - timedelta(hours=1)),                           # already started
    ]
    d = rem.build_digests(ITEMS, loans, res, USERS, NOW)
    assert set(d) == {"asaff@live.cn", "mahjoubi@gmail.com"}
    gao = d["asaff@live.cn"]
    assert [x["item"] for x in gao.overdue] == ["Interrogator"] and gao.overdue[0]["days"] == 3
    assert gao.due_soon == [] and gao.upcoming == []
    saeed = d["mahjoubi@gmail.com"]
    assert [x["item"] for x in saeed.due_soon] == ["EC5 sensor"]
    assert [x["item"] for x in saeed.upcoming] == ["Interrogator"]


def test_render_digest_is_plain_and_counts_everything():
    d = rem.Digest(email="a@b.c", name="Ana", overdue=[{"item": "X", "qty": 2, "due": "2026-08-18", "days": 3}],
                   upcoming=[{"item": "Y", "start": "2026-08-21 10:00", "end": "2026-08-21 18:00", "purpose": "flume"}])
    subject, html, text = rem.render_digest(d, NOW)
    assert subject == "Lin Lab inventory: 2 reminders for 2026-08-21"
    assert "X ×2, due 2026-08-18 (3 d overdue)" in text and "Y: 2026-08-21 10:00" in text
    assert "<li>" in html and "Ana" in html


# --- idempotent run --------------------------------------------------------------
@pytest.fixture()
def wired(monkeypatch):
    store = FakeReminders()
    import app.core.database as dbmod
    monkeypatch.setattr(dbmod, "inv_reminders_collection", store)
    data = {
        "items": ITEMS, "reservations": [],
        "open_loans": [_loan("t1", "LL-FOS-001", "Yongxuan Gao", NOW - timedelta(days=3), email="asaff@live.cn"),
                       _loan("t2", "LL-SEN-004", "Saeed Mahjoubi", NOW + timedelta(hours=3))],
        "users": USERS,
    }
    return store, data


async def test_one_email_per_user_per_day_and_never_twice(wired):
    store, data = wired
    sender = FakeSender()
    first = await rem.run_reminders(now=NOW, sender=sender, data=data)
    assert first["sent"] == ["asaff@live.cn", "mahjoubi@gmail.com"] and first["candidates"] == 2
    assert len(sender.sent) == 2 and all(s[1].startswith("Lin Lab inventory:") for s in sender.sent)
    # Same day again (e.g. after a restart): recorded sends are skipped.
    second = await rem.run_reminders(now=NOW + timedelta(hours=5), sender=sender, data=data)
    assert second["sent"] == [] and sorted(second["skipped"]) == ["asaff@live.cn", "mahjoubi@gmail.com"]
    assert len(sender.sent) == 2
    # Next day: a fresh digest goes out.
    third = await rem.run_reminders(now=NOW + timedelta(days=1), sender=sender, data=data)
    assert len(third["sent"]) == 2 and len(sender.sent) == 4
    assert len(store.rows) == 4


async def test_dry_run_sends_and_records_nothing(wired):
    store, data = wired
    sender = FakeSender()
    out = await rem.run_reminders(now=NOW, sender=sender, data=data, dry_run=True)
    assert out["dryRun"] is True and len(out["sent"]) == 2
    assert sender.sent == [] and store.rows == []


async def test_failed_send_is_reported_and_not_recorded_so_it_retries(wired):
    store, data = wired
    out = await rem.run_reminders(now=NOW, sender=FakeSender(ok=False), data=data)
    assert len(out["failed"]) == 2 and store.rows == []


# --- endpoint gates ----------------------------------------------------------------
def _user(role="user"):
    return User(id="u1", email="x@uvic.ca", hashed_password="x", full_name="X", role=role)


async def test_endpoint_404_when_flag_off_and_403_for_non_manager(monkeypatch):
    monkeypatch.setattr(config, "INVENTORY_REMINDERS_ENABLED", False)
    with pytest.raises(HTTPException) as ei:
        await inv.run_reminders_now(dryRun=True, current_user=_user("professor"))
    assert ei.value.status_code == 404
    monkeypatch.setattr(config, "INVENTORY_REMINDERS_ENABLED", True)
    with pytest.raises(HTTPException) as ei2:
        await inv.run_reminders_now(dryRun=True, current_user=_user("user"))
    assert ei2.value.status_code == 403
