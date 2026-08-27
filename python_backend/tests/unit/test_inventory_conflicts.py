"""Phase 1 — overlap prevention. The pure rule (reservation_conflicts) and
the server-side gate on create / update / approve, plus the CONFLICT alert.
Route functions are called directly with async fakes; no Mongo."""

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.core import config
from app.routers import inventory as inv
from app.services.inventory_service import alerts_for, reservation_conflicts
from models import User

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _personal_view_off(monkeypatch):
    """The overlap gate here is flag-independent, but the router paths these
    tests drive also carry flag-gated ownership/owner-key logic; pin the
    flag off (the dev .env now ships it true) so this file keeps asserting
    the pre-flag contract it was written for."""
    monkeypatch.setattr(config, "INVENTORY_PERSONAL_VIEW", False)

T = lambda d, h=9: datetime(2026, 8, d, h, 0)  # noqa: E731
ITEM = {"id": "LL-FOS-001", "name": "Interrogator", "kind": "equipment", "qty": 1}
SENSOR = {"id": "LL-SEN-004", "name": "EC5 sensor", "kind": "equipment", "qty": 3}
EPOXY = {"id": "LL-CON-008", "name": "Epoxy", "kind": "consumable", "qty": 3}


def _res(rid, item_id, start, end, status="Approved", qty=1, user="Saeed Mahjoubi"):
    return {"id": rid, "itemId": item_id, "start": start, "end": end, "status": status,
            "qty": qty, "user": user}


def _loan(tid, item_id, ts, due, qty=1, user="Yongxuan Gao"):
    return {"id": tid, "itemId": item_id, "type": "checkout", "ts": ts, "expectedReturn": due,
            "actualReturn": None, "qty": qty, "user": user}


# --- pure rule -----------------------------------------------------------------
def test_abutting_bookings_are_allowed_on_both_edges():
    existing = [_res("r1", ITEM["id"], T(23), T(24))]
    assert reservation_conflicts(ITEM, T(24), T(25), 1, [], existing) == []   # starts at its end
    assert reservation_conflicts(ITEM, T(22), T(23), 1, [], existing) == []   # ends at its start


def test_true_overlap_is_rejected_and_names_the_holder():
    existing = [_res("r1", ITEM["id"], T(23), T(24))]
    holders = reservation_conflicts(ITEM, T(23, 12), T(25), 1, [], existing)
    assert [h["user"] for h in holders] == ["Saeed Mahjoubi"]
    assert holders[0]["start"] == "2026-08-23 09:00" and holders[0]["end"] == "2026-08-24 09:00"


def test_partial_quantity_overlap_allowed_up_to_capacity():
    existing = [_res("r1", SENSOR["id"], T(23), T(26), qty=2)]
    assert reservation_conflicts(SENSOR, T(24), T(25), 1, [], existing) == []          # 2 + 1 <= 3
    assert reservation_conflicts(SENSOR, T(24), T(25), 2, [], existing) != []          # 2 + 2 > 3


def test_pending_reservations_also_commit_the_window_but_denied_do_not():
    pending = [_res("r1", ITEM["id"], T(23), T(24), status="Pending")]
    denied = [_res("r2", ITEM["id"], T(23), T(24), status="Denied")]
    assert reservation_conflicts(ITEM, T(23), T(24), 1, [], pending) != []
    assert reservation_conflicts(ITEM, T(23), T(24), 1, [], denied) == []


def test_open_loans_commit_equipment_but_not_consumables():
    loan = [_loan("t1", ITEM["id"], T(20), T(30))]
    assert reservation_conflicts(ITEM, T(23), T(24), 1, loan, []) != []
    # Open-ended loan (no expected return) blocks everything after it starts.
    open_ended = [_loan("t2", ITEM["id"], T(20), None)]
    assert reservation_conflicts(ITEM, T(23), T(24), 1, open_ended, []) != []
    # A loan due back BEFORE the window does not conflict (half-open).
    early = [_loan("t3", ITEM["id"], T(20), T(23))]
    assert reservation_conflicts(ITEM, T(23), T(24), 1, early, []) == []
    # Consumables are consumed at checkout; their checkout rows never block.
    assert reservation_conflicts(EPOXY, T(23), T(24), 1, [_loan("t4", EPOXY["id"], T(20), None, qty=3)], []) == []


def test_a_reservation_never_conflicts_with_itself():
    mine = [_res("r1", ITEM["id"], T(23), T(24))]
    assert reservation_conflicts(ITEM, T(23), T(24), 1, [], mine, exclude_id="r1") == []


