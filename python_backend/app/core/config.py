"""
Configuration management for the application
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Groq Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is not set in environment variables")

# Model is env-overridable so we can swap without code changes. Llama 4 Scout
# is the default since it doesn't emit <think> tags and has much higher TPM
# headroom than qwen3-32b.
GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# MongoDB Configuration
MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise ValueError("MONGODB_URI is not set in environment variables")

# Redis Configuration
REDIS_HOST = os.getenv("REDIS_HOST")
if not REDIS_HOST:
    raise ValueError("REDIS_HOST is not set in environment variables")

REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
if not REDIS_PORT:
    raise ValueError("REDIS_PORT is not set in environment variables")

REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
if not REDIS_PASSWORD:
    raise ValueError("REDIS_PASSWORD is not set in environment variables")

REDIS_USER = os.getenv("REDIS_USER", "default")

# Application Constants
USER_ID = "default-user"  # Hardcoded user ID to match Next.js implementation

# ---------------------------------------------------------------------------
# Chunking & Retrieval (v2)
# ---------------------------------------------------------------------------
# CHUNKING_VERSION tags new chunks so old (500-char v1) chunks can coexist.
# Old chunks have no chunkingVersion field; new chunks get this value.
CHUNKING_VERSION = os.getenv("CHUNKING_VERSION", "v2")

# Structure-aware chunker sizing (characters)
CHUNK_TARGET_SIZE = int(os.getenv("CHUNK_TARGET_SIZE", "1200"))
CHUNK_MAX_SIZE = int(os.getenv("CHUNK_MAX_SIZE", "1500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))

# Cross-encoder reranking (after vector search, before LLM prompt assembly)
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "true").lower() == "true"
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "5"))

# Pre-rerank candidate pool sizes (per category). With reranker disabled,
# the legacy 5/3 limits are used instead.
PRE_RERANK_POOL_USER = int(os.getenv("PRE_RERANK_POOL_USER", "15"))
PRE_RERANK_POOL_KB = int(os.getenv("PRE_RERANK_POOL_KB", "10"))

# Folder of source PDFs used by the kb_admin CLI for reindex/add operations.
# Required for reindex when chunks don't carry the original PDF binary.
KNOWLEDGE_BASE_PDF_PATH = os.getenv("KNOWLEDGE_BASE_PDF_PATH", "")

# ---------------------------------------------------------------------------
# OCR Configuration (Tesseract)
# ---------------------------------------------------------------------------
# Master switch — when False, image files are skipped and PDF pages with
# little extractable text are not OCR'd.
OCR_ENABLED = os.getenv("OCR_ENABLED", "true").lower() == "true"

# Path to the tesseract executable. On Windows this is typically
# "C:/Program Files/Tesseract-OCR/tesseract.exe". Leave empty on Linux/macOS
# when tesseract is on PATH.
TESSERACT_PATH = os.getenv("TESSERACT_PATH", "")

# A page (or OCR result) shorter than this is treated as "no usable text"
# — for PDFs, that triggers the OCR fallback; for images, it triggers a warn+skip.
OCR_MIN_TEXT_LEN = int(os.getenv("OCR_MIN_TEXT_LEN", "50"))

# Embedded PDF images smaller than this (width OR height) are skipped when
# OCRing figures/diagrams — tiny images are usually icons/decorations.
PDF_IMAGE_OCR_MIN_DIM = int(os.getenv("PDF_IMAGE_OCR_MIN_DIM", "200"))

# CORS Origins
CORS_ORIGINS = [
    "https://ai-geotechnical-assistant-production.up.railway.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "*",
]

