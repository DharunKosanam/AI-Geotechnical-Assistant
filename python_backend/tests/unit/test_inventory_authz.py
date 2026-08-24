"""Phase-6 cleanup unit tests: open access with exactly two manager-gated
actions (delete items/users, reservation approval) enforced SERVER-side, and
the roster-resolved studentId/group on transactions. Route functions are
called directly with fake collections — no Mongo, no HTTP server."""

import re

import pytest
from fastapi import HTTPException

from app.routers import inventory as inv
from models import User

pytestmark = pytest.mark.unit


def _user(role: str, email: str = "dharunk@uvic.ca") -> User:
    return User(id="u1", email=email, hashed_password="x",
                full_name="Dharun Kosanam", role=role)


class _Result:
    deleted_count = 1


class FakeColl:
    """Minimal async collection double; records find_one queries so tests can
    assert WHICH key a roster join used."""

    def __init__(self, doc=None):
        self.doc = doc
        self.inserted = []
        self.updated = []
        self.queries = []

    async def find_one(self, query=None, *a, **k):
        self.queries.append(query)
        return self.doc

    async def insert_one(self, d):
        self.inserted.append(d)

    async def update_one(self, *a, **k):
        self.updated.append((a, k))

    async def delete_one(self, *a, **k):
        return _Result()


@pytest.fixture()
def fakes(monkeypatch):
    """Swap every collection the touched routes reach for fakes."""
    items = FakeColl(doc=None)
    tx = FakeColl()
    res = FakeColl(doc={"id": "RS-1", "status": "Pending", "updatedAt": None})
    users_coll = FakeColl(doc=None)
    audit = FakeColl()
    monkeypatch.setattr(inv, "inv_items_collection", items)
    monkeypatch.setattr(inv, "inv_tx_collection", tx)
    monkeypatch.setattr(inv, "inv_users_collection", users_coll)
    monkeypatch.setattr(inv, "inv_audit_collection", audit)
    monkeypatch.setattr(inv, "_RESOURCES", {
        "items": (items, inv._ITEM_FIELDS),
        "tx": (tx, inv._TX_FIELDS),
        "res": (res, inv._RES_FIELDS),
        "users": (users_coll, inv._USER_FIELDS),
        "plaxis": (FakeColl(), inv._PLAXIS_FIELDS),
    })
    return {"items": items, "tx": tx, "res": res, "users": users_coll, "audit": audit}


# --- the two retained gates, enforced server-side ---------------------------
@pytest.mark.parametrize("resource", ["items", "users"])
async def test_delete_items_and_users_403_for_non_manager(resource):
    # Gate fires BEFORE any DB access — no fakes needed.
    with pytest.raises(HTTPException) as ei:
        await inv.delete_resource(resource, "X-1", current_user=_user("user"))
    assert ei.value.status_code == 403


@pytest.mark.parametrize("role", ["admin", "professor"])
async def test_delete_items_allowed_for_managers(role, fakes):
    out = await inv.delete_resource("items", "LL-X", current_user=_user(role))
    assert out == {"deleted": "LL-X"}


async def test_delete_reservation_stays_open_to_everyone(fakes):
    # A cancel is a res delete — NOT one of the two retained gates.
    out = await inv.delete_resource("res", "RS-1", current_user=_user("user"))
    assert out == {"deleted": "RS-1"}


async def test_approve_via_put_403_for_non_manager():
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("res", "RS-1", {"status": "Approved"},
                                  current_user=_user("user"))
    assert ei.value.status_code == 403


async def test_approve_via_put_allowed_for_manager(fakes):
    out = await inv.update_resource("res", "RS-1", {"status": "Approved"},
                                    current_user=_user("professor"))
    assert out["id"] == "RS-1"
    assert fakes["res"].updated  # the write happened


async def test_non_status_res_edit_stays_open(fakes):
    # Editing your own reservation's purpose is NOT approval — no gate.
    out = await inv.update_resource("res", "RS-1", {"purpose": "flume"},
                                    current_user=_user("user"))
    assert out["id"] == "RS-1"


