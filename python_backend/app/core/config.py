"""
Configuration management for the application
"""
import os
from urllib.parse import quote

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# LLM provider selection. "groq" (default) uses the hosted Groq API; "ollama"
# uses a local llama-index Ollama LLM. Both share the same llama-index
# interface, so everything downstream (.acomplete, RAG, citation filtering) is
# provider-agnostic — only LLM construction differs.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()

# Groq Configuration. The API key is only REQUIRED when Groq is the active
# provider; an Ollama-only deployment need not carry a Groq key, so we gate the
# check on LLM_PROVIDER instead of failing startup unconditionally.
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if LLM_PROVIDER == "groq" and not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is not set in environment variables")

# Model is env-overridable so we can swap without code changes. Llama 4 Scout
# is the default since it doesn't emit <think> tags and has much higher TPM
# headroom than qwen3-32b.
GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# Ollama Configuration (used when LLM_PROVIDER == "ollama"). Points at a local
# Ollama server; the model must already be pulled/served on that host.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")

# Ollama generation tuning — passed as the `options` dict on the raw
# ollama.AsyncClient.chat calls in llm_service.py.
#   num_ctx     - context window. Ollama's runtime DEFAULT is 4096, but a
#                 multi-turn RAG prompt (system + up to ~6000-token history +
#                 retrieved chunks) reaches ~7.3k tokens. At 4096 the input fills
#                 the window and leaves no output budget, so generation halts
#                 after ~1 word ("Based") yet still returns HTTP 200. qwen3.5:9b
#                 supports 262k, so we raise the window to hold the worst case.
#                 12288 (up from 8192): the ~7.3k-token worst-case prompt plus
#                 num_predict 2048 output left thin margin at 8192; 12288 gives
#                 comfortable headroom without spilling to CPU.
#   num_predict - upper bound on OUTPUT tokens so a long answer can't run away.
#                 2048 sits safely above the largest observed good answer
#                 (~4800 chars / ~1.3k tokens); 1024 would clip it.
#   temperature - override the model Modelfile default (1.0, too high for
#                 grounded RAG); 0.3 matches the Groq answer LLM.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "12288"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "2048"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.3"))

