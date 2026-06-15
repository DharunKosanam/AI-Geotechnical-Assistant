"""
READ-ONLY diagnostic: inventory the legacy "default-user" test data.

Phase 4A audit. Prints how much user-scoped data is tagged with the legacy
hardcoded userId "default-user" across the conversations, messages, and files
collections -- WITHOUT deleting anything. The knowledge_base file count is the
CONTROL: it must stay identical before and after any later purge.

CRITICAL CONTEXT: in the files collection, knowledge_base chunks ALSO carry
userId="default-user" (the app was single-user). So user data there can only be
distinguished by category="user_upload" -- never by userId alone. This audit
makes that split explicit so a later purge can be scoped safely.

Run (from the python_backend directory):
    python -m app.scripts.audit_default_user
"""
from __future__ import annotations

import asyncio

from app.core.database import (
    close_mongo_connection,
    conversations_collection,
    files_collection,
    messages_collection,
)

# The legacy hardcoded id. Defined locally so this audit stays correct even
# after config.USER_ID is eventually changed/removed.
LEGACY_USER_ID = "default-user"


def _rule() -> None:
    print("-" * 60)


async def audit() -> None:
    print("=" * 60)
    print("AUDIT: legacy '%s' data (READ-ONLY, no deletes)" % LEGACY_USER_ID)
    print("=" * 60)

    # --- conversations -----------------------------------------------------
    conv_count = await conversations_collection.count_documents(
        {"userId": LEGACY_USER_ID}
    )
    conv_total = await conversations_collection.count_documents({})
    thread_ids = await conversations_collection.distinct(
        "threadId", {"userId": LEGACY_USER_ID}
    )
    print()
    print("CONVERSATIONS (scoping field: userId)")
    _rule()
    print("  userId == '%s'            : %d" % (LEGACY_USER_ID, conv_count))
    print("  total in collection         : %d" % conv_total)
    print("  distinct threadIds for user : %d" % len(thread_ids))

    # --- messages ----------------------------------------------------------
    msg_by_user = await messages_collection.count_documents(
        {"userId": LEGACY_USER_ID}
    )
    msg_by_thread = (
        await messages_collection.count_documents(
            {"threadId": {"$in": thread_ids}}
        )
        if thread_ids
        else 0
    )
    msg_total = await messages_collection.count_documents({})
    print()
    print("MESSAGES (scoping fields: userId / threadId)")
    _rule()
    print("  userId == '%s'               : %d" % (LEGACY_USER_ID, msg_by_user))
    print("  threadId in that user's threads : %d" % msg_by_thread)
    print("  total in collection            : %d" % msg_total)

    # --- files -------------------------------------------------------------
    # CRITICAL: knowledge_base chunks ALSO carry userId="default-user", so the
    # ONLY safe discriminator for user data here is category="user_upload".
    kb_count = await files_collection.count_documents(
        {"category": "knowledge_base"}
    )
    upload_total = await files_collection.count_documents(
        {"category": "user_upload"}
    )
    upload_default = await files_collection.count_documents(
        {"category": "user_upload", "userId": LEGACY_USER_ID}
    )
    upload_other = await files_collection.count_documents(
        {"category": "user_upload", "userId": {"$ne": LEGACY_USER_ID}}
    )
    other_cat = await files_collection.count_documents(
        {"category": {"$nin": ["user_upload", "knowledge_base"]}}
    )
    files_total = await files_collection.count_documents({})

    print()
    print("FILES (scoping fields: category / userId)")
    _rule()
    print("  category == 'knowledge_base'  : %d   <-- CONTROL (must NOT change)" % kb_count)
    print("  category == 'user_upload' (all) : %d" % upload_total)
    print("      userId == '%s' : %d   <-- deletion target" % (LEGACY_USER_ID, upload_default))
    print("      userId != '%s' : %d" % (LEGACY_USER_ID, upload_other))
    print("  other / missing category      : %d   (left untouched)" % other_cat)
    print("  total docs in files collection: %d" % files_total)

    # Sample of the user_upload filenames tagged to the legacy user.
    sample = await files_collection.distinct(
        "filename", {"category": "user_upload", "userId": LEGACY_USER_ID}
    )
    names = sorted(n for n in sample if n)
    if names:
        print()
        print("  user_upload filenames for '%s' (first 10 of %d distinct):"
              % (LEGACY_USER_ID, len(names)))
        for name in names[:10]:
            print("      * %s" % name)

    # --- summary -----------------------------------------------------------
    print()
    print("=" * 60)
    print("A PURGE WOULD DELETE:")
    print("  conversations      : %d" % conv_count)
    print("  messages (by userId): %d" % msg_by_user)
    print("  user_upload files  : %d" % upload_default)
    print("PRESERVED (control):")
    print("  knowledge_base files: %d" % kb_count)
    print("=" * 60)
    print("READ-ONLY: nothing was modified.")


async def _main() -> None:
    try:
        await audit()
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(_main())
