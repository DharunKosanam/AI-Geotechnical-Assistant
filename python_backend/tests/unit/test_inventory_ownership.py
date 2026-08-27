"""INVENTORY_PERSONAL_VIEW ownership boundary, unit-tested the authz way:
route functions called directly with fake collections — no Mongo, no HTTP.

Owner-only rows: open loans (inv_tx), reservations (inv_res), PLAXIS sessions
(inv_plaxis). Managers bypass ONLY on returns and PLAXIS release (audited with
actor and owner recorded separately); reservation edits and cancels have no
bypass. Flag OFF: every handler behaves exactly as before this feature — the
last test class pins that.
"""

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException

from app.core import config
from app.routers import inventory as inv
from models import User

pytestmark = pytest.mark.unit

OWNER = User(id="u1", email="jiming@uvic.ca", hashed_password="x",
             full_name="Jiming Liu", role="user")
OTHER = User(id="u2", email="subash@uvic.ca", hashed_password="x",
             full_name="Subash Koirala", role="user")
MANAGER = User(id="u3", email="clin@uvic.ca", hashed_password="x",
               full_name="Dr. Cheng Lin", role="professor")

LOAN = {"id": "TX-1", "itemId": "LL-X", "type": "checkout", "user": "Jiming Liu",
        "email": "jiming@uvic.ca", "qty": 1, "actualReturn": None, "updatedAt": None}
# res/plaxis carry the owner KEY (email — the same field inv_tx keys on);
# `user` stays the display name and is never an ownership input flag-on.
RES = {"id": "RS-1", "itemId": "LL-X", "user": "Jiming Liu", "status": "Pending",
       "email": "jiming@uvic.ca",
       "start": datetime(2026, 9, 1, 9), "end": datetime(2026, 9, 2, 9),
       "qty": 1, "updatedAt": None}
PLX = {"id": "PX-1", "seat": 0, "user": "Jiming Liu", "loggedOut": False,
       "email": "jiming@uvic.ca",
       "start": datetime(2026, 9, 1, 9), "end": datetime(2026, 9, 1, 12),
       "updatedAt": None}
ITEM = {"id": "LL-X", "name": "Interrogator", "kind": "equipment", "qty": 2,
        "qtyOut": 1, "updatedAt": None}


class _Result:
    deleted_count = 1


class _Cursor:
    """Just enough of a Motor cursor for list_resource (.limit) and the
    plain async-for walks."""

    def __init__(self, docs):
        self.docs = list(docs)

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def sort(self, *a, **k):
        return self

    def __aiter__(self):
        async def gen():
            for d in self.docs:
                yield dict(d)
        return gen()


class FakeColl:
    """Async collection double; records every write so the ownership tests can
    assert a rejected call mutated NOTHING (the counter-integrity check).
    ``docs`` feeds find() (empty by default, matching the old behavior)."""

    def __init__(self, doc=None, docs=None):
        self.doc = doc
        self.docs = docs or []
        self.inserted = []
        self.updated = []
        self.deleted = []
        self.queries = []

    async def find_one(self, query=None, *a, **k):
        self.queries.append(query)
        return dict(self.doc) if isinstance(self.doc, dict) else self.doc

    def find(self, query=None, *a, **k):
        return _Cursor(self.docs)

    async def insert_one(self, d):
        self.inserted.append(d)

    async def update_one(self, *a, **k):
        self.updated.append((a, k))

    async def delete_one(self, *a, **k):
        self.deleted.append(a)
        return _Result()


@pytest.fixture()
def fakes(monkeypatch):
    monkeypatch.setattr(config, "INVENTORY_PERSONAL_VIEW", True)
    items = FakeColl(doc=dict(ITEM))
    tx = FakeColl(doc=dict(LOAN))
    res = FakeColl(doc=dict(RES))
    plaxis = FakeColl(doc=dict(PLX))
    users = FakeColl(doc=None)  # no roster row unless a test sets one
    audit = FakeColl()
    monkeypatch.setattr(inv, "inv_items_collection", items)
    monkeypatch.setattr(inv, "inv_tx_collection", tx)
    monkeypatch.setattr(inv, "inv_res_collection", res)
    monkeypatch.setattr(inv, "inv_plaxis_collection", plaxis)
    monkeypatch.setattr(inv, "inv_users_collection", users)
    monkeypatch.setattr(inv, "inv_audit_collection", audit)
    monkeypatch.setattr(inv, "_RESOURCES", {
        "items": (items, inv._ITEM_FIELDS),
        "tx": (tx, inv._TX_FIELDS),
        "res": (res, inv._RES_FIELDS),
        "plaxis": (plaxis, inv._PLAXIS_FIELDS),
        "users": (users, inv._USER_FIELDS),
    })
    return {"items": items, "tx": tx, "res": res, "plaxis": plaxis,
            "users": users, "audit": audit}