async def test_create_res_pre_approved_403_for_non_manager(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("res", {"itemId": "LL-X", "status": "Approved"},
                                  current_user=_user("user"))
    assert ei.value.status_code == 403


async def test_create_pending_res_open_to_everyone(fakes):
    out = await inv.create_resource("res", {"itemId": "LL-X", "status": "Pending"},
                                    current_user=_user("user"))
    assert out["status"] == "Pending"
    assert fakes["res"].inserted


# --- newly ungated actions ----------------------------------------------------
async def test_item_create_and_edit_open_to_plain_users(fakes):
    created = await inv.create_resource(
        "items", {"id": "LL-NEW", "name": "Probe", "qty": 1},
        current_user=_user("user"),
    )
    assert created["id"] == "LL-NEW"
    fakes["items"].doc = {"id": "LL-NEW", "name": "Probe", "updatedAt": None}
    updated = await inv.update_resource("items", "LL-NEW", {"location": "Shelf 2"},
                                        current_user=_user("user"))
    assert updated["id"] == "LL-NEW"


async def test_user_create_open_to_plain_users(fakes):
    out = await inv.create_resource("users", {"name": "New Member"},
                                    current_user=_user("user"))
    assert out["name"] == "New Member"


# --- roster-resolved studentId / group on transactions ------------------------
async def test_tx_carries_roster_student_id_and_ignores_client_value(fakes):
    fakes["items"].doc = {"id": "LL-FOS-001", "kind": "equipment", "qty": 1, "qtyOut": 0}
    fakes["users"].doc = {"studentId": "V00891234", "group": "Lin Lab"}
    out = await inv.create_resource(
        "tx",
        {"type": "checkout", "itemId": "LL-FOS-001", "qty": 1,
         # Client-supplied identity must be overwritten, never trusted.
         "studentId": "V99999999", "group": "Spoofed Group"},
        current_user=_user("user"),
    )
    assert out["studentId"] == "V00891234"
    assert out["group"] == "Lin Lab"
    stored = fakes["tx"].inserted[0]
    assert stored["studentId"] == "V00891234" and stored["group"] == "Lin Lab"


async def test_tx_joins_on_the_form_email_for_on_behalf(fakes):
    # A manager checking out FOR someone: the roster join must use the form's
    # (borrower's) email, so the record names the actual borrower — not the
    # session user.
    fakes["items"].doc = {"id": "LL-FOS-001", "kind": "equipment", "qty": 1, "qtyOut": 0}
    fakes["users"].doc = {"studentId": "V00555555", "group": "Lin Lab — DFOS"}
    out = await inv.create_resource(
        "tx",
        {"type": "checkout", "itemId": "LL-FOS-001", "qty": 1,
         "user": "Yongxuan Gao", "email": "asaff@live.cn"},
        current_user=_user("professor", email="clin@uvic.ca"),
    )
    assert out["studentId"] == "V00555555"
    joined = fakes["users"].queries[-1]
    assert joined == {"email": {"$regex": "^" + re.escape("asaff@live.cn") + "$", "$options": "i"}}


async def test_tx_blank_form_email_falls_back_to_session_email(fakes):
    fakes["items"].doc = {"id": "LL-FOS-001", "kind": "equipment", "qty": 1, "qtyOut": 0}
    fakes["users"].doc = {"studentId": "V00891234", "group": "Lin Lab"}
    await inv.create_resource(
        "tx", {"type": "checkout", "itemId": "LL-FOS-001", "qty": 1, "email": "  "},
        current_user=_user("user", email="dharunk@uvic.ca"),
    )
    assert fakes["users"].queries[-1] == {
        "email": {"$regex": "^" + re.escape("dharunk@uvic.ca") + "$", "$options": "i"}
    }


async def test_tx_unresolvable_email_writes_null_without_erroring(fakes):
    fakes["items"].doc = {"id": "LL-FOS-001", "kind": "equipment", "qty": 1, "qtyOut": 0}
    fakes["users"].doc = None  # no roster row for this email
    out = await inv.create_resource(
        "tx", {"type": "checkout", "itemId": "LL-FOS-001", "qty": 1,
               "email": "visitor@example.org"},
        current_user=_user("user", email="nobody@uvic.ca"),
    )
    assert out["studentId"] is None
    assert out["group"] is None


# --- roster-resolved group on reservations and PLAXIS sessions ----------------
async def test_res_group_resolved_by_name_and_client_value_ignored(fakes):
    fakes["users"].doc = {"studentId": "", "group": "Lin Lab — Geogrid"}
    out = await inv.create_resource(
        "res",
        {"itemId": "LL-FOS-001", "user": "Jiming Liu", "group": "Spoofed",
         "status": "Pending"},
        current_user=_user("user"),
    )
    assert out["group"] == "Lin Lab — Geogrid"
    # The join keyed on the NAMED person (inv_res has no email column).
    assert fakes["users"].queries[-1] == {
        "name": {"$regex": "^" + re.escape("Jiming Liu") + "$", "$options": "i"}
    }


async def test_plaxis_group_resolved_by_name_and_client_value_ignored(fakes):
    fakes["users"].doc = {"studentId": "", "group": "Lin Lab — Numerical"}
    out = await inv.create_resource(
        "plaxis",
        {"seat": 0, "user": "Shane Smith", "group": "Spoofed", "loggedOut": False},
        current_user=_user("user"),
    )
    assert out["group"] == "Lin Lab — Numerical"
    assert fakes["users"].queries[-1] == {
        "name": {"$regex": "^" + re.escape("Shane Smith") + "$", "$options": "i"}
    }


async def test_res_unknown_name_writes_null_group_without_erroring(fakes):
    fakes["users"].doc = None
    out = await inv.create_resource(
        "res", {"itemId": "LL-FOS-001", "user": "A Visitor", "group": "Typed"},
        current_user=_user("user"),
    )
    assert out["group"] is None


async def test_roster_lookup_blank_keys_resolve_null(fakes):
    assert await inv._roster_lookup("") == {"studentId": None, "group": None}
    assert await inv._roster_lookup("   ", "  ") == {"studentId": None, "group": None}
    # And with no key at all, the roster is never queried.
    assert fakes["users"].queries == []
