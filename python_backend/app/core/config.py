"""
Configuration management for the application
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is not set in environment variables")

ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID", "asst_mbSUwnJtQTwDYQYESHzeiHtM")
VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID")
KNOWLEDGE_STORE_ID = os.getenv("OPENAI_KNOWLEDGE_STORE_ID")

# MongoDB Configuration
MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise ValueError("MONGODB_URI is not set in environment variables")

# Application Constants
USER_ID = "default-user"  # Hardcoded user ID to match Next.js implementation

# CORS Origins
CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

