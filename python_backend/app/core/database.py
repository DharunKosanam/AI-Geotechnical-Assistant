"""
Database connection and initialization
"""
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import MONGODB_URI

# Initialize MongoDB client
mongo_client = AsyncIOMotorClient(
    MONGODB_URI,
    maxPoolSize=50,
    minPoolSize=5,
    maxIdleTimeMS=30000,
    connectTimeoutMS=10000,
    serverSelectionTimeoutMS=10000
)

# Database and collections
db = mongo_client["ai-geotech-db"]
conversations_collection = db["conversations"]
files_collection = db["files"]
messages_collection = db["messages"]  # For persistent chat history
users_collection = db["users"]  # JWT email/password auth

# GeoPilot workspace History (Phase 3). Separate collections from the Chat tab
# (conversations/messages) so the two features never share state.
workspace_runs_collection = db["workspace_runs"]  # persisted calculator runs
workspace_threads_collection = db["workspace_threads"]  # persisted chat threads

# KB upload audit trail (Phase 6): a SEPARATE collection recording every upload
# and every deletion (endpoint or CLI), so removals are traceable independent of
# the KB data itself.
kb_audit_collection = db["kb_audit"]

# Message highlights + notes (HIGHLIGHTS_ENABLED). Separate collection keyed by
# (threadId, messageId); messages themselves are never touched.
highlights_collection = db["highlights"]

# GeoPilot instrument datasets (INSTRUMENT_PARSERS_ENABLED). Pointer documents
# ONLY (parser id, kind, metadata, shapes, file paths, warnings, job state) --
# the numeric arrays live on disk as .npz and are never written to Mongo.
# Separate from files/workspace_runs so the RAG and history data are untouched.
workspace_datasets_collection = db["workspace_datasets"]  # dataset artifacts
workspace_parse_jobs_collection = db["workspace_parse_jobs"]  # parse job state

# Lab inventory (INVENTORY_ENABLED). Operational lab records: equipment /
# consumables / software seats, loans, reservations, PLAXIS sessions, lab
# members, and a full mutation audit trail. Names and emails are stored AS-IS
# (no transformation) -- these are working custodial records, and answering
# "who has the interrogator?" requires real identity. Separate collections so
# the RAG/chat data is untouched.
inv_items_collection = db["inv_items"]      # equipment / consumable / software items
inv_tx_collection = db["inv_tx"]            # checkout / return / adjust / damage transactions
inv_res_collection = db["inv_res"]          # reservations
inv_plaxis_collection = db["inv_plaxis"]    # PLAXIS seat sessions
inv_users_collection = db["inv_users"]      # lab members (name/email/role/group)
inv_audit_collection = db["inv_audit"]      # one record per inventory mutation
inv_reminders_collection = db["inv_reminders"]  # (email, day) sends — idempotency for the daily digest


async def ensure_indexes():
    """Create required indexes. Idempotent -- safe to call on every startup.

    users.email gets a UNIQUE index so duplicate signups are rejected at the
    DB level (a second insert with the same email raises DuplicateKeyError).
    create_index is a no-op if the index already exists.

    files.threadId (sparse: only thread_upload docs carry a threadId) turns the
    per-turn thread lookups -- the document-inventory/fingerprint read on the
    THREAD_DOC cache path and the thread-scoped chunk retrieval -- from a
    25k-doc COLLSCAN into an index seek over one thread's ~100 docs.
    """
    await users_collection.create_index("email", unique=True, name="uniq_email")
    await files_collection.create_index("threadId", name="threadId_1", sparse=True)

    # Password reset (Phase 3): TTL + unique-hash indexes on the
    # password_reset_tokens collection. Imported inside the function because
    # password_reset_service imports THIS module at its top level -- a
    # module-level import here would be circular. By the time startup calls
    # ensure_indexes() both modules are fully initialized, so the deferred
    # import is safe.
    from app.services.password_reset_service import ensure_password_reset_indexes

    await ensure_password_reset_indexes()

    # Highlights (HIGHLIGHTS_ENABLED): index for the per-thread list and the
    # per-message lookups. Flag-gated so a flag-off deployment creates nothing.
    # Imported here (not at module top) so a flag-off process never even
    # evaluates it; config is already loaded by this point.
    from app.core import config as _config

    if _config.HIGHLIGHTS_ENABLED:
        await highlights_collection.create_index(
            [("threadId", 1), ("messageId", 1)], name="threadId_1_messageId_1"
        )

    # Instrument datasets (INSTRUMENT_PARSERS_ENABLED): per-user newest-first
    # listing + job lookups. Flag-gated: a flag-off deployment creates nothing.
    if _config.INSTRUMENT_PARSERS_ENABLED:
        await workspace_datasets_collection.create_index(
            [("user_id", 1), ("created_at", -1)], name="user_id_1_created_at_-1"
        )
        await workspace_parse_jobs_collection.create_index(
            [("dataset_id", 1)], name="dataset_id_1"
        )

    # Lab inventory (INVENTORY_ENABLED): unique item ids, per-item transaction
    # and reservation lookups (the feasibility walk), the open-loan scan
    # (actualReturn sparse: only returned loans carry the field), the
    # reservation window scan, and active PLAXIS sessions. Flag-gated: a
    # flag-off deployment creates nothing.
    if _config.INVENTORY_ENABLED:
        await inv_items_collection.create_index("id", unique=True, name="uniq_id")
        await inv_tx_collection.create_index("itemId", name="itemId_1")
        await inv_tx_collection.create_index(
            "actualReturn", name="actualReturn_1", sparse=True
        )
        await inv_res_collection.create_index("itemId", name="itemId_1")
        await inv_res_collection.create_index(
            [("start", 1), ("end", 1)], name="start_1_end_1"
        )
        await inv_plaxis_collection.create_index("loggedOut", name="loggedOut_1")
        # TOCTOU backstop for the seat-window gate: two truly concurrent
        # identical bookings can both pass the application-level
        # read-then-check, so HELD sessions (loggedOut: false — the UI always
        # writes the field) are additionally unique on (seat, start, end) at
        # the DB layer. The router translates the DuplicateKeyError into the
        # same 409 the gate produces. Partial: logged-out history rows are
        # exempt, so re-booking a window someone has released stays legal.
        # Verified buildable against the live data before adding (1 held
        # row, no duplicates, no rows missing loggedOut).
        await inv_plaxis_collection.create_index(
            [("seat", 1), ("start", 1), ("end", 1)],
            unique=True,
            partialFilterExpression={"loggedOut": False},
            name="uniq_held_seat_window",
        )
        await inv_reminders_collection.create_index(
            [("email", 1), ("day", 1)], unique=True, name="email_1_day_1"
        )


async def close_mongo_connection():
    """Close MongoDB connection"""
    mongo_client.close()
    print("MongoDB connection closed")

