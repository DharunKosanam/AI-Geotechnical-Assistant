"""Lab inventory CRUD (INVENTORY_ENABLED).

/api/inventory/* — collection-per-resource CRUD over the inv_* collections,
mirroring the existing auth pattern (JWT resolved by get_current_user from the
httpOnly cookie or Bearer header). Registered ONLY when INVENTORY_ENABLED is
on (highlights pattern — off means absent, route table unchanged).

Rules:
  * Names and emails are stored AS-IS. No transformation — these are working
    custodial records ("who has the interrogator?" needs real identity).
  * EVERY mutation writes a record to inv_audit (id, ts, actor, action,
    entity, detail) — best-effort, never blocking the operation it describes,
    same posture as the KB audit.
  * Transactions carry their stock side effects server-side (checkout/return
    move qtyOut for equipment and qty for consumables; adjust moves qty;
    damage marks the item) so the snapshot and the feasibility walk always
    see consistent numbers.
  * Updates accept an ``expectedUpdatedAt`` precondition: when supplied and
    stale, the write is refused with 409 so concurrent edits surface as a
    conflict instead of silently clobbering (the frontend shows a toast).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from app.core import config
from app.core.database import (
    files_collection,
    inv_audit_collection,
    inv_items_collection,
    inv_plaxis_collection,
    inv_res_collection,
    inv_tx_collection,
    inv_users_collection,
)
from app.dependencies.auth import get_current_user
from app.services.inventory_service import (
    PLAXIS_SEATS,
    ItemRequest,
    build_inventory_snapshot_result,
    check_feasibility,
    conflict_message,
    render_feasibility_report,
    reservation_conflicts,
    seat_conflicts,
    derive_reserved,
    strip_stored_reserved,
)
from models import User

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# Resource name -> (collection, allowed fields). ``id`` is the domain key
# (unique-indexed on inv_items); Mongo _id never leaves the API.
_ITEM_FIELDS = (
    "id", "name", "category", "subCategory", "kind", "manufacturer", "model",
    "serial", "qty", "qtyOut", "unit", "location", "custodian", "condition",
    "status", "minStock", "purchaseDate", "expiryDate", "maintDays",
    "lastMaint", "supplier", "notes", "description",
)


def _derive_item_fields(doc: Dict[str, Any]) -> None:
    """Server-derived item fields, recomputed on every write.

    nextMaint = lastMaint + maintDays (stored so it is queryable); None when
    either input is missing. Never trusted from the client — the whitelist
    above deliberately omits it, so a client value cannot even reach here.
    Seeded rows written before this existed simply lack the key; readers
    render that as "—"."""
    last = doc.get("lastMaint")
    days = doc.get("maintDays")
    if isinstance(last, datetime) and isinstance(days, int) and days > 0:
        doc["nextMaint"] = last + timedelta(days=days)
    else:
        doc["nextMaint"] = None


async def _items_view(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Items as READ: ``Reserved`` derived from the live inv_res rows (never
    the stored value — see derive_reserved). Every item read goes through
    here so the table, the drawer and the chat snapshot agree."""
    if not docs:
        return docs
    ids = [d.get("id") for d in docs]
    reservations = [
        r async for r in inv_res_collection.find({"itemId": {"$in": ids}}, {"_id": 0})
    ]
    return derive_reserved(docs, reservations, datetime.now())
_TX_FIELDS = (
    "id", "itemId", "type", "user", "email", "group", "qty", "ts",
    "expectedReturn", "actualReturn", "condBefore", "condAfter", "purpose",
    "approval", "studentId", "photoId",
)
_RES_FIELDS = ("id", "itemId", "user", "group", "start", "end", "purpose", "status", "notes", "qty")
_PLAXIS_FIELDS = ("id", "seat", "user", "group", "purpose", "start", "end", "loggedOut")
_USER_FIELDS = ("id", "name", "email", "studentId", "role", "program", "group", "cosup", "since")

_RESOURCES: Dict[str, Any] = {
    "items": (inv_items_collection, _ITEM_FIELDS),
    "tx": (inv_tx_collection, _TX_FIELDS),
    "res": (inv_res_collection, _RES_FIELDS),
    "plaxis": (inv_plaxis_collection, _PLAXIS_FIELDS),
    "users": (inv_users_collection, _USER_FIELDS),
}

# users.since is deliberately NOT a date field: the lab roster records it as
# a bare year label ("2024"), which fromisoformat would reject.
_DATE_FIELDS = frozenset({
    "purchaseDate", "expiryDate", "lastMaint", "ts", "expectedReturn",
    "actualReturn", "start", "end",
})
_INT_FIELDS = frozenset({"qty", "qtyOut", "minStock", "maintDays", "seat"})
_TX_TYPES = frozenset({"checkout", "return", "adjust", "damage"})


def _resource(name: str):
    entry = _RESOURCES.get(name)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return entry