# Ollama request timeouts (seconds) — passed to the ollama.AsyncClient
# constructor (forwarded to the underlying httpx client) so a hung generation
# fails cleanly instead of holding a worker forever. Sized against the observed
# concurrency worst case: under 6 concurrent /chat requests Ollama serializes on
# the single MIG slice and the 6th answer call completed at ~148s. The chain is
# ordered to fail inside-out: Ollama (180s) < Next route (240s) < nginx (300s).
#   OLLAMA_REQUEST_TIMEOUT - ceiling for the ANSWER chat call. 180s = the ~148s
#       observed worst case + ~22% margin, so a legitimately queued request (or
#       the ~44.7s slow query landing last in the queue) still completes, while a
#       genuinely stuck generation is released instead of hanging indefinitely.
#   OLLAMA_REWRITE_TIMEOUT - ceiling for the tiny query-REWRITE chat call, which
#       should finish in seconds. A short 30s cap keeps the worst-case combined
#       path (rewrite 30s + answer 180s = 210s) comfortably under the 240s route
#       limit. On timeout the rewriter falls back to the raw query.
OLLAMA_REQUEST_TIMEOUT = float(os.getenv("OLLAMA_REQUEST_TIMEOUT", "180"))
OLLAMA_REWRITE_TIMEOUT = float(os.getenv("OLLAMA_REWRITE_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# Engineering Workspace (Phase 2) feature flag
# ---------------------------------------------------------------------------
# Master switch for the Engineering Workspace back end (CPT lane + AI
# Interpretation). Default OFF so the live chatbot deployment is completely
# unaffected until the workspace is deliberately turned on. Read at call time
# (via the config module) so it can be toggled in tests without re-import.
# Accepts 1/true/yes/on (case-insensitive).
WORKSPACE_ENABLED = os.getenv("WORKSPACE_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# ---------------------------------------------------------------------------
# Chat intent router (Phase: general-assistant) feature flag
# ---------------------------------------------------------------------------
# Master switch for the Chat-tab LLM intent router (KB_QUERY / GENERAL / MIXED /
# THREAD_DOC). Default OFF so the live chat path is byte-identical to the
# pre-router, always-retrieve behavior until deliberately enabled. Read at call
# time (via the config module, e.g. config.ROUTER_ENABLED) so it can be toggled
# in tests without re-import. Accepts 1/true/yes/on (case-insensitive).
ROUTER_ENABLED = os.getenv("ROUTER_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# ---------------------------------------------------------------------------
# Response streaming (Phase 2) feature flag
# ---------------------------------------------------------------------------
# Master switch for POST /chat/stream, the SSE variant of the chat turn. Default
# OFF: with the flag off the endpoint 404s and NOTHING about POST /chat changes —
# it remains the only chat path, byte-identical to before. Read at call time (via
# the config module, e.g. config.STREAMING_ENABLED) so it can be toggled in tests
# without re-import. Accepts 1/true/yes/on (case-insensitive).
STREAMING_ENABLED = os.getenv("STREAMING_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

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

# Redis connection URI assembled from the REDIS_* settings above (SAME server,
# no new Redis config). slowapi's limits storage uses this so rate-limit
# counters live in Redis -- they survive restarts and are shared across workers.
# Override with REDIS_URL in .env if your Redis needs TLS (use rediss://...).
REDIS_URL = os.getenv(
    "REDIS_URL",
    f"redis://{quote(REDIS_USER)}:{quote(REDIS_PASSWORD)}@{REDIS_HOST}:{REDIS_PORT}",
)

# Rate limits, slowapi "<count>/<period>" syntax. Env-overridable for tuning.
#   login : tight brute-force protection, keyed by client IP (no user yet)
#   chat  : generous for normal use, keyed by authenticated user id
#   upload: keyed by authenticated user id
RATE_LIMIT_LOGIN = os.getenv("RATE_LIMIT_LOGIN", "5/minute")
RATE_LIMIT_CHAT = os.getenv("RATE_LIMIT_CHAT", "20/minute")
RATE_LIMIT_UPLOAD = os.getenv("RATE_LIMIT_UPLOAD", "10/minute")
# Per-user HOURLY upload cap (Phase 0.5), stacked on top of the per-minute limit
# so a single user cannot queue a large backlog over an hour. Applies to
# /api/upload (thread + user uploads) and the KB upload endpoint.
RATE_LIMIT_UPLOAD_HOURLY = os.getenv("RATE_LIMIT_UPLOAD_HOURLY", "60/hour")

# Application Constants
USER_ID = "default-user"  # Hardcoded user ID to match Next.js implementation

# ---------------------------------------------------------------------------
# JWT Authentication
# ---------------------------------------------------------------------------
# Secret used to sign access tokens. No default on purpose -- it MUST be set in
# .env. Generate one with:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    raise ValueError("JWT_SECRET_KEY is not set in environment variables")

# Signing algorithm and token lifetime. Env-overridable, with sane defaults.
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", "7"))

# Secure attribute for the access_token cookie set at login.
#   * Production (HTTPS): set COOKIE_SECURE=True so the cookie is transmitted
#     ONLY over TLS and never leaks over plain http.
#   * Local dev (http://localhost): leave it False. A Secure cookie is NOT sent
#     over http, so the browser would silently drop it -- login would appear to
#     succeed but every following request would be unauthenticated.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "False").lower() == "true"

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

# Absolute cross-encoder score floor applied AFTER reranking (and after the
# top-K cap). ms-marco-MiniLM scores go negative for "not relevant", so chunks
# below this are dropped from the displayed sources — they are retrieval noise.
# Tune here without touching the pipeline code.
RERANK_SCORE_THRESHOLD = float(os.getenv("RERANK_SCORE_THRESHOLD", "0.0"))

# SEPARATE, permissive threshold for THREAD-SCOPED retrieval (THREAD_DOC mode).
# The KB threshold above (0.0) is calibrated to filter noise out of a 16,811-chunk
# corpus, where relevant chunks score +3 to +6. Thread-scoped retrieval has a
# candidate set of exactly ONE user-uploaded document, so aggressive filtering
# serves no purpose -- and actively harms: the ms-marco cross-encoder scores
# generic/meta questions ("what did it find?", "summarize this document") against
# a short single-chunk doc near its floor (~-11), indistinguishable from an
# off-topic query, so the KB threshold wrongly drops on-target chunks and the
# THREAD_DOC answer falls through to the "not found in your document" fallback
# even though the answer is present. This much lower default keeps the thread's
# own chunk for on-target questions while still letting the most clearly
# off-topic questions (which score at the very floor) fall through. Used ONLY by
# query_thread_documents; the KB path is unchanged. Tune via env.
THREAD_RERANK_SCORE_THRESHOLD = float(os.getenv("THREAD_RERANK_SCORE_THRESHOLD", "-11.0"))

# Per-document representation quotas for THREAD_DOC retrieval (Phase 3).
# Incident thread_7162ec5f...: with two documents attached, the older 40-chunk
# paper took 19 of the 25 cosine candidate slots and ALL 5 post-rerank context
# slots; the newer 56-chunk paper's best chunk sat at global rerank rank 7,
# 0.07 below the cutoff, so the answer cited the wrong file. These quotas
# guarantee every document in the thread is CONSIDERED at both stages -- the
# candidate cap and the context cap -- while the rerank threshold above still
# decides INCLUSION (a document whose best chunk fails -11.0 contributes
# nothing). Used ONLY by query_thread_documents; KB retrieval is unchanged.
#
# THREAD_DOC_MIN_CANDIDATES_PER_DOC: each document's top-N cosine chunks are
# reserved in the COMBINED_SEARCH_LIMIT candidate set before the remaining
# slots fill by global score order, so the reranker always SEES every document.
# Known tuning consideration: at the default of 5, FIVE documents saturate the
# 25-slot COMBINED_SEARCH_LIMIT (5 docs x 5 reserved = 25), at which point
# global score order no longer contributes any slots and the round-robin
# reservation alone decides the candidate set; beyond five documents each doc's
# reserved share shrinks below 5. Revisit this default (or raise
# COMBINED_SEARCH_LIMIT) if threads routinely hold more than four documents.
THREAD_DOC_MIN_CANDIDATES_PER_DOC = int(os.getenv("THREAD_DOC_MIN_CANDIDATES_PER_DOC", "5"))
# THREAD_DOC_MIN_CHUNKS_PER_DOC: each document with at least one chunk clearing
# the threshold holds this many slots in the final RERANK_TOP_K context; the
# rest fill by global rerank order. When a thread holds more documents than
# context slots, slots fill one per document in best-score order instead.
THREAD_DOC_MIN_CHUNKS_PER_DOC = int(os.getenv("THREAD_DOC_MIN_CHUNKS_PER_DOC", "1"))

# Context budget for DOCUMENT-LEVEL thread requests (summarize / what is this
# about). These bypass relevance ranking -- a summary query shares no content
# terms with any chunk, so the cross-encoder floors every chunk below -11.0 and
# the turn used to fall through to the "not found in your document" fallback.
# Instead a structured sample is read: each document's opening chunks plus an
# even spread across the remainder, split across documents per the Phase 3
# quota spirit. 8 chunks x ~1200 chars ~= 2.4k tokens, inside the num_ctx
# headroom alongside the worst-case prompt.
THREAD_DOC_SAMPLE_CHUNKS = int(os.getenv("THREAD_DOC_SAMPLE_CHUNKS", "8"))

# ---------------------------------------------------------------------------
# Source-grounded output formats (additive, flag-gated -- default OFF)
# ---------------------------------------------------------------------------
# Feature A: generate structured documents (study guide, briefing doc, FAQ,
# timeline, key-terms glossary) from EVERY chunk of a thread's ready documents.
# Engine selection is automatic on measured prompt size:
#   - single call: the whole document set in one generation, with num_ctx sized
#     per request (options are per-request in Ollama, so neither the chat path
#     nor the KB path ever sees a different context size). Measured on the
#     deployment GPU (H100L 12GB MIG, gemma4:12b Q4_K_M): fully GPU-resident to
#     ~96k ctx, spills to CPU at >=131k; prefill ~715 tok/s at 10k falling to
#     ~590 tok/s at 36k; generation ~24 tok/s.
#   - map-reduce: above SOURCE_FORMATS_SINGLE_CALL_MAX_TOKENS the set is
#     summarized in reading-order batches (map), then the notes are synthesized
#     (reduce), hierarchically if the notes themselves overflow one call.
# 90k keeps the single call safely under the measured 96k spill point with the
# output budget and estimator error inside the margin.
SOURCE_FORMATS_ENABLED = os.getenv("SOURCE_FORMATS_ENABLED", "false").lower() == "true"
SOURCE_FORMATS_SINGLE_CALL_MAX_TOKENS = int(
    os.getenv("SOURCE_FORMATS_SINGLE_CALL_MAX_TOKENS", "90000")
)
# Map batch size in chunks (~9k prompt tokens at the measured ~424 tok/chunk,
# comfortably inside the default num_ctx with the 640-token note budget), and
# the note budget itself (512 truncated notes mid-sentence in benchmarking).
SOURCE_FORMATS_MAP_BATCH_CHUNKS = int(os.getenv("SOURCE_FORMATS_MAP_BATCH_CHUNKS", "20"))
SOURCE_FORMATS_MAP_NUM_PREDICT = int(os.getenv("SOURCE_FORMATS_MAP_NUM_PREDICT", "640"))
# Per-Ollama-call ceiling for format generation. A near-ceiling single call is
# ~153s prefill + ~85s generation, over the 180s chat-call timeout, so format
# calls carry their own. The SSE transport has no total deadline (heartbeats
# reset every proxy read timeout), so this bounds a HUNG call, not a slow one.
SOURCE_FORMATS_TIMEOUT = float(os.getenv("SOURCE_FORMATS_TIMEOUT", "420"))

# ---------------------------------------------------------------------------
# Named persistent source sets (additive, flag-gated -- default OFF)
# ---------------------------------------------------------------------------
# Feature B: the thread-as-source-set surfaces -- the sources view (per-doc
# status, chunk count, provenance) and per-source removal with a mandatory
# user-visible dry-run. Gates ONLY those new endpoints and their UI; the
# rename hardening on PUT /api/assistants/threads/history is a fix to a live
# path and ships unflagged.
SOURCE_SETS_ENABLED = os.getenv("SOURCE_SETS_ENABLED", "false").lower() == "true"

# Ingestion staleness rule (Phase 1). A thread-upload parent doc stuck in
# status "processing" longer than this is REPORTED as failed with a timeout
# reason wherever status is read (upload-status endpoint, document inventory,
# scope note). Background ingestion does not survive a backend restart, so a
# crash mid-ingest would otherwise leave the doc "processing" forever with no
# terminal state. Derived at read time -- nothing writes to the doc. The
# default comfortably exceeds the largest observed ingest (~28s for a 120-page
# scan) while catching a dead ingestion within minutes.
INGEST_PENDING_TIMEOUT_SECONDS = int(os.getenv("INGEST_PENDING_TIMEOUT_SECONDS", "600"))

# When every reranked chunk falls below RERANK_SCORE_THRESHOLD we still hand the
# LLM this many top chunks as low-confidence context (so it can attempt an
# answer); these are NOT shown as sources.
LOW_CONF_CONTEXT_CHUNKS = int(os.getenv("LOW_CONF_CONTEXT_CHUNKS", "2"))

# Single combined candidate pool size. KB chunks and the current user's uploads
# compete in ONE vector search ranked purely by similarity (no per-category
# slots, no user-upload prioritization) before reranking. This replaces the old
# split PRE_RERANK_POOL_USER (15) + PRE_RERANK_POOL_KB (10) pools. Held at 25 to
# keep reranker cost constant vs the old 15+10 budget.
COMBINED_SEARCH_LIMIT = int(os.getenv("COMBINED_SEARCH_LIMIT", "25"))

# ---------------------------------------------------------------------------
# BM25 hybrid search (additive, flag-gated — default OFF)
# ---------------------------------------------------------------------------
# When enabled, retrieval runs the existing $vectorSearch AND a new Atlas
# $search (Lucene BM25) over the chunk `text` field IN PARALLEL, then fuses the
# two ranked lists with Reciprocal Rank Fusion (RRF) before the fused pool is
# handed to the EXISTING cross-encoder reranker (unchanged). Default False so
# current behavior is byte-for-byte unchanged until deliberately flipped.
HYBRID_SEARCH_ENABLED = os.getenv("HYBRID_SEARCH_ENABLED", "false").lower() == "true"

# RRF constant. rrf_score = sum over each list of 1/(RRF_K + rank). Larger K
# flattens the contribution of top ranks; 60 is the value from the original
# RRF paper and the Atlas hybrid-search reference examples.
RRF_K = int(os.getenv("RRF_K", "60"))

# Candidates pulled from EACH search (vector and BM25) before the RRF merge.
# The fused, deduped pool is then trimmed to COMBINED_SEARCH_LIMIT for the
# reranker, keeping reranker cost unchanged.
HYBRID_POOL = int(os.getenv("HYBRID_POOL", "20"))

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

# ---------------------------------------------------------------------------
# Vision extraction for scanned PDF pages (feature flag, default OFF)
# ---------------------------------------------------------------------------
# Master switch. When ON, pages that the normal PDF extraction identified as
# having NO readable text layer are rasterized with PyMuPDF and transcribed by
# the vision-capable Ollama model (one page per call, background ingest path
# only). The output is model-generated INTERPRETATION, never verbatim document
# text: every chunk it produces carries visionDerived=True plus the model name,
# and that provenance surfaces in citations and the THREAD_DOC scope note.
# Default OFF so ingestion is byte-identical until deliberately enabled. Read
# at call time (config.VISION_EXTRACTION_ENABLED) so tests can toggle it.
VISION_EXTRACTION_ENABLED = os.getenv(
    "VISION_EXTRACTION_ENABLED", "false"
).strip().lower() in ("1", "true", "yes", "on")

# Vision model. Defaults to the active OLLAMA_MODEL (gemma4:12b already reports
# the vision capability, so no second model and no extra VRAM); override only
# to point vision at a different already-pulled model.
VISION_MODEL = os.getenv("VISION_MODEL", "") or OLLAMA_MODEL

# Rasterization DPI for the page image sent to the vision model. Conservative
# default: image tokens are expensive against OLLAMA_NUM_CTX, and the CLIP
# projector downsamples anyway, so higher DPI mostly costs encode time.
VISION_DPI = int(os.getenv("VISION_DPI", "150"))

# Hard cap on vision-transcribed pages PER DOCUMENT. A 200-page scan must not
# occupy an ingest worker indefinitely (one page = one full vision call); pages
# beyond the cap stay unindexed and are reported as such in the chip warning.
VISION_MAX_PAGES_PER_DOC = int(os.getenv("VISION_MAX_PAGES_PER_DOC", "20"))

# Ceiling for a single per-page vision call (seconds). On expiry the PAGE is
# recorded as failed and ingest moves on -- one slow page never fails the
# document.
VISION_TIMEOUT_SECONDS = float(os.getenv("VISION_TIMEOUT_SECONDS", "120"))

# Longest edge (pixels) for a DIRECTLY UPLOADED image sent to the vision model.
# Larger photos are downscaled to this before the call -- never rejected for
# size (the 50 MB upload cap is the only size gate). Latency is generation-
# bound, not resolution-bound, so this mostly caps decode/transfer cost.
VISION_IMAGE_MAX_DIM = int(os.getenv("VISION_IMAGE_MAX_DIM", "1600"))

# ---------------------------------------------------------------------------
# Ingestion concurrency (Phase 0 — off the event loop)
# ---------------------------------------------------------------------------
# Document ingestion (extract -> chunk -> embed) is CPU-bound. It used to run on
# the single uvicorn event loop, so a large PDF blocked EVERY concurrent request
# for the duration (a 220-page file froze the server ~42s and its own upload POST
# didn't return for ~42s). The compute now runs in a dedicated, bounded thread
# pool so the loop stays responsive; motor DB writes stay on the loop. The heavy
# steps release the GIL (fastembed/ONNX, the tesseract subprocess, PyMuPDF), so
# workers run in parallel with the loop. Keep the pool SMALL: each worker holds a
# batch of embeddings in RAM and competes for CPU with query-time embedding.
INGEST_WORKERS = int(os.getenv("INGEST_WORKERS", "2"))

# Independent rollback switch for the offload above (NOT the KB-upload feature
# flag). Default ON -- the point is to fix a live stall. Set false to restore the
# exact pre-Phase-0 behaviour (compute inline on the loop) via env, no redeploy.
# Read at call time so tests can toggle it.
INGEST_OFFLOAD_ENABLED = os.getenv("INGEST_OFFLOAD_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Max ingests queued or running at once in this process (Phase 0.5 queue-depth
# cap). Each pending upload holds its file bytes in RAM until processed, so an
# unbounded backlog under a burst is a memory/CPU exhaustion risk. Uploads beyond
# this are rejected with 503 (retry shortly). Applies to every upload path.
INGEST_MAX_QUEUE = int(os.getenv("INGEST_MAX_QUEUE", "10"))

# ---------------------------------------------------------------------------
# KB upload feature flag + validation thresholds (Phase 3)
# ---------------------------------------------------------------------------
# Master switch for the student KB-upload feature (endpoint + UI). Default OFF so
# the deployment is byte-identical until deliberately enabled. Read at call time.
KB_UPLOAD_ENABLED = os.getenv("KB_UPLOAD_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Validation thresholds, CALIBRATED 2026-07-24 against the 16,811-chunk KB via
# scripts/kb_calibrate_validation.py (owner-approved). All env-tunable.
#   Extraction quality gate — reject probable scans / empty extractions. KB good
#   docs ran 758..13550 chars/page (one degenerate at 47); off/scan ~0.
KB_MIN_CHARS_PER_PAGE = int(os.getenv("KB_MIN_CHARS_PER_PAGE", "150"))
KB_MIN_CONTENT_CHARS = int(os.getenv("KB_MIN_CONTENT_CHARS", "200"))
#   Hard caps.
KB_MAX_PAGES = int(os.getenv("KB_MAX_PAGES", "1500"))
KB_MAX_UPLOAD_BYTES = int(os.getenv("KB_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
#   Duplicate detection — first-chunk cosine >= this WARNS (soft; the student is
#   shown the match and may proceed). Distinct KB docs topped out at p99.9=0.897;
#   true near-dups score >=0.997.
KB_DUP_WARN_COSINE = float(os.getenv("KB_DUP_WARN_COSINE", "0.95"))
#   Relevance — first-chunk cosine to the KB centroid BELOW this flags an outlier
#   (flag + confirm, never block). KB docs >= 0.678; off-topic samples <= 0.515.
KB_RELEVANCE_MIN_COSINE = float(os.getenv("KB_RELEVANCE_MIN_COSINE", "0.62"))
# Window during which a student may delete their OWN KB upload (Phase 5). After
# this, removal is admin-only (Phase 6).
KB_SELF_DELETE_WINDOW_HOURS = int(os.getenv("KB_SELF_DELETE_WINDOW_HOURS", "24"))

# Bulk (multi-file) KB upload. Max files per submitted batch, and the pause
# between files in the sequential background worker so one student's batch can't
# monopolise the ingest pool / starve chat + Ollama.
KB_BULK_MAX_FILES = int(os.getenv("KB_BULK_MAX_FILES", "50"))
KB_BULK_PACING_SECONDS = float(os.getenv("KB_BULK_PACING_SECONDS", "0.5"))

# CORS Origins. NOTE: no "*" wildcard here. The frontend sends credentials (the
# httpOnly access_token cookie), and the CORS spec forbids pairing
# Access-Control-Allow-Credentials: true with a "*" origin -- browsers reject
# it. So every allowed origin must be listed explicitly. Add the production UVic
# HTTPS origin to this list when deploying.
CORS_ORIGINS = [
    "https://ai-geotechnical-assistant-production.up.railway.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

