"""
DESTRUCTIVE one-shot cleanup: delete the legacy "default-user" test data.

Phase 4A step 3. Deletes the legacy single-user test data so the app can move
to real per-user scoping. Targets (confirmed from the audit):
    conversations : userId == "default-user"                      (expect 19)
    messages      : userId == "default-user"                      (expect 524)
    files         : category == "user_upload" AND userId=="..."   (expect 564)

PRESERVED, NEVER TOUCHED:
    files         : category == "knowledge_base"   (CONTROL, expect 16811)

SAFETY MODEL
  * Refuses to run without the --yes-i-am-sure flag.
  * EVERY delete against the files collection goes through _guarded_files_delete,
    which aborts unless the filter explicitly contains category=="user_upload".
    files_collection.delete_many is never called directly anywhere else, so it
    is structurally impossible for this script to match category=="knowledge_base".
  * Prints the knowledge_base count BEFORE and AFTER and asserts they are equal;
    any difference prints a loud FAILURE and exits non-zero.

Run (from the python_backend directory):
    python -m app.scripts.purge_default_user --yes-i-am-sure
"""
from __future__ import annotations

import sys

from app.core.database import (
    close_mongo_connection,
    conversations_collection,
    files_collection,
    messages_collection,
)

# Legacy hardcoded id. Defined locally so this stays correct regardless of any
# later change to config.USER_ID.
LEGACY_USER_ID = "default-user"

CONFIRM_FLAG = "--yes-i-am-sure"


async def _guarded_files_delete(delete_filter: dict) -> int:
    """Delete from the files collection ONLY when the filter is explicitly
    scoped to category=="user_upload".

    This is the single choke point for every files delete in this script. The
    guard makes it structurally impossible to match category=="knowledge_base":
    if category is not exactly "user_upload", the script aborts before touching
    the DB.
    """
    if delete_filter.get("category") != "user_upload":
        raise SystemExit(
            "ABORT: refusing a files delete that is not scoped to "
            "category=='user_upload'. Filter was: %r" % (delete_filter,)
        )
    result = await files_collection.delete_many(delete_filter)
    return result.deleted_count


async def purge() -> int:
    """Run the purge. Returns a process exit code (0 = OK, 1 = control failed)."""
    print("=" * 60)
    print("PURGE: legacy '%s' test data (DESTRUCTIVE)" % LEGACY_USER_ID)
    print("=" * 60)

    # (1) BEFORE control number.
    kb_before = await files_collection.count_documents(
        {"category": "knowledge_base"}
    )
    print("CONTROL knowledge_base BEFORE : %d" % kb_before)
    print("-" * 60)

    # (2) Delete conversations (userId-scoped; this collection has no KB data).
    conv_deleted = (
        await conversations_collection.delete_many({"userId": LEGACY_USER_ID})
    ).deleted_count
    print("Deleted conversations (userId=='%s') : %d" % (LEGACY_USER_ID, conv_deleted))

    # (2) Delete messages (userId-scoped; includes orphaned messages by design).
    msg_deleted = (
        await messages_collection.delete_many({"userId": LEGACY_USER_ID})
    ).deleted_count
    print("Deleted messages      (userId=='%s') : %d" % (LEGACY_USER_ID, msg_deleted))

    # (2+3) Delete user_upload files via the HARD-GUARDED choke point only.
    files_filter = {"category": "user_upload", "userId": LEGACY_USER_ID}
    files_deleted = await _guarded_files_delete(files_filter)
    print(
        "Deleted files         (category=='user_upload' AND userId=='%s') : %d"
        % (LEGACY_USER_ID, files_deleted)
    )

    # (4) AFTER control number + equality assertion.
    print("-" * 60)
    kb_after = await files_collection.count_documents(
        {"category": "knowledge_base"}
    )
    print("CONTROL knowledge_base AFTER  : %d" % kb_after)

    print("=" * 60)
    if kb_after != kb_before:
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("FAILURE: knowledge_base count CHANGED (%d -> %d). INVESTIGATE NOW."
              % (kb_before, kb_after))
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        return 1

    print("OK: knowledge_base preserved (%d unchanged)." % kb_after)
    print("Deleted totals -> conversations: %d, messages: %d, user_upload files: %d"
          % (conv_deleted, msg_deleted, files_deleted))
    print("=" * 60)
    return 0


async def _main() -> int:
    try:
        return await purge()
    finally:
        await close_mongo_connection()


def main() -> None:
    if CONFIRM_FLAG not in sys.argv[1:]:
        print("Refusing to run without confirmation.")
        print("This DELETES '%s' conversations, messages, and user_upload files." % LEGACY_USER_ID)
        print("knowledge_base files are preserved.")
        print("Re-run with:")
        print("    python -m app.scripts.purge_default_user %s" % CONFIRM_FLAG)
        raise SystemExit(2)

    import asyncio

    rc = asyncio.run(_main())
    sys.exit(rc)


if __name__ == "__main__":
    main()