def _coerce(payload: Dict[str, Any], allowed) -> Dict[str, Any]:
    """Whitelist + light coercion (ISO strings -> datetime, numerics -> int).
    Unknown keys are dropped, not errors, so the bench can send its whole
    object shape unchanged."""
    doc: Dict[str, Any] = {}
    for key in allowed:
        if key not in payload:
            continue
        value = payload[key]
        if key in _DATE_FIELDS and isinstance(value, str) and value.strip():
            try:
                value = datetime.fromisoformat(value.strip().replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Invalid date for '{key}': {value!r}")
        if key in _INT_FIELDS and value is not None and not isinstance(value, bool):
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Invalid number for '{key}': {value!r}")
        doc[key] = value
    return doc


def _actor(user: User) -> str:
    return getattr(user, "full_name", None) or getattr(user, "email", "unknown")


# Open-access policy: every AUTHENTICATED user may transact (checkout, return,
# reserve, damage, adjust, edit items, add/edit users). Exactly two actions
# stay manager-gated, enforced HERE (the client only hides controls):
#   1. deleting items/users — the one action the audit log can't reverse (a
#      deleted item just disappears and the assistant then denies the lab
#      owns it);
#   2. reservation approval — open approval makes Pending meaningless (users
#      would approve their own bookings).
# Mirrors app/routers/kb.py ADMIN_ROLES.
ADMIN_ROLES = frozenset({"admin", "professor"})


def _is_manager(user: User) -> bool:
    return getattr(user, "role", "user") in ADMIN_ROLES


def _require_manager(user: User, action: str) -> None:
    if not _is_manager(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Only a lab manager can {action}.")


async def _roster_lookup(email: str = "", name: str = "") -> Dict[str, Optional[str]]:
    """studentId + group joined from the inv_users roster at write time
    (case-insensitive exact match; email preferred, else name — inv_res and
    inv_plaxis carry no email column, so their join key is the named person).
    One source, not two: a client-supplied value is never trusted. No roster
    row (or no key at all) resolves to None values and the write continues.
    """
    out: Dict[str, Optional[str]] = {"studentId": None, "group": None}
    e, n = (email or "").strip(), (name or "").strip()
    if e:
        query: Dict[str, Any] = {"email": {"$regex": f"^{re.escape(e)}$", "$options": "i"}}
    elif n:
        query = {"name": {"$regex": f"^{re.escape(n)}$", "$options": "i"}}
    else:
        return out
    row = await inv_users_collection.find_one(query, {"studentId": 1, "group": 1})
    if row:
        out["studentId"] = str(row.get("studentId") or "").strip() or None
        out["group"] = str(row.get("group") or "").strip() or None
    return out


async def _reject_if_conflicting(res_doc: Dict[str, Any], exclude_id: Any = None) -> None:
    """Phase 1 overlap gate, SERVER-side. Loads the item plus that item's open
    loans and non-denied reservations and refuses (409, naming the holders
    and windows) any reservation whose window would over-commit the item.
    Denied reservations never conflict; a reservation being edited/approved
    is excluded from its own check."""
    if str(res_doc.get("status") or "Pending").lower() == "denied":
        return
    start, end = res_doc.get("start"), res_doc.get("end")
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="A reservation needs a start and an end.")
    if end <= start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The end must be after the start.")
    item = await inv_items_collection.find_one({"id": res_doc.get("itemId")}, {"_id": 0})
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="That item is no longer in the inventory.")
    if _is_archived(item):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"{item.get('name') or item['id']} is archived and cannot be reserved.")
    loans = [d async for d in inv_tx_collection.find(
        {"type": "checkout", "actualReturn": None, "itemId": item["id"]}, {"_id": 0})]
    others = [d async for d in inv_res_collection.find({"itemId": item["id"]}, {"_id": 0})]
    holders = reservation_conflicts(
        item, start, end, int(res_doc.get("qty") or 1), loans, others, exclude_id=exclude_id,
    )
    if holders:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=conflict_message(item, holders))


async def _reject_if_seat_conflict(doc: Dict[str, Any], exclude_id: Any = None) -> None:
    """PLAXIS seat gate, SERVER-side (INVENTORY_PERSONAL_VIEW — flag-off the
    create stays unconditional for parity). Validates the booking (seat in
    the known set, both dates present, start < end) with 400, then loads the
    seat's held sessions and refuses (409, naming the holder and window via
    conflict_message — the reservation gate's exact shape) any window that
    overlaps one. Half-open, so back-to-back bookings pass. The row being
    edited is excluded from its own check. NOT owner-aware: a seat is
    physical state, every held session counts."""
    if not config.INVENTORY_PERSONAL_VIEW:
        return
    seat = doc.get("seat")
    if seat not in PLAXIS_SEATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Choose Seat 1 or Seat 2.")
    start, end = doc.get("start"), doc.get("end")
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="A PLAXIS session needs a start and an end.")
    if end <= start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The end must be after the start.")
    sessions = [d async for d in inv_plaxis_collection.find({"seat": seat}, {"_id": 0})]
    holders = seat_conflicts(sessions, seat, start, end, exclude_id=exclude_id)
    if holders:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=conflict_message({"name": f"PLAXIS seat {seat + 1}"}, holders))


_V_NUMBER_RE = re.compile(r"^[Vv]\d{8}$")


def _validate_roster_write(doc: Dict[str, Any], creating: bool) -> None:
    """Server-side People validation (INVENTORY_PERSONAL_VIEW — flag-off the
    endpoints accept what they always did). The modal gates these client-side
    only, so a direct call could store a nameless member or a malformed
    V-number. A blank EMAIL stays storable by design: it is surfaced as a
    People-tab note and refused only when that person is named on an
    on-behalf write — never at roster time (two live rows are legitimately
    email-less and must not become uneditable)."""
    if not config.INVENTORY_PERSONAL_VIEW:
        return
    if (creating or "name" in doc) and not str(doc.get("name") or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="A lab member needs a name.")
    student_id = str(doc.get("studentId") or "").strip()
    if "studentId" in doc and student_id and not _V_NUMBER_RE.match(student_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The student ID should look like V00891234.")


async def _roster_email_for_name(name: str) -> Optional[str]:
    """Unique roster email for an exact (case-insensitive) name, else None —
    the never-guess lookup shared by best-effort paths."""
    n = (name or "").strip()
    if not n:
        return None
    candidates = [
        u async for u in inv_users_collection.find(
            {"name": {"$regex": f"^{re.escape(n)}$", "$options": "i"}}, {"_id": 0})
    ]
    matches = [u for u in candidates
               if str(u.get("name") or "").strip().lower() == n.lower()]
    if len(matches) == 1:
        return str(matches[0].get("email") or "").strip() or None
    return None


async def _resolve_owner_email(current_user: User, form_user: str) -> Optional[str]:
    """The owner key for a NEW row (INVENTORY_PERSONAL_VIEW paths only).

    Defaults to the JWT caller. When the form names a DIFFERENT person, that
    person's ROSTER email is stamped instead — resolved by exact
    case-insensitive name, exactly one match, non-blank email — so an
    on-behalf row belongs to the person it names. A client-typed email
    string is never stored, and a name that cannot be keyed rejects the
    write with 400 naming the problem (an unkeyed row would be actionable by
    nobody)."""
    caller_email, caller_names = await _caller_identity(current_user)
    name = (form_user or "").strip()
    if not name or name.lower() in caller_names:
        return (getattr(current_user, "email", "") or "").strip() or None
    candidates = [
        u async for u in inv_users_collection.find(
            {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}, {"_id": 0})
    ]
    # Exact-match re-check in Python: the regex is the index-friendly fetch,
    # equality is the rule (and keeps the test fakes honest).
    matches = [u for u in candidates
               if str(u.get("name") or "").strip().lower() == name.lower()]
    # Each rejection states the problem, the person, and the REMEDY — this
    # 400 lands on whoever is standing at the cupboard, not on whoever can
    # fix the roster, so it must read as instructions, never as broken
    # software. No field/collection names in user-facing text.
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No roster member named '{name}'. Add them under People first.")
    if len(matches) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"More than one roster member is named '{name}'. "
                   "Ask a lab manager to fix the duplicate.")
    email = str(matches[0].get("email") or "").strip()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{name} has no email on file. Add one under People "
                   "before checking out on their behalf.")
    return email


