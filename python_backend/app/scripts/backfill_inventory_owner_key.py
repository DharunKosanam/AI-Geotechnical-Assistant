"""Backfill the inv_res / inv_plaxis owner key (``email`` — the same field
inv_tx has always keyed ownership on) for rows written before the key
existed.

For every row LACKING the key, the stored display name is resolved against
inv_users by EXACT case-insensitive equality and classified:

  resolved   — exactly one roster match with a non-blank email
  ambiguous  — more than one roster match
  unresolved — no match, a blank stored name, or a matched roster row whose
               own email is blank

Nothing is ever written for ambiguous or unresolved rows — no guessing, no
first-match, no fuzzy matching. They are reported individually (collection,
row id, stored name, item/seat, date range) for a human to fix by hand.

Default is ``--dry-run`` (running with no arguments writes NOTHING).
``--apply`` sets the key on resolved rows only — nothing else on the row is
touched (deliberately not even ``updatedAt``: the key is invisible to
flag-off clients, and a silent bump would surface phantom edit conflicts) —
and records each write to inv_audit with the actor marked as this script.
Idempotent: a second pass finds zero rows lacking the key and writes
nothing.

Run (from the python_backend directory):
    python -m app.scripts.backfill_inventory_owner_key            # dry run
    python -m app.scripts.backfill_inventory_owner_key --apply    # write
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

ACTOR = "backfill_inventory_owner_key"
# tx included for symmetry (its email predates the key work but legacy rows
# can lack it); same never-guess classification for all three.
COLLECTIONS = ("tx", "res", "plaxis")


def _has_key(row: Dict[str, Any]) -> bool:
    return bool(str(row.get("email") or "").strip())


def _fmt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value) if value else "?"


def describe(collection: str, row: Dict[str, Any]) -> str:
    """One human-readable line: collection, row id, stored name, item/seat,
    date range — everything needed to fix the row by hand."""
    if collection == "tx":
        where = f"item {row.get('itemId')} ({row.get('type')})"
        window = (row.get("ts"), row.get("expectedReturn"))
    elif collection == "res":
        where = f"item {row.get('itemId')}"
        window = (row.get("start"), row.get("end"))
    else:
        where = f"seat {row.get('seat')}"
        window = (row.get("start"), row.get("end"))
    return (f"{collection} {row.get('id')} {str(row.get('user') or '(blank name)')!r} "
            f"{where} {_fmt(window[0])} -> {_fmt(window[1])}")


@dataclass
class Classification:
    resolved: List[Tuple[Dict[str, Any], str]] = field(default_factory=list)
    ambiguous: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = field(default_factory=list)
    unresolved: List[Tuple[Dict[str, Any], str]] = field(default_factory=list)


def classify_rows(rows: Sequence[Dict[str, Any]],
                  users: Sequence[Dict[str, Any]]) -> Classification:
    """Pure core: classify every row lacking the key. Exact case-insensitive
    name equality only — never a guess."""
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for u in users:
        key = str(u.get("name") or "").strip().lower()
        if key:
            by_name.setdefault(key, []).append(u)
    out = Classification()
    for row in rows:
        if _has_key(row):
            continue
        name = str(row.get("user") or "").strip()
        if not name:
            out.unresolved.append((row, "blank stored name"))
            continue
        matches = by_name.get(name.lower(), [])
        if not matches:
            out.unresolved.append((row, "no roster match"))
        elif len(matches) > 1:
            out.ambiguous.append((row, matches))
        else:
            email = str(matches[0].get("email") or "").strip()
            if email:
                out.resolved.append((row, email))
            else:
                out.unresolved.append((row, "roster row has no email"))
    return out


async def run_backfill(apply: bool, db=None) -> Dict[str, Any]:
    """Fetch, classify, report — and with ``apply`` set the key on resolved
    rows only, auditing each write. ``db`` is injectable for tests."""
    if db is None:
        from app.core import database as db  # noqa: PLC0415

    users = [d async for d in db.inv_users_collection.find({}, {"_id": 0})]
    summary: Dict[str, Any] = {"apply": apply, "collections": {}}
    for name in COLLECTIONS:
        coll = getattr(db, f"inv_{name}_collection")
        rows = [d async for d in coll.find({}, {"_id": 0})]
        c = classify_rows(rows, users)
        lacking = len(c.resolved) + len(c.ambiguous) + len(c.unresolved)
        print(f"[BACKFILL] inv_{name}: {len(rows)} rows, {lacking} lacking the owner key")
        print(f"[BACKFILL]   resolved: {len(c.resolved)}   "
              f"ambiguous: {len(c.ambiguous)}   unresolved: {len(c.unresolved)}")
        for row, matches in c.ambiguous:
            ids = ", ".join(str(m.get("id")) for m in matches)
            print(f"[BACKFILL]   AMBIGUOUS  {describe(name, row)} (roster matches: {ids})")
        for row, reason in c.unresolved:
            print(f"[BACKFILL]   UNRESOLVED {describe(name, row)} ({reason})")
        written = 0
        if apply:
            for row, email in c.resolved:
                await coll.update_one({"id": row["id"]}, {"$set": {"email": email}})
                await db.inv_audit_collection.insert_one({
                    "id": uuid.uuid4().hex,
                    "ts": datetime.now(),
                    "actor": ACTOR,
                    "action": f"backfill_owner_key_{name}",
                    "entity": f"inv_{name}:{row['id']}",
                    "detail": {"email": email},
                    "owner": str(row.get("user") or email),
                })
                written += 1
            print(f"[BACKFILL]   wrote {written} row(s) to inv_{name}")
        summary["collections"][name] = {
            "total": len(rows), "lacking": lacking,
            "resolved": len(c.resolved), "ambiguous": len(c.ambiguous),
            "unresolved": len(c.unresolved), "written": written,
        }
    if not apply:
        print("[BACKFILL] dry run: nothing written. Re-run with --apply to set "
              "the key on resolved rows.")
    return summary


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="classify and report only (the default)")
    mode.add_argument("--apply", action="store_true",
                      help="set the key on resolved rows (audited)")
    args = parser.parse_args()

    from app.core.database import close_mongo_connection

    try:
        await run_backfill(apply=bool(args.apply))
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(_main())
