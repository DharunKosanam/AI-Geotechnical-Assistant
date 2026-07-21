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


async def ensure_indexes():
    """Create required indexes. Idempotent -- safe to call on every startup.

    users.email gets a UNIQUE index so duplicate signups are rejected at the
    DB level (a second insert with the same email raises DuplicateKeyError).
    create_index is a no-op if the index already exists.
    """
    await users_collection.create_index("email", unique=True, name="uniq_email")


async def close_mongo_connection():
    """Close MongoDB connection"""
    mongo_client.close()
    print("MongoDB connection closed")