async def _audit(actor: str, action: str, entity: str, detail: Any = None,
                 owner: Optional[str] = None) -> None:
    """Best-effort audit record (never blocks the operation it describes).

    ``owner`` (additive, INVENTORY_PERSONAL_VIEW paths only) records WHOSE
    record was changed separately from WHO acted, so an on-behalf return
    reads "Subash closed Jiming's loan" instead of implying Jiming did it,
    and a rejected attempt names both the caller and the owner. Rows written
    before the field existed simply lack the key."""
    try:
        doc: Dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "ts": datetime.now(),
            "actor": actor,
            "action": action,
            "entity": entity,
            "detail": detail,
        }
        if owner is not None:
            doc["owner"] = owner
        await inv_audit_collection.insert_one(doc)
    except Exception as e:  # noqa: BLE001
        print(f"[INV_AUDIT] failed to record {action} on {entity}: {e}")


# ---------------------------------------------------------------------------
# Ownership boundary (INVENTORY_PERSONAL_VIEW). Owner-only rows: open loans
# (inv_tx), reservations (inv_res), PLAXIS sessions (inv_plaxis). The check
# lives IN each handler (load-then-compare-then-mutate, never a middleware a
# new route could forget) and compares against the SAME resolver everything
# else uses: get_current_user's JWT User. inv_tx rows carry the borrower's
# email; inv_res/inv_plaxis carry only the person's NAME, so the caller's
# name set is the JWT full_name plus the roster name joined by email — the
# exact email-first/name-fallback rule the Dashboard's "Checked out to you"
# already applies client-side. Read-side visibility is untouched: no list
# query is ever filtered by user.
# ---------------------------------------------------------------------------
OWNERSHIP_DETAIL = "This record belongs to another user."
NO_OWNER_KEY_DETAIL = ("This record is missing an owner reference. "
                       "Ask a lab manager to fix it.")
LEDGER_DELETE_DETAIL = ("Ledger rows cannot be deleted. Close the loan with a "
                        "return, or correct the count with a stock adjustment.")


def _hide_owner_key(resource: str, doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Flag-off parity: the additive owner-key fields are always STORED but
    projected out of every API response until INVENTORY_PERSONAL_VIEW is on,
    so both parity scenarios stay byte-identical — ``email`` +
    ``createdByEmail`` on res/plaxis, ``createdByEmail`` alone on tx
    (tx.email predates the flag and has always been visible: the overdue
    report renders it). (The manager-only /backup export deliberately keeps
    whole documents — stripping there would make a backup→restore cycle lose
    ownership keys.)"""
    if doc is not None and not config.INVENTORY_PERSONAL_VIEW:
        if resource in ("res", "plaxis"):
            doc.pop("email", None)
            doc.pop("createdByEmail", None)
        elif resource == "tx":
            doc.pop("createdByEmail", None)
    return doc


async def _caller_identity(user: User) -> Tuple[str, set]:
    """(email, names) for the JWT-resolved caller, all lowercased. names =
    the JWT full_name plus the inv_users roster name joined by email (a
    reservation stores the roster spelling of a person, which can differ
    from the account's full_name)."""
    email = (getattr(user, "email", "") or "").strip().lower()
    names: set = set()
    full = (getattr(user, "full_name", "") or "").strip().lower()
    if full:
        names.add(full)
    if email:
        row = await inv_users_collection.find_one(
            {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}}, {"name": 1}
        )
        roster_name = str((row or {}).get("name") or "").strip().lower()
        if roster_name:
            names.add(roster_name)
    return email, names


def _owns_row(row: Dict[str, Any], email: str, names: set) -> bool:
    """Email match first (what the server records on transactions), name as
    the fallback — and the ONLY key for inv_res/inv_plaxis, which carry no
    email column. A row naming nobody is owned by nobody."""
    row_email = str(row.get("email") or "").strip().lower()
    if email and row_email and row_email == email:
        return True
    row_user = str(row.get("user") or "").strip().lower()
    return bool(row_user) and row_user in names


def _row_owner_label(row: Dict[str, Any]) -> str:
    return str(row.get("user") or row.get("email") or "unknown")


async def _require_owner(
    resource: str,
    row: Dict[str, Any],
    current_user: User,
    action: str,
    allow_manager: bool = False,
) -> bool:
    """403 unless the caller owns ``row`` (or is a manager where the bypass
    applies: returns and PLAXIS release ONLY — never reservation edits).
    Returns True when a manager is acting on someone else's row, so the
    caller can audit the on-behalf. The rejected attempt itself is audited
    with the caller, the target row id and the owner it belonged to.
    No-op with INVENTORY_PERSONAL_VIEW off.

    Ownership keys, per collection:
      * inv_res / inv_plaxis — the stored owner ``email`` ONLY. A display
        name is not an ownership check (duplicate names, corrected
        spellings and graduated-and-changed emails all silently flip it),
        so there is no name fallback here. A row with NO key is not a
        fallback case either: it 403s with its own message and a
        ``denied_no_owner_key`` audit row so it surfaces in the log —
        except to a manager where the bypass applies (release), who could
        otherwise never clear a legacy stale seat.
      * inv_tx — email first with name fallback, unchanged (tx rows have
        carried the email key since the collection existed)."""
    if not config.INVENTORY_PERSONAL_VIEW:
        return False
    if resource in ("res", "plaxis"):
        caller = (getattr(current_user, "email", "") or "").strip().lower()
        row_key = str(row.get("email") or "").strip().lower()
        if caller and row_key and row_key == caller:
            return False
        if allow_manager and _is_manager(current_user):
            return True
        if not row_key:
            await _audit(_actor(current_user), "denied_no_owner_key",
                         f"inv_{resource}:{row.get('id')}", {"reason": "no_owner_key"},
                         owner=_row_owner_label(row))
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=NO_OWNER_KEY_DETAIL)
        await _audit(_actor(current_user), f"denied_{action}",
                     f"inv_{resource}:{row.get('id')}", {"reason": "not_owner"},
                     owner=_row_owner_label(row))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OWNERSHIP_DETAIL)
    email, names = await _caller_identity(current_user)
    if _owns_row(row, email, names):
        return False
    if allow_manager and _is_manager(current_user):
        return True
    await _audit(_actor(current_user), f"denied_{action}",
                 f"inv_{resource}:{row.get('id')}", {"reason": "not_owner"},
                 owner=_row_owner_label(row))
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OWNERSHIP_DETAIL)


