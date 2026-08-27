"""Phase 5 — archive instead of delete."""

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.routers import inventory as inv
from app.services.inventory_service import (
    ItemRequest, alerts_for, compute_feasibility, render_snapshot,
)
from models import User

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 21, 12, 0)
LIVE = {"id": "LL-A", "name": "Live probe", "kind": "equipment", "qty": 1, "qtyOut": 0, "status": "Available"}
ARCH = {"id": "LL-Z", "name": "Old logger", "kind": "consumable", "qty": 0, "minStock": 2,
        "status": "Archived", "archivedFrom": "Depleted", "condition": "Damaged"}


def _user(role="user"):
    return User(id="u1", email="x@uvic.ca", hashed_password="x", full_name="X", role=role)


class FakeColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserted, self.updated = [], []

    async def find_one(self, query=None, *a, **k):
        for d in self.docs:
            if all(d.get(kk) == vv for kk, vv in (query or {}).items() if not isinstance(vv, dict)):
                return d
        return None

    def find(self, query=None, *a, **k):
        docs = self.docs

        async def gen():
            for d in docs:
                yield d
        return gen()

    async def insert_one(self, d):
        self.inserted.append(d)

    async def update_one(self, flt, update, *a, **k):
        self.updated.append((flt, update))
        for d in self.docs:
            if d.get("id") == flt.get("id"):
                d.update(update.get("$set", {}))
                for key in update.get("$unset", {}):
                    d.pop(key, None)


@pytest.fixture()
def fakes(monkeypatch):
    items = FakeColl(docs=[dict(LIVE), dict(ARCH)])
    tx, res, users, audit = FakeColl(), FakeColl(), FakeColl(), FakeColl()
    for name, coll in [("inv_items_collection", items), ("inv_tx_collection", tx),
                       ("inv_res_collection", res), ("inv_users_collection", users),
                       ("inv_audit_collection", audit)]:
        monkeypatch.setattr(inv, name, coll)
    monkeypatch.setattr(inv, "_RESOURCES", {
        "items": (items, inv._ITEM_FIELDS), "tx": (tx, inv._TX_FIELDS),
        "res": (res, inv._RES_FIELDS), "users": (users, inv._USER_FIELDS),
        "plaxis": (FakeColl(), inv._PLAXIS_FIELDS),
    })
    return {"items": items, "tx": tx, "res": res, "audit": audit}


async def test_archive_is_manager_only(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.archive_item("LL-A", current_user=_user("user"))
    assert ei.value.status_code == 403


async def test_archive_then_restore_round_trips_and_is_audited(fakes):
    out = await inv.archive_item("LL-A", current_user=_user("professor"))
    assert out["status"] == "Archived" and out["archivedFrom"] == "Available"
    assert fakes["audit"].inserted[-1]["action"] == "archive_items"
    back = await inv.restore_item("LL-A", current_user=_user("admin"))
    assert back["status"] == "Available" and "archivedFrom" not in back
    assert fakes["audit"].inserted[-1]["action"] == "restore_items"


async def test_restore_uses_the_prior_status(fakes):
    back = await inv.restore_item("LL-Z", current_user=_user("professor"))
    assert back["status"] == "Depleted"


async def test_put_status_archived_requires_manager(fakes):
    fakes["items"].docs[0]["updatedAt"] = None
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("items", "LL-A", {"status": "Archived"}, current_user=_user("user"))
    assert ei.value.status_code == 403


async def test_archived_item_cannot_be_checked_out_or_reserved(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("tx", {"type": "checkout", "itemId": "LL-Z", "qty": 1},
                                  current_user=_user("user"))
    assert ei.value.status_code == 409 and "archived" in ei.value.detail
    with pytest.raises(HTTPException) as ei2:
        await inv.create_resource("res", {"itemId": "LL-Z", "user": "X", "start": "2026-09-01T09:00:00",
                                          "end": "2026-09-02T09:00:00"}, current_user=_user("user"))
    assert ei2.value.status_code == 409 and "archived" in ei2.value.detail


def test_archived_items_leave_alerts_snapshot_and_feasibility():
    # ARCH would otherwise fire low_stock (0 <= 2) and damaged alerts.
    assert [a for a in alerts_for([LIVE, ARCH], [], [], [], NOW) if a["itemId"] == "LL-Z"] == []
    snap = render_snapshot({"items": [LIVE, ARCH], "open_loans": [], "reservations": [], "plaxis": []},
                           "ITEMS", NOW, cap_tokens=4000)
    assert "Old logger" not in snap.text and "Live probe" in snap.text
    report = compute_feasibility([ItemRequest("LL-Z", 1)], NOW, NOW, [LIVE, ARCH], [], [], now=NOW)
    assert report.items[0].status == "unknown_item"
