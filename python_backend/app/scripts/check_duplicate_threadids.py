"""Read-only check: does any ``conversations.threadId`` appear more than once?

Audit F-02 (2026-08-26). Two ``conversations`` rows for one threadId let the
row that is NOT the owner's take the id over once the owner deletes theirs,
exposing the remaining members' orphaned turns. The code guard (409 in
POST /api/assistants/threads/history) closes the door for new registrations;
the race-proof half is a UNIQUE index on ``conversations.threadId``, which is
a live Atlas write and must be applied deliberately -- and can only be built
if no duplicates exist. This script answers that question and WRITES NOTHING:
one aggregation, one find per duplicate id, printed counts, exit code 1 when
duplicates exist (0 when clean).

Run (from the python_backend directory, against the .env's MONGODB_URI):
    python -m app.scripts.check_duplicate_threadids

Then, only when it reports "no duplicate threadIds", the index (NOT run here):
    db.conversations.createIndex({threadId: 1}, {unique: true, name: "uniq_threadId"})
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict, List


async def find_duplicate_thread_ids(coll) -> List[Dict[str, Any]]:
    """Aggregate ``{_id: threadId, n}`` for every threadId with n > 1, most
    duplicated first. Pure read (``$group`` + ``$match``), injectable ``coll``
    for tests."""
    pipeline = [
        {"$group": {"_id": "$threadId", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$sort": {"n": -1, "_id": 1}},
    ]
    return [d async for d in coll.aggregate(pipeline)]


async def run_check(db=None) -> int:
    """Print the report; return the number of duplicated threadIds."""
    if db is None:
        from app.core import database as db  # noqa: PLC0415

    coll = db.conversations_collection
    total = await coll.count_documents({})
    dups = await find_duplicate_thread_ids(coll)

    print(f"[DUP-CHECK] conversations: {total} row(s), "
          f"{len(dups)} threadId(s) registered more than once")
    for d in dups:
        thread_id = d["_id"]
        rows = [r async for r in coll.find(
            {"threadId": thread_id},
            {"userId": 1, "name": 1, "isGroup": 1, "members": 1, "createdAt": 1},
        ).sort("createdAt", 1)]
        print(f"[DUP-CHECK]   threadId={thread_id!r} x{d['n']}")
        for r in rows:
            print(f"[DUP-CHECK]     userId={r.get('userId')!r} isGroup={r.get('isGroup')!r} "
                  f"members={r.get('members')!r} name={r.get('name')!r} "
                  f"createdAt={r.get('createdAt')}")
    if dups:
        print("[DUP-CHECK] RESULT: duplicates exist -- resolve them BEFORE creating the "
              "unique index (createIndex would fail).")
    else:
        print("[DUP-CHECK] RESULT: no duplicate threadIds -- the unique index can be created.")
    print("[DUP-CHECK] Nothing was written.")
    return len(dups)


def main() -> int:
    n = asyncio.run(run_check())
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