async def _apply_tx_side_effects(doc: Dict[str, Any]) -> None:
    """Keep item stock consistent with the transaction being recorded.

    checkout: equipment -> qtyOut += qty; consumable -> qty -= qty (consumed).
    return  : equipment -> qtyOut -= qty; consumable -> qty += qty;
              condAfter, when given, becomes the item's condition.
    adjust  : qty is a DELTA applied to item.qty.
    damage  : the item is marked Damaged (condAfter overrides the label).
    """
    item = await inv_items_collection.find_one({"id": doc.get("itemId")})
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="That item is no longer in the inventory.")
    if _is_archived(item) and doc.get("type") == "checkout":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"{item.get('name') or item['id']} is archived and cannot be checked out.")
    qty = int(doc.get("qty") or 1)
    tx_type = doc.get("type")
    # Stock gates (INVENTORY_PERSONAL_VIEW — flag-off the side effects apply
    # unconditionally, exactly as before): the modals validate these client-
    # side only, so a stale tab or a direct API call could over-check-out
    # (negative availability) or adjust a count below zero. 409 = the item's
    # CURRENT state refuses it; the conflict toast refetches and shows this
    # detail.
    if config.INVENTORY_PERSONAL_VIEW:
        name = item.get("name") or item["id"]
        on_hand = int(item.get("qty") or 0)
        if tx_type == "checkout":
            if qty < 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="Checkout quantity must be at least 1.")
            avail = on_hand if item.get("kind") == "consumable" \
                else on_hand - int(item.get("qtyOut") or 0)
            if qty > avail:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail=f"Only {max(avail, 0)} of {name} available.")
        elif tx_type == "adjust":
            if on_hand + int(doc.get("qty") or 0) < 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Cannot adjust {name} below zero (it currently has {on_hand}).")
    update: Dict[str, Any] = {}
    if tx_type == "checkout":
        if item.get("kind") == "consumable":
            update = {"$inc": {"qty": -qty}}
        else:
            update = {"$inc": {"qtyOut": qty}}
    elif tx_type == "return":
        if item.get("kind") == "consumable":
            update = {"$inc": {"qty": qty}}
        else:
            update = {"$inc": {"qtyOut": -qty}}
        if doc.get("condAfter"):
            update.setdefault("$set", {})["condition"] = doc["condAfter"]
    elif tx_type == "adjust":
        update = {"$inc": {"qty": int(doc.get("qty") or 0)}}
    elif tx_type == "damage":
        update = {"$set": {"condition": doc.get("condAfter") or "Damaged"}}
    if update:
        update.setdefault("$set", {})["updatedAt"] = datetime.now()
        await inv_items_collection.update_one({"id": doc.get("itemId")}, update)


# ---------------------------------------------------------------------------
# Nav probe + alerts. Fixed paths MUST be registered before the /{resource}
# catch-all below or FastAPI would route them as resource names.
# ---------------------------------------------------------------------------
@router.get("/status")
async def inventory_status():
    """Ungated nav probe (the kb/workspace status pattern): the Header shows
    the Inventory tab only when this returns enabled. The router itself is
    registered ONLY when INVENTORY_ENABLED is on, so with the flag off this
    path 404s — which the Header's probe treats as disabled. No auth, like
    its siblings: it reveals nothing but the feature flag."""
    return {"enabled": True, "photos": bool(config.INVENTORY_PHOTOS_ENABLED)}


# ---------------------------------------------------------------------------
# Damage-report photos (INVENTORY_PHOTOS_ENABLED). Storage reuses the KB /
# user-upload pipeline's backend exactly: a `files` document with the bytes
# inline (`content`), no GridFS, no disk. Served here (not by files.py) so any
# authenticated member can see a lab record, and so the flag can hide it.
# ---------------------------------------------------------------------------
_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def sniff_image_mime(head: bytes) -> Optional[str]:
    """MIME by CONTENT (magic bytes), never by extension. JPEG / PNG / WebP
    only; anything else is None."""
    for magic, mime in _IMAGE_MAGIC:
        if head.startswith(magic):
            return mime
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _require_photos() -> None:
    if not config.INVENTORY_PHOTOS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.post("/photos")
async def upload_photo(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    _require_photos()
    cap = config.INVENTORY_PHOTO_MAX_BYTES
    data = await file.read(cap + 1)
    if len(data) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The file is empty.")
    if len(data) > cap:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"Photos are capped at {cap // (1024 * 1024)} MB.")
    mime = sniff_image_mime(data[:16])
    if mime is None:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="Only JPEG, PNG or WebP images are accepted (checked by content).")
    safe_name = re.sub(r"[^A-Za-z0-9._ \-()]+", "_", (file.filename or "photo"))[:120]
    doc = {
        "category": "inventory_photo",
        "sourceType": "inventory_photo",
        "filename": safe_name,
        "mimetype": mime,
        "size": len(data),
        "content": data,
        "userId": str(getattr(current_user, "id", "")),
        "uploaderName": _actor(current_user),
        "createdAt": datetime.now(),
    }
    result = await files_collection.insert_one(doc)
    photo_id = str(result.inserted_id)
    await _audit(_actor(current_user), "upload_photo", f"files:{photo_id}",
                 {"filename": safe_name, "mimetype": mime, "size": len(data)})
    return {"photoId": photo_id, "url": f"/api/inventory/photos/{photo_id}",
            "mimetype": mime, "size": len(data)}