# --- CONFLICT alert for pre-existing overlaps ----------------------------------
def test_alerts_flag_pre_existing_overlap_once_per_pair():
    res = [_res("r1", ITEM["id"], T(23), T(25), user="Saeed"),
           _res("r2", ITEM["id"], T(24), T(26), user="Jiming")]
    alerts = alerts_for([ITEM], [], res, [], T(21))
    conflicts = [a for a in alerts if a["kind"] == "conflict"]
    assert len(conflicts) == 1
    assert conflicts[0]["severity"] == "high" and conflicts[0]["itemId"] == "LL-FOS-001"
    assert "Saeed" in conflicts[0]["detail"] and "Jiming" in conflicts[0]["detail"]


# --- route-level gate ----------------------------------------------------------
class FakeColl:
    def __init__(self, docs=None, doc=None):
        self.docs = list(docs or [])
        self.doc = doc
        self.inserted, self.updated, self.queries = [], [], []

    async def find_one(self, query=None, *a, **k):
        self.queries.append(query)
        if self.doc is not None:
            return self.doc
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

    async def update_one(self, *a, **k):
        self.updated.append((a, k))


def _user(role="user"):
    return User(id="u1", email="x@uvic.ca", hashed_password="x", full_name="X", role=role)


@pytest.fixture()
def fakes(monkeypatch):
    items = FakeColl(docs=[ITEM])
    tx = FakeColl(docs=[])
    res = FakeColl(docs=[_res("r1", ITEM["id"], T(23), T(24), user="Saeed Mahjoubi")])
    users_coll = FakeColl(docs=[])
    audit = FakeColl()
    monkeypatch.setattr(inv, "inv_items_collection", items)
    monkeypatch.setattr(inv, "inv_tx_collection", tx)
    monkeypatch.setattr(inv, "inv_res_collection", res)
    monkeypatch.setattr(inv, "inv_users_collection", users_coll)
    monkeypatch.setattr(inv, "inv_audit_collection", audit)
    monkeypatch.setattr(inv, "_RESOURCES", {
        "items": (items, inv._ITEM_FIELDS), "tx": (tx, inv._TX_FIELDS),
        "res": (res, inv._RES_FIELDS), "users": (users_coll, inv._USER_FIELDS),
        "plaxis": (FakeColl(), inv._PLAXIS_FIELDS),
    })
    return {"items": items, "tx": tx, "res": res}


async def test_create_overlapping_reservation_is_409_naming_the_holder(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource(
            "res", {"itemId": ITEM["id"], "user": "Jiming Liu",
                    "start": "2026-08-23T12:00:00", "end": "2026-08-25T09:00:00"},
            current_user=_user(),
        )
    assert ei.value.status_code == 409
    assert "Saeed Mahjoubi" in ei.value.detail and "2026-08-23 09:00" in ei.value.detail
    assert fakes["res"].inserted == []  # nothing written


async def test_create_abutting_reservation_is_written(fakes):
    out = await inv.create_resource(
        "res", {"itemId": ITEM["id"], "user": "Jiming Liu",
                "start": "2026-08-24T09:00:00", "end": "2026-08-25T09:00:00"},
        current_user=_user(),
    )
    assert out["status"] == "Pending" and out["qty"] == 1
    assert len(fakes["res"].inserted) == 1


async def test_approve_after_conflict_is_409(fakes):
    # A Pending request that overlaps the approved r1 must not be approvable.
    pending = _res("r2", ITEM["id"], T(23, 12), T(25), status="Pending", user="Jiming Liu")
    pending["updatedAt"] = None
    fakes["res"].docs.append(pending)
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("res", "r2", {"status": "Approved"}, current_user=_user("professor"))
    assert ei.value.status_code == 409
    assert fakes["res"].updated == []


async def test_denying_a_conflicted_request_is_allowed(fakes):
    pending = _res("r2", ITEM["id"], T(23, 12), T(25), status="Pending", user="Jiming Liu")
    pending["updatedAt"] = None
    fakes["res"].docs.append(pending)
    out = await inv.update_resource("res", "r2", {"status": "Denied"}, current_user=_user("professor"))
    assert out["id"] == "r2" and fakes["res"].updated


async def test_moving_dates_into_a_conflict_is_409_but_editing_purpose_is_not(fakes):
    mine = _res("r3", ITEM["id"], T(26), T(27), status="Approved", user="Jiming Liu")
    mine["updatedAt"] = None
    fakes["res"].docs.append(mine)
    out = await inv.update_resource("res", "r3", {"purpose": "flume"}, current_user=_user())
    assert out["id"] == "r3"
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("res", "r3", {"start": "2026-08-23T10:00:00", "end": "2026-08-23T18:00:00"},
                                  current_user=_user())
    assert ei.value.status_code == 409
