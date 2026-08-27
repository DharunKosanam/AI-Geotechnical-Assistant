"""Reserved is DERIVED from live inv_res rows, never trusted from the stored
item status (the LL-SEN-004 incident: seeded "Reserved", zero reservations).

  - an item with an active (or upcoming) non-denied reservation reads Reserved
  - denying, cancelling, deleting or expiring the LAST reservation clears it
  - a stored "Reserved" with nothing behind it reads Available
  - non-reservable statuses (maintenance, borrowed, missing, archived) are untouched
  - the write path never stores "Reserved"; the reconcile plan finds stale rows
  - GET /items and PUT /items/{id} serve the derived view
"""
from datetime import datetime, timedelta

import pytest

from app.routers import inventory as inv
from app.services.inventory_service import (
    derive_reserved,
    reconcile_status_plan,
    strip_stored_reserved,
)

NOW = datetime(2026, 8, 24, 12, 0)


def _item(status="Available", id="LL-SEN-004"):
    return {"id": id, "name": "EC5 soil moisture sensor", "status": status, "qty": 5, "qtyOut": 0}


def _res(status="Approved", start=NOW + timedelta(days=1), end=NOW + timedelta(days=3), item="LL-SEN-004"):
    return {"id": "r1", "itemId": item, "user": "Jiming Liu", "status": status, "start": start, "end": end}


def _status(items, res):
    return derive_reserved(items, res, NOW)[0]["status"]


# --- derivation ------------------------------------------------------------
def test_active_reservation_reads_reserved():
    assert _status([_item("Available")], [_res()]) == "Reserved"


def test_reservation_in_progress_reads_reserved():
    assert _status([_item()], [_res(start=NOW - timedelta(days=1), end=NOW + timedelta(days=1))]) == "Reserved"


def test_pending_reservation_also_reserves():
    # Pending is a live claim on the item until a manager denies it.
    assert _status([_item()], [_res(status="Pending")]) == "Reserved"


def test_stored_reserved_with_no_rows_reads_available():
    # The LL-SEN-004 case exactly.
    assert _status([_item("Reserved")], []) == "Available"


@pytest.mark.parametrize("dead", ["Denied", "Cancelled"])
def test_denying_or_cancelling_last_reservation_clears(dead):
    assert _status([_item("Reserved")], [_res(status=dead)]) == "Available"


def test_deleting_last_reservation_clears():
    rows = [_res()]
    assert _status([_item()], rows) == "Reserved"
    rows.clear()  # the row is gone
    assert _status([_item("Reserved")], rows) == "Available"


def test_expired_reservation_clears():
    assert _status([_item("Reserved")], [_res(start=NOW - timedelta(days=5), end=NOW - timedelta(days=2))]) == "Available"


def test_reservation_on_another_item_does_not_reserve():
    assert _status([_item()], [_res(item="LL-FOS-001")]) == "Available"


@pytest.mark.parametrize("status", ["Under maintenance", "Borrowed", "In use", "Missing", "Archived", "Retired"])
def test_non_reservable_statuses_are_left_alone(status):
    assert _status([_item(status)], [_res()]) == status
    assert _status([_item(status)], []) == status


def test_derivation_does_not_mutate_inputs():
    items = [_item("Reserved")]
    out = derive_reserved(items, [], NOW)
    assert items[0]["status"] == "Reserved" and out[0]["status"] == "Available"
    assert out[0] is not items[0]


# --- write guard + reconcile plan -----------------------------------------
def test_write_path_never_stores_reserved():
    doc = {"id": "x", "status": "Reserved"}
    strip_stored_reserved(doc)
    assert doc["status"] == "Available"
    other = {"id": "y", "status": "Under maintenance"}
    strip_stored_reserved(other)
    assert other["status"] == "Under maintenance"
    strip_stored_reserved({})  # no status: no-op, no KeyError


def test_reconcile_plan_reports_stale_and_missing_rows():
    items = [_item("Reserved"), _item("Available", id="LL-FOS-001"), _item("Borrowed", id="LL-X")]
    plan = reconcile_status_plan(items, [_res(item="LL-FOS-001")], NOW)
    by_id = {p["id"]: p for p in plan}
    assert by_id["LL-SEN-004"] == {"id": "LL-SEN-004", "name": "EC5 soil moisture sensor",
                                   "stored": "Reserved", "derived": "Available", "live": 0}
    assert by_id["LL-FOS-001"]["stored"] == "Available" and by_id["LL-FOS-001"]["derived"] == "Reserved"
    assert by_id["LL-FOS-001"]["live"] == 1
    assert "LL-X" not in by_id  # borrowed: never rewritten, never reported


def test_reconcile_plan_is_empty_when_consistent():
    assert reconcile_status_plan([_item("Available")], [], NOW) == []
    assert reconcile_status_plan([_item("Reserved")], [_res()], NOW) == []


# --- API surface -----------------------------------------------------------
class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, *_):
        return self

    def sort(self, *_):
        return self

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield dict(d)
        return gen()


class _Coll:
    def __init__(self, docs):
        self.docs = docs
        self.updates = []

    def find(self, query=None, *_a, **_k):
        q = query or {}
        out = self.docs
        if "itemId" in q and isinstance(q["itemId"], dict):
            wanted = set(q["itemId"]["$in"])
            out = [d for d in out if d.get("itemId") in wanted]
        return _Cursor(out)

    async def find_one(self, query=None, *_a, **_k):
        for d in self.docs:
            if d.get("id") == (query or {}).get("id"):
                return dict(d)
        return None

    async def update_one(self, q, u, *_a, **_k):
        self.updates.append((q, u))
        for d in self.docs:
            if d.get("id") == q.get("id"):
                d.update(u.get("$set", {}))

    async def insert_one(self, d):
        self.docs.append(dict(d))


@pytest.fixture()
def api(monkeypatch):
    items = _Coll([_item("Reserved")])
    res = _Coll([])
    audit = _Coll([])
    monkeypatch.setattr(inv, "inv_items_collection", items)
    monkeypatch.setattr(inv, "inv_res_collection", res)
    monkeypatch.setattr(inv, "inv_audit_collection", audit)
    monkeypatch.setattr(inv, "_RESOURCES", {"items": (items, inv._ITEM_FIELDS), "res": (res, inv._RES_FIELDS)})
    return items, res


class _User:
    id = "u1"
    email = "clin@uvic.ca"
    full_name = "Dr. Cheng Lin"
    role = "professor"


async def test_get_items_serves_derived_status(api):
    items, res = api
    out = await inv.list_resource("items", itemId=None, open_only=False, limit=100, current_user=_User())
    assert out["items"][0]["status"] == "Available"  # stored Reserved, no rows
    assert items.docs[0]["status"] == "Reserved"  # read path does not write
    res.docs.append(_res(end=datetime.now() + timedelta(days=2)))
    out = await inv.list_resource("items", itemId=None, open_only=False, limit=100, current_user=_User())
    assert out["items"][0]["status"] == "Reserved"


async def test_put_items_strips_reserved_and_returns_derived(api):
    items, _res_coll = api
    updated = await inv.update_resource("items", "LL-SEN-004", {"status": "Reserved", "location": "Bench 2"},
                                        current_user=_User())
    # Stored: Available (never Reserved); returned view: Available (no live rows).
    assert items.docs[0]["status"] == "Available"
    assert updated["status"] == "Available"
    assert updated["location"] == "Bench 2"
