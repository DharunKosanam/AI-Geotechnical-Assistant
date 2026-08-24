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
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status

from app.core import config
from app.core.database import (
    inv_audit_collection,
    inv_items_collection,
    inv_plaxis_collection,
    inv_res_collection,
    inv_tx_collection,
    inv_users_collection,
)
from app.dependencies.auth import get_current_user
from app.services.inventory_service import (
    ItemRequest,
    build_inventory_snapshot_result,
    check_feasibility,
    render_feasibility_report,
)
from models import User

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# Resource name -> (collection, allowed fields). ``id`` is the domain key
# (unique-indexed on inv_items); Mongo _id never leaves the API.
_ITEM_FIELDS = (
    "id", "name", "category", "subCategory", "kind", "manufacturer", "model",
    "serial", "qty", "qtyOut", "unit", "location", "custodian", "condition",
    "status", "minStock", "purchaseDate", "expiryDate", "maintDays",
    "lastMaint", "supplier", "notes",
)
_TX_FIELDS = (
    "id", "itemId", "type", "user", "email", "group", "qty", "ts",
    "expectedReturn", "actualReturn", "condBefore", "condAfter", "purpose",
    "approval", "studentId",
)
_RES_FIELDS = ("id", "itemId", "user", "group", "start", "end", "purpose", "status", "notes")
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


async def _audit(actor: str, action: str, entity: str, detail: Any = None) -> None:
    """Best-effort audit record (never blocks the operation it describes)."""
    try:
        await inv_audit_collection.insert_one({
            "id": uuid.uuid4().hex,
            "ts": datetime.now(),
            "actor": actor,
            "action": action,
            "entity": entity,
            "detail": detail,
        })
    except Exception as e:  # noqa: BLE001
        print(f"[INV_AUDIT] failed to record {action} on {entity}: {e}")


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
                            detail=f"Unknown itemId: {doc.get('itemId')!r}")
    qty = int(doc.get("qty") or 1)
    tx_type = doc.get("type")
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
    return {"enabled": True}


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
    # Reservation approval is manager-only: creating one already Approved (or
    # Denied) would bypass the Pending queue, so only a manager may do it.
    if resource == "res" and str(doc.get("status") or "Pending") != "Pending":
        _require_manager(current_user, "approve or deny reservations")
    if resource in ("res", "plaxis"):
        # group is roster-resolved from the NAMED person (these schemas carry
        # no email column) — a client-supplied group is overwritten, never
        # trusted; an unknown name records null and the write continues.
        doc["group"] = (await _roster_lookup(name=str(doc.get("user") or "")))["group"]
    if resource == "tx":
        tx_type = doc.get("type")
        if tx_type not in _TX_TYPES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"type must be one of {sorted(_TX_TYPES)}")
        doc.setdefault("ts", datetime.now())
        # studentId and group are resolved server-side from the roster via
        # the BORROWER's email — the form's email field, so an on-behalf
        # transaction names the actual borrower; blank falls back to the
        # session user (the form prefills their email anyway). Any client-
        # supplied studentId/group is overwritten (never trusted).
        # Unresolvable email -> null, transaction continues.
        borrower_email = (
            str(doc.get("email") or "").strip()
            or (getattr(current_user, "email", "") or "")
        )
        identity = await _roster_lookup(email=borrower_email)
        doc["studentId"] = identity["studentId"]
        doc["group"] = identity["group"]
        # A return recorded against an existing open checkout closes that
        # checkout's row too, so the open-loan walk stops counting it.
        if tx_type == "return":
            doc.setdefault("actualReturn", datetime.now())
            closes = (payload or {}).get("closesTxId")
            if closes:
                await inv_tx_collection.update_one(
                    {"id": closes, "type": "checkout"},
                    {"$set": {"actualReturn": doc["actualReturn"]}},
                )
        await _apply_tx_side_effects(doc)
    if resource == "items" and await coll.find_one({"id": doc["id"]}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Item id {doc['id']!r} already exists")
    now = datetime.now()
    doc["createdAt"] = now
    doc["updatedAt"] = now
    await coll.insert_one(dict(doc))
    doc.pop("_id", None)
    await _audit(_actor(current_user), f"create_{resource}", f"inv_{resource}:{doc['id']}", doc.get("type") or doc.get("name"))
    return doc


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
    changes["updatedAt"] = datetime.now()
    await coll.update_one({"id": doc_id}, {"$set": changes})
    await _audit(_actor(current_user), f"update_{resource}", f"inv_{resource}:{doc_id}",
                 sorted(k for k in changes if k != "updatedAt"))
    updated = await coll.find_one({"id": doc_id}, {"_id": 0})
    return updated


@router.delete("/{resource}/{doc_id}")
async def delete_resource(
    resource: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
):
    # Deleting items or lab members is the one action the audit trail cannot
    # reverse — manager-only, checked BEFORE any DB access. Other resources
    # (a reservation cancel, closing a plaxis row) stay open.
    if resource in ("items", "users"):
        _require_manager(current_user, "delete inventory records")
    coll, _fields = _resource(resource)
    result = await coll.delete_one({"id": doc_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await _audit(_actor(current_user), f"delete_{resource}", f"inv_{resource}:{doc_id}")
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


def register(app: FastAPI) -> None:
    """Include the routes ONLY when INVENTORY_ENABLED is on — with the flag
    off the routes are absent and the app's route table is unchanged."""
    if config.INVENTORY_ENABLED:
        app.include_router(router)
