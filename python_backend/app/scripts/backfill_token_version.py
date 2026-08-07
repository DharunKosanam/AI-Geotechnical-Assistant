"""
Backfill token_version onto user docs that predate the field.

Phase 2 of password reset. get_current_user treats a MISSING (or None)
token_version as 1, so the app is correct without this backfill -- running it
just makes the field explicit on every user doc, so later code (the reset
endpoint's $inc, ad-hoc queries) never has to reason about absent fields.

Idempotent and non-destructive: it only SETS token_version: 1 where the field
is absent or None; docs that already carry a version (including one that has
been bumped past 1) are never touched, so existing sessions are unaffected.

SAFETY MODEL
  * DRY RUN by default: prints what would change and changes nothing.
  * Requires an explicit --apply flag to write.

Run (from the python_backend directory):
    python -m app.scripts.backfill_token_version           # dry run
    python -m app.scripts.backfill_token_version --apply   # write
"""
from __future__ import annotations

import sys

from app.core.database import close_mongo_connection, users_collection

APPLY_FLAG = "--apply"

# Matches the runtime rule in auth_service.effective_token_version: absent and
# None both mean "version 1", so both shapes are backfilled.
MISSING_FILTER = {
    "$or": [
        {"token_version": {"$exists": False}},
        {"token_version": None},
    ]
}


async def backfill(apply: bool) -> int:
    """Run the backfill (or dry run). Returns a process exit code (0 = OK)."""
    print("=" * 60)
    print("BACKFILL: users.token_version (%s)" % ("APPLY" if apply else "DRY RUN"))
    print("=" * 60)

    total = await users_collection.count_documents({})
    missing = await users_collection.count_documents(MISSING_FILTER)
    print("Users total                          : %d" % total)
    print("Users missing/None token_version     : %d" % missing)
    print("Users already carrying token_version : %d" % (total - missing))
    print("-" * 60)

    if not apply:
        print("DRY RUN: would set token_version: 1 on %d user(s)." % missing)
        print("Nothing was changed. Re-run with %s to write." % APPLY_FLAG)
        print("=" * 60)
        return 0

    result = await users_collection.update_many(
        MISSING_FILTER, {"$set": {"token_version": 1}}
    )
    remaining = await users_collection.count_documents(MISSING_FILTER)
    print("Set token_version: 1 on %d user(s)." % result.modified_count)
    print("Users still missing token_version    : %d" % remaining)
    print("=" * 60)
    if remaining != 0:
        print("WARNING: %d user(s) still missing the field. Investigate." % remaining)
        return 1
    print("OK: every user doc now carries token_version.")
    return 0


async def _main(apply: bool) -> int:
    try:
        return await backfill(apply)
    finally:
        await close_mongo_connection()


def main() -> None:
    apply = APPLY_FLAG in sys.argv[1:]

    import asyncio

    rc = asyncio.run(_main(apply))
    sys.exit(rc)


if __name__ == "__main__":
    main()