def _return_payload():
    return {"type": "return", "itemId": "LL-X", "qty": 1,
            "user": "Jiming Liu", "email": "jiming@uvic.ca", "closesTxId": "TX-1"}


def _audit_rows(fakes, action):
    return [d for d in fakes["audit"].inserted if d["action"] == action]


# --- returns (POST /tx type=return) ------------------------------------------
async def test_owner_returns_own_loan(fakes):
    out = await inv.create_resource("tx", _return_payload(), current_user=OWNER)
    assert out["type"] == "return"
    # The named checkout was closed and the stock side effect applied.
    assert fakes["tx"].updated and fakes["tx"].updated[0][0][0] == {"id": "TX-1", "type": "checkout"}
    assert fakes["items"].updated[0][0][1]["$inc"] == {"qtyOut": -1}
    row = _audit_rows(fakes, "create_tx")[0]
    assert row["owner"] == "Jiming Liu"


async def test_non_owner_return_403_and_row_unchanged(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("tx", _return_payload(), current_user=OTHER)
    assert ei.value.status_code == 403
    assert ei.value.detail == "This record belongs to another user."
    # Load-then-compare-then-mutate: NOTHING moved. This is the qtyOut
    # counter-integrity check — the bug that started this work.
    assert fakes["tx"].updated == []
    assert fakes["tx"].inserted == []
    assert fakes["items"].updated == []
    denied = _audit_rows(fakes, "denied_return_tx")[0]
    assert denied["actor"] == "Subash Koirala"
    assert denied["owner"] == "Jiming Liu"
    assert denied["entity"] == "inv_tx:TX-1"


async def test_manager_returns_on_behalf_audited_with_both_names(fakes):
    out = await inv.create_resource("tx", _return_payload(), current_user=MANAGER)
    # The closed loan and the return row keep the BORROWER's identity.
    assert out["user"] == "Jiming Liu" and out["email"] == "jiming@uvic.ca"
    assert fakes["items"].updated[0][0][1]["$inc"] == {"qtyOut": -1}
    row = _audit_rows(fakes, "create_tx")[0]
    assert row["actor"] == "Dr. Cheng Lin"
    assert row["owner"] == "Jiming Liu"
    assert row["actor"] != row["owner"]


async def test_return_requires_closes_tx_id_when_flag_on(fakes):
    payload = _return_payload()
    payload.pop("closesTxId")
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("tx", payload, current_user=OWNER)
    assert ei.value.status_code == 400
    assert fakes["items"].updated == []


async def test_return_unknown_loan_is_404_not_403(fakes):
    fakes["tx"].doc = None
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("tx", _return_payload(), current_user=OWNER)
    assert ei.value.status_code == 404
    assert fakes["items"].updated == []


async def test_double_return_refused_with_409(fakes):
    fakes["tx"].doc = {**LOAN, "actualReturn": datetime(2026, 8, 20)}
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("tx", _return_payload(), current_user=OWNER)
    assert ei.value.status_code == 409
    # The second decrement (the shelf-count corruption) never happens.
    assert fakes["items"].updated == []
    assert fakes["tx"].updated == []


# --- reservations -------------------------------------------------------------
async def test_owner_edits_own_reservation(fakes):
    out = await inv.update_resource(
        "res", "RS-1", {"start": "2026-09-03T09:00:00", "end": "2026-09-04T09:00:00"},
        current_user=OWNER)
    assert out["id"] == "RS-1"
    assert fakes["res"].updated


async def test_non_owner_reservation_edit_403_row_unchanged(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("res", "RS-1", {"end": "2026-09-05T09:00:00"},
                                  current_user=OTHER)
    assert ei.value.status_code == 403
    assert ei.value.detail == "This record belongs to another user."
    assert fakes["res"].updated == []
    assert _audit_rows(fakes, "denied_update_res")[0]["owner"] == "Jiming Liu"


async def test_manager_editing_anothers_reservation_is_403(fakes):
    # Phase 2 bypass covers returns and PLAXIS release ONLY.
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("res", "RS-1", {"end": "2026-09-05T09:00:00"},
                                  current_user=MANAGER)
    assert ei.value.status_code == 403
    assert fakes["res"].updated == []


async def test_manager_approval_path_still_works_flag_on(fakes):
    # Status-only change = the approval queue, not an edit — no ownership gate.
    out = await inv.update_resource("res", "RS-1", {"status": "Approved"},
                                    current_user=MANAGER)
    assert out["id"] == "RS-1"
    assert fakes["res"].updated


async def test_owner_cancels_own_reservation(fakes):
    out = await inv.delete_resource("res", "RS-1", current_user=OWNER)
    assert out == {"deleted": "RS-1"}
    assert fakes["res"].deleted


async def test_non_owner_cancel_403_row_kept(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.delete_resource("res", "RS-1", current_user=OTHER)
    assert ei.value.status_code == 403
    assert fakes["res"].deleted == []


async def test_manager_cancel_of_anothers_reservation_is_403(fakes):
    # Managers clear someone else's booking by DENYING it, never by cancel.
    with pytest.raises(HTTPException) as ei:
        await inv.delete_resource("res", "RS-1", current_user=MANAGER)
    assert ei.value.status_code == 403
    assert fakes["res"].deleted == []


async def test_res_ownership_ignores_the_display_name(fakes):
    # A name is not an ownership check: the row NAMES the caller but its KEY
    # is someone else's → 403 no matter what the roster says.
    fakes["res"].doc = {**RES, "user": "Jiming Liu", "email": "impostor@uvic.ca"}
    fakes["users"].doc = {"name": "Jiming Liu"}
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("res", "RS-1", {"purpose": "flume"},
                                  current_user=OWNER)
    assert ei.value.status_code == 403
    assert ei.value.detail == "This record belongs to another user."
    assert fakes["res"].updated == []


async def test_same_display_name_distinct_keys(fakes):
    # Two people named "Jiming Liu": each acts only on the row carrying
    # THEIR key — the collision that name-matching used to wave through.
    twin = User(id="u4", email="jiming2@uvic.ca", hashed_password="x",
                full_name="Jiming Liu", role="user")
    fakes["res"].doc = {**RES, "email": "jiming2@uvic.ca"}  # the twin's row
    out = await inv.update_resource("res", "RS-1", {"purpose": "flume"},
                                    current_user=twin)
    assert out["id"] == "RS-1"
    fakes["res"].updated.clear()
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("res", "RS-1", {"purpose": "flume"},
                                  current_user=OWNER)
    assert ei.value.status_code == 403
    assert fakes["res"].updated == []


async def test_keyless_row_is_403_denied_no_owner_key(fakes):
    # No key is not a fallback case — its own message and audit action, so
    # legacy rows surface in the log instead of behaving like someone else's.
    fakes["res"].doc = {**RES, "email": None}
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("res", "RS-1", {"purpose": "flume"},
                                  current_user=OWNER)
    assert ei.value.status_code == 403
    assert ei.value.detail == ("This record is missing an owner reference. "
                               "Ask a lab manager to fix it.")
    assert fakes["res"].updated == []
    denied = _audit_rows(fakes, "denied_no_owner_key")[0]
    assert denied["entity"] == "inv_res:RS-1"
    assert denied["owner"] == "Jiming Liu"


async def test_manager_release_bypass_still_works_on_keyless_seat(fakes):
    # The release bypass never consulted the key, and a manager must be able
    # to clear a legacy stale seat — "ask a lab manager to fix it" has to
    # lead somewhere.
    fakes["plaxis"].doc = {**PLX, "email": None}
    out = await inv.update_resource("plaxis", "PX-1", {"loggedOut": True},
                                    current_user=MANAGER)
    assert out["id"] == "PX-1"
    assert _audit_rows(fakes, "update_plaxis")[0]["owner"] == "Jiming Liu"


# --- PLAXIS -------------------------------------------------------------------
async def test_owner_releases_own_seat(fakes):
    out = await inv.update_resource("plaxis", "PX-1", {"loggedOut": True},
                                    current_user=OWNER)
    assert out["id"] == "PX-1"
    assert fakes["plaxis"].updated


async def test_non_owner_release_403_row_unchanged(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("plaxis", "PX-1", {"loggedOut": True},
                                  current_user=OTHER)
    assert ei.value.status_code == 403
    assert fakes["plaxis"].updated == []


async def test_manager_releases_on_behalf_with_owner_audited(fakes):
    out = await inv.update_resource("plaxis", "PX-1", {"loggedOut": True},
                                    current_user=MANAGER)
    assert out["id"] == "PX-1"
    row = _audit_rows(fakes, "update_plaxis")[0]
    assert row["actor"] == "Dr. Cheng Lin" and row["owner"] == "Jiming Liu"


async def test_manager_non_release_edit_of_anothers_seat_is_403(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("plaxis", "PX-1", {"purpose": "settlement model"},
                                  current_user=MANAGER)
    assert ei.value.status_code == 403
    assert fakes["plaxis"].updated == []


async def test_plaxis_delete_owner_or_manager_only(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.delete_resource("plaxis", "PX-1", current_user=OTHER)
    assert ei.value.status_code == 403
    assert fakes["plaxis"].deleted == []
    # Deleting the row frees the seat, so it keeps the release bypass.
    out = await inv.delete_resource("plaxis", "PX-1", current_user=MANAGER)
    assert out == {"deleted": "PX-1"}
    assert _audit_rows(fakes, "delete_plaxis")[0]["owner"] == "Jiming Liu"


# --- tx rows: edits owner-only, the ledger undeletable ------------------------
async def test_tx_put_is_owner_only(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("tx", "TX-1", {"actualReturn": "2026-08-25T09:00:00"},
                                  current_user=OTHER)
    assert ei.value.status_code == 403
    assert fakes["tx"].updated == []


@pytest.mark.parametrize("caller", [OWNER, OTHER, MANAGER])
async def test_tx_delete_is_405_for_everyone_flag_on(fakes, caller):
    # Ledger immutability: even the OWNER deleting their own open checkout
    # would leave qtyOut overstated forever — the same corruption class as
    # the return bug. 405 for everyone, managers included.
    with pytest.raises(HTTPException) as ei:
        await inv.delete_resource("tx", "TX-1", current_user=caller)
    assert ei.value.status_code == 405
    assert ei.value.detail == ("Ledger rows cannot be deleted. Close the loan "
                               "with a return, or correct the count with a "
                               "stock adjustment.")
    assert fakes["tx"].deleted == []
    assert fakes["items"].updated == []  # qtyOut untouched


# --- Phase 2 (session 3): creator vs owner keys -------------------------------
ROSTER_SUBASH = {"id": "U-2", "name": "Subash Koirala", "email": "subash@uvic.ca"}


async def test_checkout_on_behalf_stamps_roster_owner_and_creator(fakes):
    # Jiming checks out FOR Subash: owner = Subash's ROSTER email (never the
    # client-typed string), createdByEmail = the caller.
    fakes["items"].doc = {"id": "LL-X", "kind": "equipment", "qty": 2, "qtyOut": 0}
    fakes["users"].docs = [dict(ROSTER_SUBASH)]
    out = await inv.create_resource(
        "tx", {"type": "checkout", "itemId": "LL-X", "qty": 1,
               "user": "Subash Koirala", "email": "typo@wrong.example"},
        current_user=OWNER)
    assert out["email"] == "subash@uvic.ca"          # roster, not the typo
    assert out["createdByEmail"] == "jiming@uvic.ca"
    stored = fakes["tx"].inserted[0]
    assert stored["email"] == "subash@uvic.ca"
    assert stored["createdByEmail"] == "jiming@uvic.ca"


async def test_on_behalf_loan_returnable_by_owner_not_creator(fakes):
    loan = {"id": "TX-OB", "itemId": "LL-X", "type": "checkout", "user": "Subash Koirala",
            "email": "subash@uvic.ca", "createdByEmail": "jiming@uvic.ca",
            "qty": 1, "actualReturn": None, "updatedAt": None}
    fakes["tx"].doc = dict(loan)
    fakes["items"].doc = {"id": "LL-X", "kind": "equipment", "qty": 2, "qtyOut": 1}
    payload = {"type": "return", "itemId": "LL-X", "qty": 1,
               "user": "Subash Koirala", "closesTxId": "TX-OB"}
    # The CREATOR cannot return it...
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("tx", dict(payload), current_user=OWNER)
    assert ei.value.status_code == 403
    assert fakes["items"].updated == []
    # ...the named OWNER can, and the return row mirrors the loan's owner.
    out = await inv.create_resource("tx", dict(payload), current_user=OTHER)
    assert out["email"] == "subash@uvic.ca"
    assert fakes["items"].updated  # qtyOut -1 applied


@pytest.mark.parametrize("name, users_docs, message", [
    ("Nobody Known", [],
     "No roster member named 'Nobody Known'. Add them under People first."),
    ("Alex Chen", [{"id": "U-3", "name": "Alex Chen", "email": "a@uvic.ca"},
                   {"id": "U-4", "name": "Alex Chen", "email": "b@uvic.ca"}],
     "More than one roster member is named 'Alex Chen'. "
     "Ask a lab manager to fix the duplicate."),
    ("Shane Smith", [{"id": "U-5", "name": "Shane Smith", "email": ""}],
     "Shane Smith has no email on file. Add one under People "
     "before checking out on their behalf."),
])
async def test_unkeyable_on_behalf_name_is_400_naming_the_fix(fakes, name, users_docs, message):
    # Each rejection states the problem, the person, and the remedy — this
    # 400 lands on whoever is at the cupboard, so it must read as
    # instructions, not as broken software.
    fakes["items"].doc = {"id": "LL-X", "kind": "equipment", "qty": 2, "qtyOut": 0}
    fakes["users"].docs = users_docs
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource(
            "tx", {"type": "checkout", "itemId": "LL-X", "qty": 1, "user": name},
            current_user=OWNER)
    assert ei.value.status_code == 400
    assert ei.value.detail == message
    assert fakes["tx"].inserted == []
    assert fakes["items"].updated == []


async def test_blank_email_member_gets_the_blank_email_message_not_not_found(fakes):
    # The roster row EXISTS — the message must say "no email on file", never
    # "no roster member" (which would send the fixer hunting for a missing
    # person instead of a missing field).
    fakes["items"].doc = {"id": "LL-X", "kind": "equipment", "qty": 2, "qtyOut": 0}
    fakes["users"].docs = [{"id": "U-5", "name": "Shane Smith", "email": ""}]
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource(
            "tx", {"type": "checkout", "itemId": "LL-X", "qty": 1, "user": "Shane Smith"},
            current_user=OWNER)
    assert "has no email on file" in ei.value.detail
    assert "No roster member" not in ei.value.detail


# --- Phase 4 (session 4): server-side People validation ------------------------
async def test_user_create_blank_name_is_400_flag_on(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("users", {"name": "   ", "email": "x@uvic.ca"},
                                  current_user=OWNER)
    assert ei.value.status_code == 400
    assert ei.value.detail == "A lab member needs a name."
    assert fakes["users"].inserted == []


async def test_user_update_cannot_blank_the_name(fakes):
    fakes["users"].doc = {"id": "U-9", "name": "Jiming Liu", "updatedAt": None}
    with pytest.raises(HTTPException) as ei:
        await inv.update_resource("users", "U-9", {"name": ""}, current_user=OWNER)
    assert ei.value.status_code == 400
    assert fakes["users"].updated == []
    # An edit NOT touching the name still works on a legacy nameless row.
    fakes["users"].doc = {"id": "U-9", "name": "", "updatedAt": None}
    out = await inv.update_resource("users", "U-9", {"group": "Lin Lab"},
                                    current_user=OWNER)
    assert out["id"] == "U-9"


async def test_user_student_id_format_enforced_but_blank_email_storable(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("users", {"name": "New Member", "studentId": "12345"},
                                  current_user=OWNER)
    assert ei.value.status_code == 400
    assert ei.value.detail == "The student ID should look like V00891234."
    # Blank email and blank studentId stay STORABLE (two live rows are
    # legitimately email-less; only on-behalf naming refuses them).
    out = await inv.create_resource("users", {"name": "New Member", "email": ""},
                                    current_user=OWNER)
    assert out["name"] == "New Member"


async def test_res_on_behalf_owner_is_the_named_person(fakes):
    fakes["items"].doc = {"id": "LL-X", "kind": "equipment", "qty": 1}
    fakes["users"].docs = [dict(ROSTER_SUBASH)]
    out = await inv.create_resource(
        "res", {"itemId": "LL-X", "user": "Subash Koirala",
                "start": "2026-09-10T09:00:00", "end": "2026-09-11T09:00:00"},
        current_user=OWNER)
    assert out["email"] == "subash@uvic.ca"
    assert out["createdByEmail"] == "jiming@uvic.ca"


async def test_tx_put_cannot_rewrite_the_owner_key(fakes):
    out = await inv.update_resource("tx", "TX-1", {"email": "stolen@evil.example",
                                                   "purpose": "x"},
                                    current_user=OWNER)
    assert out["id"] == "TX-1"
    (_args, _kw) = fakes["tx"].updated[-1]
    assert "email" not in _args[1]["$set"]
    assert _args[1]["$set"]["purpose"] == "x"


# --- Phase 4 (session 3): stock gates -----------------------------------------
async def test_over_checkout_is_409_and_nothing_moves(fakes):
    fakes["items"].doc = {"id": "LL-X", "name": "Interrogator", "kind": "equipment",
                          "qty": 2, "qtyOut": 2}
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource(
            "tx", {"type": "checkout", "itemId": "LL-X", "qty": 1, "user": "Jiming Liu"},
            current_user=OWNER)
    assert ei.value.status_code == 409
    assert "Only 0 of Interrogator available" in ei.value.detail
    assert fakes["items"].updated == []
    assert fakes["tx"].inserted == []


async def test_adjust_below_zero_is_409(fakes):
    fakes["items"].doc = {"id": "LL-X", "name": "Epoxy", "kind": "consumable", "qty": 2}
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource(
            "tx", {"type": "adjust", "itemId": "LL-X", "qty": -3, "user": "Jiming Liu"},
            current_user=OWNER)
    assert ei.value.status_code == 409
    assert "below zero" in ei.value.detail
    assert fakes["items"].updated == []


async def test_return_qty_exceeding_the_loan_is_400(fakes):
    with pytest.raises(HTTPException) as ei:
        await inv.create_resource("tx", {**_return_payload(), "qty": 5},
                                  current_user=OWNER)
    assert ei.value.status_code == 400
    assert "exceeds the open loan" in ei.value.detail
    assert fakes["items"].updated == []


# --- flag OFF: byte-identical behavior ----------------------------------------
class TestFlagOff:
    @pytest.fixture(autouse=True)
    def _off(self, fakes, monkeypatch):
        monkeypatch.setattr(config, "INVENTORY_PERSONAL_VIEW", False)
        self.fakes = fakes

    async def test_non_owner_return_still_succeeds(self):
        out = await inv.create_resource("tx", _return_payload(), current_user=OTHER)
        assert out["type"] == "return"
        assert self.fakes["items"].updated  # side effect applied, as today

    async def test_non_owner_res_edit_and_cancel_still_open(self):
        out = await inv.update_resource("res", "RS-1", {"purpose": "flume"},
                                        current_user=OTHER)
        assert out["id"] == "RS-1"
        out = await inv.delete_resource("res", "RS-1", current_user=OTHER)
        assert out == {"deleted": "RS-1"}

    async def test_non_owner_plaxis_release_still_open(self):
        out = await inv.update_resource("plaxis", "PX-1", {"loggedOut": True},
                                        current_user=OTHER)
        assert out["id"] == "PX-1"

    async def test_tx_delete_still_open(self):
        out = await inv.delete_resource("tx", "TX-1", current_user=OTHER)
        assert out == {"deleted": "TX-1"}

    async def test_keyless_res_edit_still_open(self):
        # Flag off, a keyless legacy row behaves exactly as before this
        # session — no denied_no_owner_key, no 403.
        self.fakes["res"].doc = {**RES, "email": None}
        out = await inv.update_resource("res", "RS-1", {"purpose": "flume"},
                                        current_user=OTHER)
        assert out["id"] == "RS-1"

    async def test_res_create_response_hides_the_stored_key(self):
        # The key is ALWAYS stamped (data must not drift while the flag is
        # off) but never echoed flag-off — parity.
        self.fakes["items"].doc = {"id": "LL-X", "kind": "equipment", "qty": 1}
        out = await inv.create_resource(
            "res", {"itemId": "LL-X", "user": "Subash Koirala",
                    "start": "2026-09-10T09:00:00", "end": "2026-09-11T09:00:00"},
            current_user=OTHER)
        assert "email" not in out
        assert self.fakes["res"].inserted[0]["email"] == "subash@uvic.ca"

    async def test_res_list_response_hides_the_stored_key(self):
        self.fakes["res"].docs = [{**RES, "createdByEmail": "jiming@uvic.ca"}]
        listed = await inv.list_resource("res", itemId=None, open_only=False,
                                         limit=1000, current_user=OTHER)
        assert listed["total"] == 1
        assert all("email" not in d and "createdByEmail" not in d for d in listed["items"])

    async def test_tx_create_keeps_client_email_and_hides_creator_field(self):
        # Flag-off inv_tx writes behave EXACTLY as today: the form's email is
        # stored verbatim, no roster resolution, no 400s — only the additive
        # createdByEmail is stamped (and projected out of the response).
        self.fakes["items"].doc = {"id": "LL-X", "kind": "equipment", "qty": 2, "qtyOut": 0}
        out = await inv.create_resource(
            "tx", {"type": "checkout", "itemId": "LL-X", "qty": 1,
                   "user": "A Visitor", "email": "typed@example.org"},
            current_user=OTHER)
        assert out["email"] == "typed@example.org"
        assert "createdByEmail" not in out
        assert self.fakes["tx"].inserted[0]["createdByEmail"] == "subash@uvic.ca"

    async def test_over_checkout_and_negative_adjust_still_pass_flag_off(self):
        self.fakes["items"].doc = {"id": "LL-X", "kind": "equipment", "qty": 1, "qtyOut": 1}
        out = await inv.create_resource(
            "tx", {"type": "checkout", "itemId": "LL-X", "qty": 1, "user": "X"},
            current_user=OTHER)
        assert out["type"] == "checkout"  # no stock gate flag-off, as today


# --- flag ON: the key IS stamped and IS visible -------------------------------
async def test_res_create_stamps_and_echoes_the_key_flag_on(fakes):
    fakes["items"].doc = {"id": "LL-X", "kind": "equipment", "qty": 1}
    out = await inv.create_resource(
        "res", {"itemId": "LL-X", "user": "Subash Koirala",
                # a client-supplied key must be ignored (not whitelisted)
                "email": "spoofed@evil.example",
                "start": "2026-09-10T09:00:00", "end": "2026-09-11T09:00:00"},
        current_user=OTHER)
    assert out["email"] == "subash@uvic.ca"
    assert fakes["res"].inserted[0]["email"] == "subash@uvic.ca"


async def test_res_list_includes_the_key_flag_on(fakes):
    fakes["res"].docs = [dict(RES)]
    listed = await inv.list_resource("res", itemId=None, open_only=False,
                                     limit=1000, current_user=OWNER)
    assert listed["items"][0]["email"] == "jiming@uvic.ca"

    async def test_audit_rows_carry_no_owner_key(self):
        await inv.update_resource("plaxis", "PX-1", {"loggedOut": True},
                                  current_user=MANAGER)
        assert all("owner" not in d for d in self.fakes["audit"].inserted)


# --- route registration -------------------------------------------------------
def test_me_route_registered_only_with_both_flags(monkeypatch, fakes, me_data):
    from fastapi.testclient import TestClient

    from app.dependencies.auth import get_current_user

    monkeypatch.setattr(config, "INVENTORY_ENABLED", True)
    monkeypatch.setattr(config, "INVENTORY_PERSONAL_VIEW", False)
    app = FastAPI()
    inv.register(app)
    assert "/api/inventory/me" not in app.openapi()["paths"]

    monkeypatch.setattr(config, "INVENTORY_PERSONAL_VIEW", True)
    app2 = FastAPI()
    inv.register(app2)
    assert "/api/inventory/me" in app2.openapi()["paths"]
    # And it must DISPATCH to my_bench, not be swallowed by the /{resource}
    # catch-all as an unknown resource name (which would 404).
    app2.dependency_overrides[get_current_user] = lambda: OWNER
    r = TestClient(app2).get("/api/inventory/me")
    assert r.status_code == 200
    assert [t["id"] for t in r.json()["loans"]] == ["TX-1"]


# --- GET /api/inventory/me ----------------------------------------------------
@pytest.fixture()
def me_data(monkeypatch, fakes):
    from app.services import inventory_service

    now = datetime.now()
    data = {
        "items": [dict(ITEM)],
        "open_loans": [
            {"id": "TX-1", "itemId": "LL-X", "type": "checkout", "user": "Jiming Liu",
             "email": "jiming@uvic.ca", "qty": 1,
             "expectedReturn": now - timedelta(days=3), "actualReturn": None},
            {"id": "TX-2", "itemId": "LL-X", "type": "checkout", "user": "Subash Koirala",
             "email": "subash@uvic.ca", "qty": 1,
             "expectedReturn": now + timedelta(days=3), "actualReturn": None},
        ],
        "reservations": [
            {"id": "RS-1", "itemId": "LL-X", "user": "Jiming Liu", "status": "Pending",
             "email": "jiming@uvic.ca",
             "start": now + timedelta(days=2), "end": now + timedelta(days=3)},
            {"id": "RS-OLD", "itemId": "LL-X", "user": "Jiming Liu", "status": "Approved",
             "email": "jiming@uvic.ca",
             "start": now - timedelta(days=9), "end": now - timedelta(days=8)},
            {"id": "RS-2", "itemId": "LL-X", "user": "Subash Koirala", "status": "Approved",
             "email": "subash@uvic.ca",
             "start": now + timedelta(days=5), "end": now + timedelta(days=6)},
            # KEYLESS decoy naming the caller: reservation ownership is the
            # key ONLY, so this row must never surface in the caller's /me.
            {"id": "RS-NOKEY", "itemId": "LL-X", "user": "Jiming Liu", "status": "Pending",
             "start": now + timedelta(days=4), "end": now + timedelta(days=5)},
        ],
        "plaxis": [
            {"id": "PX-1", "seat": 0, "user": "Jiming Liu", "loggedOut": False,
             "email": "jiming@uvic.ca",
             "start": now - timedelta(hours=4), "end": now - timedelta(hours=1)},
            {"id": "PX-2", "seat": 1, "user": "Subash Koirala", "loggedOut": False,
             "email": "subash@uvic.ca",
             "start": now, "end": now + timedelta(hours=2)},
        ],
    }

    async def fake_fetch():
        return data

    monkeypatch.setattr(inventory_service, "_fetch_inventory_data", fake_fetch)
    return data


async def test_me_returns_only_the_callers_rows(me_data):
    out = await inv.my_bench(current_user=OWNER)
    assert [t["id"] for t in out["loans"]] == ["TX-1"]
    assert out["loans"][0]["overdueDays"] == 3  # server clock, not the browser's
    # end >= now, any status — the ended reservation is gone, the pending kept.
    assert [r["id"] for r in out["reservations"]] == ["RS-1"]
    assert [p["id"] for p in out["plaxis"]] == ["PX-1"]
    # Alerts: the unforked alerts_for output filtered by refId — the overdue
    # loan and the overrun seat are Jiming's; the pending-approval alert for
    # RS-1 is his too. Nothing about Subash's rows leaks in.
    kinds = sorted(a["kind"] for a in out["alerts"])
    assert kinds == ["overdue", "pending_approval", "plaxis_overrun"]
    assert all(a["refId"] in {"TX-1", "RS-1", "PX-1"} for a in out["alerts"])


async def test_me_is_empty_lists_not_404_for_a_user_with_nothing(me_data):
    ghost = User(id="u9", email="ghost@uvic.ca", hashed_password="x",
                 full_name="Nobody Here", role="user")
    out = await inv.my_bench(current_user=ghost)
    assert out == {"loans": [], "reservations": [], "plaxis": [], "alerts": []}
