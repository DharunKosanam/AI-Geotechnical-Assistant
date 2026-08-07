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


async def close_mongo_connection():
    """Close MongoDB connection"""
    mongo_client.close()
    print("MongoDB connection closed")

