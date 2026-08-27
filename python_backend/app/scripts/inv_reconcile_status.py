"""One-shot reconciliation: items whose STORED status disagrees with their
reservations.

``Reserved`` is derived from the live inv_res rows on every read (see
inventory_service.derive_reserved), so a stale stored value is already
invisible to the table, the drawer and the chat snapshot. This script cleans
the stored field itself so the database says what the readers say.

    venv/bin/python -m app.scripts.inv_reconcile_status            # dry-run (default): report only
    venv/bin/python -m app.scripts.inv_reconcile_status --apply    # rewrite stale Reserved -> Available

Only the stale case is rewritten (stored Reserved, no live reservation).
Stored Available with live rows is reported but left alone: the read path
shows it as Reserved and it clears itself when the rows end. Every rewrite
is audited (inv_audit action ``reconcile_status``).
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from typing import Any, Dict, List

from app.core.database import (
    close_mongo_connection,
    inv_items_collection,
    inv_res_collection,
)
from app.services.inventory_service import reconcile_status_plan


def _fmt(row: Dict[str, Any]) -> str:
    return (f"  {row['id']:<12} stored={row['stored']!r:<20} derived={row['derived']!r:<12} "
            f"live_reservations={row['live']}  {row['name']}")


async def run(apply: bool) -> List[Dict[str, Any]]:
    items = [d async for d in inv_items_collection.find({}, {"_id": 0})]
    reservations = [d async for d in inv_res_collection.find({}, {"_id": 0})]
    plan = reconcile_status_plan(items, reservations, datetime.now())
    print(f"{len(items)} items, {len(reservations)} reservation rows, "
          f"{len(plan)} disagreement(s){'' if apply else ' — DRY RUN, nothing written'}")
    for row in plan:
        print(_fmt(row))
    if not apply:
        return plan
    # Deferred import: the router module wires FastAPI; the audit helper is
    # the same one every API mutation uses, so the log stays uniform.
    from app.routers.inventory import _audit

    fixed = 0
    for row in plan:
        if row["stored"].lower() == "reserved" and row["derived"] == "Available":
            await inv_items_collection.update_one(
                {"id": row["id"]},
                {"$set": {"status": "Available", "updatedAt": datetime.now()}},
            )
            await _audit("inv_reconcile_status", "reconcile_status", f"inv_items:{row['id']}",
                         {"from": row["stored"], "to": "Available", "liveReservations": row["live"]})
            fixed += 1
    print(f"rewrote {fixed} item(s); the rest are report-only")
    return plan


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="rewrite stale stored Reserved to Available (default: dry-run report)")
    args = ap.parse_args()

    async def _go():
        try:
            await run(apply=args.apply)
        finally:
            await close_mongo_connection()

    asyncio.run(_go())


if __name__ == "__main__":
    main()
