"""Backfill ``conversations.members`` (CHAT_SHARING_ENABLED groundwork).

Every conversation gains ``members: [userId]`` — the owner, always a member.
Rows already carrying the field are untouched (idempotent: a second pass
finds zero rows lacking it and writes nothing). A row with no ``userId``
cannot be attributed and is REPORTED, never guessed and never written.

Default is ``--dry-run`` (running with no arguments writes NOTHING).
``--apply`` is required to write, and prints before/after counts.

Run (from the python_backend directory):
    python -m app.scripts.backfill_thread_members            # dry run
    python -m app.scripts.backfill_thread_members --apply    # write
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any, Dict


async def run_backfill(apply: bool, db=None) -> Dict[str, Any]:
    """Classify + report; write only with ``apply``. ``db`` injectable for
    tests (needs a ``conversations_collection``)."""
    if db is None:
        from app.core import database as db  # noqa: PLC0415

    coll = db.conversations_collection
    total = await coll.count_documents({})
    lacking = [d async for d in coll.find({"members": {"$exists": False}})]
    settable = [d for d in lacking if d.get("userId")]
    orphans = [d for d in lacking if not d.get("userId")]

    print(f"[BACKFILL] conversations: {total} total, {len(lacking)} lacking members")
    print(f"[BACKFILL]   would set members=[userId] on {len(settable)} row(s); "
          f"{len(orphans)} row(s) have NO userId (skipped, listed below)")
    for d in orphans:
        print(f"[BACKFILL]   ORPHAN threadId={d.get('threadId')!r} "
              f"name={d.get('name')!r} createdAt={d.get('createdAt')}")

    written = 0
    if apply:
        for d in settable:
            await coll.update_one({"_id": d["_id"]},
                                  {"$set": {"members": [d["userId"]]}})
            written += 1
        remaining = await coll.count_documents({"members": {"$exists": False}})
        print(f"[BACKFILL] wrote {written} row(s); "
              f"{remaining} row(s) still lack members "
              f"({len(orphans)} expected orphans)")
    else:
        print("[BACKFILL] dry run: nothing written. Re-run with --apply to write.")
    return {"total": total, "lacking": len(lacking), "settable": len(settable),
            "orphans": len(orphans), "written": written}


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="classify and report only (the default)")
    mode.add_argument("--apply", action="store_true",
                      help="set members=[userId] on rows lacking it")
    args = parser.parse_args()

    from app.core.database import close_mongo_connection

    try:
        await run_backfill(apply=bool(args.apply))
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(_main())
