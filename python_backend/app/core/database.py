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


async def close_mongo_connection():
    """Close MongoDB connection"""
    mongo_client.close()
    print("MongoDB connection closed")