@router.get("/photos/{photo_id}")
async def get_photo(photo_id: str, current_user: User = Depends(get_current_user)):
    _require_photos()
    try:
        oid = ObjectId(photo_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    doc = await files_collection.find_one({"_id": oid, "category": "inventory_photo"})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(content=doc.get("content", b""), media_type=doc.get("mimetype") or "application/octet-stream",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.post("/reminders/run")
async def run_reminders_now(
    dryRun: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
):
    """Manual / cron trigger for the daily reminder digest (manager-only,
    404 unless INVENTORY_REMINDERS_ENABLED). Idempotent per (email, day);
    dryRun previews recipients without sending or recording."""
    if not config.INVENTORY_REMINDERS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    _require_manager(current_user, "send reminders")
    from app.services.inventory_reminders import run_reminders

    summary = await run_reminders(dry_run=dryRun)
    if not dryRun:
        await _audit(_actor(current_user), "reminders_run", "inv_reminders",
                     {"sent": len(summary["sent"]), "skipped": len(summary["skipped"]), "failed": len(summary["failed"])})
    return summary


async def _photo_exists(photo_id: Any) -> bool:
    try:
        oid = ObjectId(str(photo_id))
    except (InvalidId, TypeError):
        return False
    return await files_collection.find_one({"_id": oid, "category": "inventory_photo"}, {"_id": 1}) is not None


@router.get("/alerts")
async def inventory_alerts(current_user: User = Depends(get_current_user)):
    """The server-side alert list (alerts_for over one fetch) so the frontend
    renders alerts from the API instead of recomputing them client-side —
    one implementation, no drift."""
    from datetime import datetime as _dt

    from app.services.inventory_service import _fetch_inventory_data, alerts_for

    data = await _fetch_inventory_data()
    # Records carry severity/kind/detail plus additive itemId/refId/days so
    # the UI can deep-link and show server-clock overdue days.
    return {"alerts": alerts_for(
        data["items"], data["open_loans"], data["reservations"], data["plaxis"],
        _dt.now(),
    )}


def _is_archived(item: Dict[str, Any]) -> bool:
    return str(item.get("status") or "").lower() == "archived"


# ---------------------------------------------------------------------------
# Backup / restore (spec: backup and data recovery). Manager-only, audited.
# The backup is one JSON document: schemaVersion + exportedAt + the six
# collections. Restore is per-collection upsert-by-id ("merge") or upsert +
# delete-absent ("replace") — never a drop — and ALWAYS previewed as a
# dry-run diff before a confirmed write.
# ---------------------------------------------------------------------------
BACKUP_SCHEMA_VERSION = 1
_BACKUP_COLLECTIONS = ("items", "tx", "res", "plaxis", "users", "audit")
_ALL_DATE_KEYS = _DATE_FIELDS | {"createdAt", "updatedAt", "archivedAt", "nextMaint"}


def _normalize_backup_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """A backup document as it should be stored: no _id, ISO date strings
    parsed back to datetimes for the known date keys (JSON has no datetime)."""
    out = {k: v for k, v in doc.items() if k != "_id"}
    for key in _ALL_DATE_KEYS:
        v = out.get(key)
        if isinstance(v, str) and v.strip():
            try:
                out[key] = datetime.fromisoformat(v.strip().replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                pass
    return out


def _comparable(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Value-comparable form (datetimes to second precision, no _id)."""
    return {
        k: (v.replace(microsecond=0).isoformat() if isinstance(v, datetime) else v)
        for k, v in doc.items() if k != "_id"
    }


def restore_diff(backup_docs: List[Dict[str, Any]], existing_docs: List[Dict[str, Any]],
                 mode: str) -> Dict[str, Any]:
    """Pure dry-run diff for one collection: ids added / changed / removed.
    ``removed`` is only non-empty in replace mode — merge never deletes."""
    by_id_new = {str(d.get("id")): _normalize_backup_doc(d) for d in backup_docs if d.get("id")}
    by_id_old = {str(d.get("id")): d for d in existing_docs if d.get("id")}
    added = sorted(i for i in by_id_new if i not in by_id_old)
    changed = sorted(i for i in by_id_new if i in by_id_old
                     and _comparable(by_id_new[i]) != _comparable(by_id_old[i]))
    removed = sorted(i for i in by_id_old if i not in by_id_new) if mode == "replace" else []
    return {"added": len(added), "changed": len(changed), "removed": len(removed),
            "addedIds": added[:20], "changedIds": changed[:20], "removedIds": removed[:20],
            "unchanged": len(by_id_new) - len(added) - len(changed)}


def _backup_collection(name: str):
    return {
        "items": inv_items_collection, "tx": inv_tx_collection, "res": inv_res_collection,
        "plaxis": inv_plaxis_collection, "users": inv_users_collection,
        "audit": inv_audit_collection,
    }[name]


@router.get("/backup")
async def export_backup(current_user: User = Depends(get_current_user)):
    _require_manager(current_user, "export backups")
    collections: Dict[str, List[Dict[str, Any]]] = {}
    for name in _BACKUP_COLLECTIONS:
        collections[name] = [d async for d in _backup_collection(name).find({}, {"_id": 0})]
    await _audit(_actor(current_user), "backup", "inv_*",
                 {name: len(docs) for name, docs in collections.items()})
    return {
        "schemaVersion": BACKUP_SCHEMA_VERSION,
        "exportedAt": datetime.now(),
        "collections": collections,
    }


@router.post("/restore")
async def restore_backup(payload: Dict[str, Any], current_user: User = Depends(get_current_user)):
    """Body: {backup, mode: "merge"|"replace", dryRun: bool}. Always returns
    the per-collection diff; writes only when dryRun is false."""
    _require_manager(current_user, "restore backups")
    backup = (payload or {}).get("backup") or {}
    mode = str((payload or {}).get("mode") or "merge").lower()
    dry_run = bool((payload or {}).get("dryRun", True))
    if mode not in ("merge", "replace"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode must be merge or replace")
    if backup.get("schemaVersion") != BACKUP_SCHEMA_VERSION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported backup schemaVersion {backup.get('schemaVersion')!r} "
                   f"(this deployment restores version {BACKUP_SCHEMA_VERSION}).")
    collections = backup.get("collections") or {}
    unknown = sorted(set(collections) - set(_BACKUP_COLLECTIONS))
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Unknown collections in backup: {', '.join(unknown)}")
    diff: Dict[str, Any] = {}
    for name in _BACKUP_COLLECTIONS:
        if name not in collections:
            continue
        docs = collections[name]
        if not isinstance(docs, list):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{name} must be a list")
        coll = _backup_collection(name)
        existing = [d async for d in coll.find({}, {"_id": 0})]
        diff[name] = restore_diff(docs, existing, mode)
        if dry_run:
            continue
        present = set()
        for d in docs:
            if not d.get("id"):
                continue
            norm = _normalize_backup_doc(d)
            present.add(str(norm["id"]))
            await coll.replace_one({"id": norm["id"]}, norm, upsert=True)
        if mode == "replace":
            stale = [d["id"] for d in existing if str(d.get("id")) not in present and d.get("id")]
            if stale:
                await coll.delete_many({"id": {"$in": stale}})
    if not dry_run:
        await _audit(_actor(current_user), "restore", "inv_*", {"mode": mode, "diff": diff})
    return {"dryRun": dry_run, "mode": mode, "diff": diff}


# ---------------------------------------------------------------------------
# Archive / restore (spec: inventory records are never deleted). Manager-
# only, audited, reversible: the prior status is kept in archivedFrom.
# ---------------------------------------------------------------------------
@router.post("/items/{doc_id}/archive")
async def archive_item(doc_id: str, current_user: User = Depends(get_current_user)):
    _require_manager(current_user, "archive items")
    item = await inv_items_collection.find_one({"id": doc_id}, {"_id": 0})
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if _is_archived(item):
        return item
    changes = {
        "status": "Archived",
        "archivedFrom": item.get("status") or "Available",
        "archivedAt": datetime.now(),
        "updatedAt": datetime.now(),
    }
    await inv_items_collection.update_one({"id": doc_id}, {"$set": changes})
    await _audit(_actor(current_user), "archive_items", f"inv_items:{doc_id}",
                 {"from": changes["archivedFrom"]})
    return await inv_items_collection.find_one({"id": doc_id}, {"_id": 0})


@router.post("/items/{doc_id}/restore")
async def restore_item(doc_id: str, current_user: User = Depends(get_current_user)):
    _require_manager(current_user, "restore items")
    item = await inv_items_collection.find_one({"id": doc_id}, {"_id": 0})
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not _is_archived(item):
        return item
    restored = item.get("archivedFrom") or "Available"
    if str(restored).lower() == "reserved":
        restored = "Available"  # derived on read, never stored
    await inv_items_collection.update_one(
        {"id": doc_id},
        {"$set": {"status": restored, "updatedAt": datetime.now()},
         "$unset": {"archivedFrom": "", "archivedAt": ""}},
    )
    await _audit(_actor(current_user), "restore_items", f"inv_items:{doc_id}", {"to": restored})
    return await inv_items_collection.find_one({"id": doc_id}, {"_id": 0})


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.get("/{resource}")
async def list_resource(
    resource: str,
    itemId: Optional[str] = Query(default=None),
    open_only: bool = Query(default=False, alias="open"),
    limit: int = Query(default=1000, ge=1, le=5000),
    current_user: User = Depends(get_current_user),
):
    # inv_audit is READ-ONLY over the API: listable (newest first) so the log
    # page renders it, but never in _RESOURCES — audit rows are written only
    # by the server on mutations, so POST/PUT/DELETE on it stay 404.
    if resource == "audit":
        docs = [
            d async for d in inv_audit_collection.find({}, {"_id": 0})
            .sort("ts", -1).limit(limit)
        ]
        return {"items": docs, "total": len(docs)}
    coll, _fields = _resource(resource)
    query: Dict[str, Any] = {}
    if itemId:
        query["itemId"] = itemId
    if open_only and resource == "tx":
        query["type"] = "checkout"
        query["actualReturn"] = None
    docs = [d async for d in coll.find(query, {"_id": 0}).limit(limit)]
    if resource == "items":
        docs = await _items_view(docs)
    docs = [_hide_owner_key(resource, d) for d in docs]
    return {"items": docs, "total": len(docs)}


@router.post("/{resource}")
async def create_resource(
    resource: str,
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_user),
):
    coll, fields = _resource(resource)
    doc = _coerce(payload or {}, fields)
    doc.setdefault("id", uuid.uuid4().hex[:12])
    # Whose record a flag-on return closed (owner recorded separately from the
    # actor in the audit row); None everywhere else and with the flag off.
    audit_owner: Optional[str] = None
    # Reservation approval is manager-only: creating one already Approved (or
    # Denied) would bypass the Pending queue, so only a manager may do it.
    if resource == "res" and str(doc.get("status") or "Pending") != "Pending":
        _require_manager(current_user, "approve or deny reservations")
    if resource == "res":
        doc.setdefault("status", "Pending")
        doc["qty"] = max(int(doc.get("qty") or 1), 1)
        await _reject_if_conflicting(doc)
    # Creator provenance: ALWAYS the JWT caller, on every owned collection,
    # regardless of flag state (no data drift) — never client-suppliable
    # (absent from every field whitelist) and projected out of responses
    # flag-off (_hide_owner_key). Ownership checks never read it; it exists
    # so the audit trail and the row itself agree on who created what.
    if resource in ("tx", "res", "plaxis"):
        doc["createdByEmail"] = (getattr(current_user, "email", "") or "").strip() or None
    if resource in ("res", "plaxis"):
        # group is roster-resolved from the NAMED person — a client-supplied
        # group is overwritten, never trusted; an unknown name records null
        # and the write continues.
        doc["group"] = (await _roster_lookup(name=str(doc.get("user") or "")))["group"]
        # Owner key: never client-supplied (absent from the whitelists) and
        # never overwritten on update (an approval or edit must not reassign
        # ownership). Flag-on the owner is the person the row NAMES — an
        # on-behalf booking belongs to them, resolved through the roster or
        # 400 (_resolve_owner_email). Flag-off: the caller's email, exactly
        # as before, and projected out of every response.
        if config.INVENTORY_PERSONAL_VIEW:
            doc["email"] = await _resolve_owner_email(current_user, str(doc.get("user") or ""))
        else:
            doc["email"] = (getattr(current_user, "email", "") or "").strip() or None
    if resource == "plaxis":
        # Seat-window gate (flag-gated inside): a two-seat license exists to
        # prevent exactly the double-booked seat.
        await _reject_if_seat_conflict(doc)
    if resource == "users":
        _validate_roster_write(doc, creating=True)
    if resource == "tx":
        tx_type = doc.get("type")
        if tx_type not in _TX_TYPES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"type must be one of {sorted(_TX_TYPES)}")
        doc.setdefault("ts", datetime.now())
        # Optional damage photo: only a real inventory_photo document may be
        # referenced, and only on a damage report.
        if doc.get("photoId"):
            if tx_type != "damage":
                doc.pop("photoId", None)
            elif not config.INVENTORY_PHOTOS_ENABLED or not await _photo_exists(doc["photoId"]):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="Unknown photo reference.")
        # A return recorded against an existing open checkout closes that
        # checkout's row too, so the open-loan walk stops counting it. The
        # target is resolved BEFORE the roster join below so studentId/group
        # key on the loan's true owner.
        closes = None
        if tx_type == "return":
            doc.setdefault("actualReturn", datetime.now())
            closes = (payload or {}).get("closesTxId")
            if config.INVENTORY_PERSONAL_VIEW:
                # Ownership boundary: a return acts on ONE person's open loan,
                # so it must name that loan — load it, verify it is still
                # open (a second return of the same loan would decrement
                # qtyOut twice), and compare its owner to the caller BEFORE
                # any mutation. Managers may close on behalf (Phase 2); the
                # closed row keeps its original user/email — ownership is
                # never reassigned.
                if not closes:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="This return is not linked to an open loan. "
                               "Use the item's Return button and try again.")
                target = await inv_tx_collection.find_one(
                    {"id": closes, "type": "checkout"}, {"_id": 0})
                if target is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                        detail="Not found")
                if target.get("actualReturn"):
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                        detail="This loan is already returned.")
                if int(doc.get("qty") or 1) > int(target.get("qty") or 1):
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                        detail="The return quantity exceeds the open loan.")
                await _require_owner("tx", target, current_user,
                                     "return_tx", allow_manager=True)
                audit_owner = _row_owner_label(target)
                # Owner key: a return MIRRORS the loan it closes — inherit
                # the loan's stored owner (best-effort roster resolution for
                # a keyless legacy loan). Never reassigned to the actor and
                # never a 400 that would block a legitimate return.
                doc["email"] = (str(target.get("email") or "").strip()
                                or await _roster_email_for_name(str(target.get("user") or "")))
        elif config.INVENTORY_PERSONAL_VIEW:
            # Owner key for checkout / adjust / damage: the person the form
            # NAMES, roster-resolved (or the caller when it names them or is
            # blank). A client-typed email string is never stored; an
            # unkeyable name is a 400 (_resolve_owner_email).
            doc["email"] = await _resolve_owner_email(current_user, str(doc.get("user") or ""))
        # studentId and group are resolved server-side from the roster via
        # the BORROWER's email. Flag-on that is the server-resolved OWNER
        # key; flag-off it stays the form's email field exactly as before
        # (blank falls back to the session user). Any client-supplied
        # studentId/group is overwritten (never trusted). Unresolvable
        # email -> null, transaction continues.
        borrower_email = (
            str(doc.get("email") or "").strip()
            or (getattr(current_user, "email", "") or "")
        )
        identity = await _roster_lookup(email=borrower_email)
        doc["studentId"] = identity["studentId"]
        doc["group"] = identity["group"]
        if closes:
            await inv_tx_collection.update_one(
                {"id": closes, "type": "checkout"},
                {"$set": {"actualReturn": doc["actualReturn"]}},
            )
        await _apply_tx_side_effects(doc)
    if resource == "items":
        if await coll.find_one({"id": doc["id"]}):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=f"An item with id {doc['id']!r} already exists. "
                                       "Pick a different id.")
        _derive_item_fields(doc)
        strip_stored_reserved(doc)
    now = datetime.now()
    doc["createdAt"] = now
    doc["updatedAt"] = now
    try:
        await coll.insert_one(dict(doc))
    except DuplicateKeyError:
        # The partial unique index on HELD plaxis sessions is the TOCTOU
        # backstop behind _reject_if_seat_conflict: two truly concurrent
        # identical bookings can both pass the read-then-check gate, and the
        # index refuses whichever insert lands second. Translate that into
        # the SAME 409 the gate produces, so a lost race and a normal
        # conflict look identical to the user.
        if resource == "plaxis":
            seat = doc.get("seat")
            start, end = doc.get("start"), doc.get("end")
            holders: List[Dict[str, Any]] = []
            if isinstance(start, datetime) and isinstance(end, datetime):
                sessions = [d async for d in inv_plaxis_collection.find(
                    {"seat": seat}, {"_id": 0})]
                holders = seat_conflicts(sessions, seat, start, end)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=conflict_message(
                    {"name": f"PLAXIS seat {int(seat or 0) + 1}"}, holders))
        raise
    doc.pop("_id", None)
    detail: Any = doc.get("type") or doc.get("name")
    if resource == "tx" and doc.get("photoId"):
        detail = {"type": doc.get("type"), "photoId": doc["photoId"]}
    await _audit(_actor(current_user), f"create_{resource}", f"inv_{resource}:{doc['id']}",
                 detail, owner=audit_owner)
    return _hide_owner_key(resource, doc)


@router.put("/{resource}/{doc_id}")
async def update_resource(
    resource: str,
    doc_id: str,
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_user),
):
    # Reservation approval (any status change on inv_res) is manager-only —
    # checked BEFORE any DB read so a non-manager gets a clean 403.
    if resource == "res" and "status" in (payload or {}):
        _require_manager(current_user, "approve or deny reservations")
    coll, fields = _resource(resource)
    existing = await coll.find_one({"id": doc_id})
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    # Ownership boundary (INVENTORY_PERSONAL_VIEW): tx/res/plaxis rows have
    # exactly one owner — loaded above, compared BEFORE any mutation. A
    # reservation EDIT (any non-status content) is owner-only with no manager
    # bypass; the manager approval path (status-only changes, gated above) is
    # not an edit and stays as it is. A PLAXIS row is owner-only, except a
    # manager releasing the seat (loggedOut) on behalf — audited with actor
    # and owner recorded separately. A tx row rewrite is owner-only (and even
    # then bypasses no stock side effects — returns belong on POST /tx).
    audit_owner: Optional[str] = None
    if config.INVENTORY_PERSONAL_VIEW and resource in ("tx", "res", "plaxis"):
        if resource == "res":
            edit_keys = set(_coerce(payload or {}, fields)) - {"id", "status"}
            if edit_keys:
                await _require_owner("res", existing, current_user, "update_res")
        elif resource == "plaxis":
            releasing = bool((payload or {}).get("loggedOut"))
            if await _require_owner("plaxis", existing, current_user,
                                    "update_plaxis", allow_manager=releasing):
                audit_owner = _row_owner_label(existing)
        else:
            await _require_owner("tx", existing, current_user, "update_tx")
    # Optimistic-concurrency precondition: refuse a write built on a stale
    # read so concurrent edits surface instead of silently clobbering.
    expected = (payload or {}).get("expectedUpdatedAt")
    if expected:
        current = existing.get("updatedAt")
        current_iso = current.isoformat() if hasattr(current, "isoformat") else str(current or "")
        if current_iso[:19] != str(expected)[:19]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="This record was changed by someone else. Reload and retry.")
    changes = _coerce(payload or {}, fields)
    changes.pop("id", None)  # the key is immutable
    if resource == "users":
        _validate_roster_write(changes, creating=False)
    if resource == "items" and str(changes.get("status") or "").lower() == "archived":
        _require_manager(current_user, "archive items")
    if resource == "items" and any(k in changes for k in ("lastMaint", "maintDays")):
        merged = {**existing, **changes}
        _derive_item_fields(merged)
        changes["nextMaint"] = merged["nextMaint"]
    if resource == "items":
        strip_stored_reserved(changes)
    # A reservation whose window or status changes (incl. APPROVAL) must
    # still fit — a Pending request that became conflicted meanwhile is not
    # approvable. The row itself is excluded from its own check.
    if resource == "res" and any(k in changes for k in ("start", "end", "status", "qty")):
        merged = {**existing, **changes}
        await _reject_if_conflicting(merged, exclude_id=doc_id)
    # Same rule for a PLAXIS row whose seat or window moves (gate flag-gated
    # inside; the row never conflicts with itself).
    if resource == "plaxis" and any(k in changes for k in ("seat", "start", "end")):
        merged = {**existing, **changes}
        await _reject_if_seat_conflict(merged, exclude_id=doc_id)
    # Flag-on, tx ownership is server-stamped only — email is whitelisted on
    # tx for the flag-off write path, but a PUT must not reassign the owner.
    if resource == "tx" and config.INVENTORY_PERSONAL_VIEW:
        changes.pop("email", None)
    changes["updatedAt"] = datetime.now()
    await coll.update_one({"id": doc_id}, {"$set": changes})
    await _audit(_actor(current_user), f"update_{resource}", f"inv_{resource}:{doc_id}",
                 sorted(k for k in changes if k != "updatedAt"), owner=audit_owner)
    updated = await coll.find_one({"id": doc_id}, {"_id": 0})
    if resource == "items" and updated is not None:
        updated = (await _items_view([updated]))[0]
    return _hide_owner_key(resource, updated)


@router.delete("/{resource}/{doc_id}")
async def delete_resource(
    resource: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
):
    # Inventory records are NEVER deleted (spec): archive instead — see
    # archive_item / restore_item. 405 for everyone, managers included.
    if resource == "items":
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                            detail="Inventory records are archived, not deleted. Use archive.")
    # Deleting a roster entry (added in error) is not an inventory record —
    # still manager-only, checked BEFORE any DB access. Other resources (a
    # reservation cancel, closing a plaxis row) stay open.
    if resource == "users":
        _require_manager(current_user, "delete roster entries")
    # Ledger immutability (INVENTORY_PERSONAL_VIEW): deleting a tx row leaves
    # its stock side effect behind — an owner deleting their own open
    # checkout still leaves qtyOut overstated forever, the same corruption
    # class as the return bug. Flag-on the ledger cannot be deleted at all
    # (close the loan with a return; fix a count with an adjust). Flag-off
    # keeps today's behavior.
    if resource == "tx" and config.INVENTORY_PERSONAL_VIEW:
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                            detail=LEDGER_DELETE_DETAIL)
    coll, _fields = _resource(resource)
    # Ownership boundary (INVENTORY_PERSONAL_VIEW): cancelling a reservation
    # is owner-only — a manager clears someone else's booking by DENYING it
    # through the approval path, never by cancel. Deleting a plaxis row is
    # release-equivalent (the seat frees), so it keeps the manager bypass.
    audit_owner: Optional[str] = None
    if config.INVENTORY_PERSONAL_VIEW and resource in ("res", "plaxis"):
        row = await coll.find_one({"id": doc_id}, {"_id": 0})
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        if await _require_owner(resource, row, current_user, f"delete_{resource}",
                                allow_manager=(resource == "plaxis")):
            audit_owner = _row_owner_label(row)
    result = await coll.delete_one({"id": doc_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await _audit(_actor(current_user), f"delete_{resource}", f"inv_{resource}:{doc_id}",
                 owner=audit_owner)
    return {"deleted": doc_id}


# ---------------------------------------------------------------------------
# Read-only helpers: the deterministic snapshot + the feasibility engine
# ---------------------------------------------------------------------------
@router.get("/snapshot/build")
async def snapshot(
    scope: str = Query(default="all"),
    current_user: User = Depends(get_current_user),
):
    result = await build_inventory_snapshot_result(scope)
    return {
        "snapshot": result.text,
        "note": result.scope_note(),
        "included": result.included,
        "omitted": result.omitted,
        "tokenEstimate": result.token_estimate,
    }


@router.post("/feasibility/check")
async def feasibility(
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_user),
):
    raw_requests = (payload or {}).get("requests")
    if not isinstance(raw_requests, list) or not raw_requests:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="requests[] is required")
    try:
        requests = [ItemRequest(itemId=str(r["itemId"]), qty=int(r.get("qty", 1))) for r in raw_requests]
        start = datetime.fromisoformat(str(payload["start"]).replace("Z", "+00:00")).replace(tzinfo=None)
        end = datetime.fromisoformat(str(payload["end"]).replace("Z", "+00:00")).replace(tzinfo=None)
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="requests[{itemId, qty}], start and end (ISO dates) are required")
    if end <= start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end must be after start")
    report = await check_feasibility(requests, start, end)
    return {
        "feasible": report.feasible,
        "start": report.start.isoformat(),
        "end": report.end.isoformat(),
        "earliestAvailable": report.earliest_available.isoformat() if report.earliest_available else None,
        "items": [
            {
                "itemId": r.itemId, "name": r.name, "requested": r.requested,
                "status": r.status, "shortBy": r.short_by, "conflicts": r.conflicts,
            }
            for r in report.items
        ],
        "rendered": render_feasibility_report(report),
    }


# ---------------------------------------------------------------------------
# Personal view (INVENTORY_PERSONAL_VIEW): the caller's own slice of the SAME
# full data every other read serves. A separate router so the path exists only
# when the flag is on (the flag-off route table is byte-identical to today) —
# and registered BEFORE the CRUD router, or the /{resource} catch-all would
# swallow /me as a resource name.
# ---------------------------------------------------------------------------
personal_router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@personal_router.get("/me")
async def my_bench(current_user: User = Depends(get_current_user)):
    """The caller's open loans (overdueDays from the server clock), upcoming
    reservations (end >= now, any status), currently-held PLAXIS seats, and
    the subset of the server alert list about their rows. A PRESENTATION
    filter over one full fetch — the collection queries themselves are never
    user-scoped (availability math needs every row), and the alerts are the
    unforked alerts_for output filtered by refId. Empty lists, never a 404,
    for a user with nothing out."""
    from app.services.inventory_service import _as_dt, _fetch_inventory_data, alerts_for

    email, names = await _caller_identity(current_user)
    data = await _fetch_inventory_data()
    now = datetime.now()

    def owned(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [r for r in rows if _owns_row(r, email, names)]

    def owned_by_key(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # res/plaxis ownership is the stored owner key ONLY (no name
        # fallback — same rule as _require_owner). A keyless legacy row
        # belongs to nobody until the backfill resolves it.
        return [
            r for r in rows
            if email and str(r.get("email") or "").strip().lower() == email
        ]

    loans: List[Dict[str, Any]] = []
    for t in owned(data["open_loans"]):
        due = _as_dt(t.get("expectedReturn"))
        overdue = (now - due).days if (due is not None and due < now) else 0
        loans.append({**t, "overdueDays": overdue})
    loans.sort(key=lambda t: (str(t.get("expectedReturn") or "9999"), str(t.get("id"))))

    reservations = [
        r for r in owned_by_key(data["reservations"])
        if (_as_dt(r.get("end")) or now) >= now
    ]
    reservations.sort(key=lambda r: (str(r.get("start") or ""), str(r.get("id"))))

    plaxis = owned_by_key(data["plaxis"])  # _fetch_inventory_data already keeps only held seats
    plaxis.sort(key=lambda p: (str(p.get("start") or ""), str(p.get("id"))))

    my_ids = {str(r.get("id")) for r in loans + reservations + plaxis}
    alerts = [
        a for a in alerts_for(data["items"], data["open_loans"],
                              data["reservations"], data["plaxis"], now)
        if a.get("refId") and a["refId"] in my_ids
    ]
    return {"loans": loans, "reservations": reservations,
            "plaxis": plaxis, "alerts": alerts}


def register(app: FastAPI) -> None:
    """Include the routes ONLY when INVENTORY_ENABLED is on — with the flag
    off the routes are absent and the app's route table is unchanged. The
    personal-view route additionally needs INVENTORY_PERSONAL_VIEW (both off:
    byte-identical route table; the frontend treats the 404 as flag-off)."""
    if config.INVENTORY_ENABLED:
        if config.INVENTORY_PERSONAL_VIEW:
            app.include_router(personal_router)
        app.include_router(router)
