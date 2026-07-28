"""KB upload validation (Phase 3).

Deterministic checks + duplicate detection + relevance, using the owner-approved
thresholds calibrated in scripts/kb_calibrate_validation.py. Split into small
pure functions (unit-tested) plus a couple of async helpers that touch Mongo; the
Phase 4 endpoint orchestrates them.

Policy (owner-confirmed):
  HARD reject : extraction-quality gate (chars/page + min content), size/page
                caps, EXACT normalized-text hash duplicate.
  SOFT flag   : near-duplicate (embedding cosine), off-topic (relevance), PII,
                non-English. These require the student to confirm, never block.
"""
import hashlib
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.core import config
from app.core.database import files_collection

Page = Tuple[int, str, bool]
KB = {"category": "knowledge_base"}


# ---------------------------------------------------------------------------
# Filename sanitisation
# ---------------------------------------------------------------------------
_UNSAFE = re.compile(r"[^A-Za-z0-9._ \-()]+")


def sanitize_filename(name: str) -> str:
    """Strip directory components and unsafe characters; collapse whitespace.

    Prevents path traversal and control/odd characters from reaching storage or
    a Content-Disposition header. Always returns a non-empty, bounded name.
    """
    base = os.path.basename(name or "").replace("\x00", "").strip()
    base = _UNSAFE.sub("_", base)
    base = re.sub(r"\s+", " ", base).strip(" .")
    return (base or "upload")[:200]


# ---------------------------------------------------------------------------
# Content hash (exact re-encode dedup + concurrency key)
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")


def normalized_text_hash(pages: List[Page]) -> str:
    """SHA-256 of the normalised extracted text (whitespace-collapsed, lowered).

    Binary-different re-encodes of the same document (re-saved PDF, different
    producer) that extract to the same text produce the SAME hash — catching
    duplicates that byte-level fingerprinting misses.
    """
    text = "\n".join(t for _, t, _ in pages)
    norm = _WS.sub(" ", text).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Hard gates: extraction quality + caps
# ---------------------------------------------------------------------------
def extraction_quality(pages: List[Page]) -> Tuple[bool, str]:
    """Reject probable scans / empty extractions. (ok, reason)."""
    if not pages:
        return False, "no extractable text was found"
    total = sum(len(t) for _, t, _ in pages)
    npages = max(1, len(pages))
    if total < config.KB_MIN_CONTENT_CHARS:
        return False, (f"only {total} characters extracted "
                       f"(minimum {config.KB_MIN_CONTENT_CHARS}) — the file looks empty or is a scan")
    cpp = total / npages
    if cpp < config.KB_MIN_CHARS_PER_PAGE:
        return False, (f"{cpp:.0f} characters per page "
                       f"(minimum {config.KB_MIN_CHARS_PER_PAGE}) — likely a scanned or image-only document")
    return True, ""


def check_size(nbytes: int) -> Tuple[bool, str]:
    if nbytes <= 0:
        return False, "file is empty"
    if nbytes > config.KB_MAX_UPLOAD_BYTES:
        return False, f"file is {nbytes / 1024 / 1024:.1f} MB (max {config.KB_MAX_UPLOAD_BYTES // 1024 // 1024} MB)"
    return True, ""


def check_pages(npages: int) -> Tuple[bool, str]:
    if npages > config.KB_MAX_PAGES:
        return False, f"{npages} pages (max {config.KB_MAX_PAGES})"
    return True, ""


# ---------------------------------------------------------------------------
# Soft flags: PII + language
# ---------------------------------------------------------------------------
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# North-American-style phone numbers; deliberately conservative to limit noise.
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)")
# Student identifiers, PRECISE so we don't flag DOIs / grant / ISBN numbers that
# fill academic papers: the UVic V-number, plus a bare 8-9 digit run ONLY when a
# student/SIN/ID keyword sits just before it.
_VNUMBER = re.compile(r"\b[Vv]\d{8}\b")
_CONTEXT_ID = re.compile(
    r"(?i)\b(?:student\s*(?:number|no\.?|id)|s\.?\s*i\.?\s*n\.?|social\s+insurance(?:\s+number)?|id\s*(?:number|no\.?))\b"
    r"[^0-9\n]{0,15}(\d{8,9})\b"
)

# Kinds that GATE an upload. Emails are public author-contact info in published
# papers — detected (`emails`) but NON-gating; see sensitive_pii().
SENSITIVE_PII_KINDS = ("student_numbers", "phones")


def scan_pii(text: str) -> Dict[str, List[str]]:
    """Regex scan for personal data. Returns {kind: [samples]} for anything found
    (capped). Empty dict == clean. `emails` is informational; the GATING subset is
    `sensitive_pii()` (student numbers + phones)."""
    found: Dict[str, List[str]] = {}
    emails = sorted(set(_EMAIL.findall(text)))
    phones = sorted(set(_PHONE.findall(text)))
    ids = sorted(set(_VNUMBER.findall(text)) | set(_CONTEXT_ID.findall(text)))
    if emails:
        found["emails"] = emails[:10]
    if phones:
        found["phones"] = phones[:10]
    if ids:
        found["student_numbers"] = ids[:10]
    return found


def sensitive_pii(found: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """The subset of scan_pii that GATES an upload — student numbers + phone
    numbers. Author-contact emails in published papers do not gate."""
    return {k: v for k, v in found.items() if k in SENSITIVE_PII_KINDS}


_ENGLISH_STOP = frozenset(
    "the and of to in is that for with as are was this by be on an at or from it".split()
)


def detect_non_english(text: str) -> Tuple[bool, str]:
    """Heuristic 'is this not English?' -> (is_non_english, note).

    Reuses the repo's non-Latin-script detector (catches CJK/Cyrillic/Arabic),
    then a light English-stopword-rate check for Latin-script non-English. It is
    a FLAG only, so a false positive just asks the student to confirm.
    """
    from app.services.llm_service import _needs_translation

    if _needs_translation(text):
        return True, "contains substantial non-Latin-script text"
    words = re.findall(r"[a-z]+", text.lower())
    if len(words) >= 50:
        rate = sum(1 for w in words if w in _ENGLISH_STOP) / len(words)
        if rate < 0.03:
            return True, "very few common English words — may not be English"
    return False, ""


# ---------------------------------------------------------------------------
# Cosine, KB centroid (cached), relevance
# ---------------------------------------------------------------------------
def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


_centroid_cache: Optional[List[float]] = None


async def get_kb_centroid(force: bool = False) -> Optional[List[float]]:
    """Mean of the KB first-chunk embeddings (matches how the relevance floor was
    calibrated). Cached in-process; pass force=True to recompute after a bulk KB
    change. Returns None if the KB has no embeddings yet."""
    global _centroid_cache
    if _centroid_cache is not None and not force:
        return _centroid_cache
    acc: Optional[List[float]] = None
    n = 0
    async for d in files_collection.find({**KB, "chunkIndex": 0}, {"embedding": 1}):
        e = d.get("embedding")
        if not e:
            continue
        if acc is None:
            acc = [0.0] * len(e)
        for i in range(len(e)):
            acc[i] += e[i]
        n += 1
    if not acc or n == 0:
        return None
    _centroid_cache = [x / n for x in acc]
    return _centroid_cache


def relevance(first_chunk_embedding: List[float], centroid: List[float]) -> Tuple[float, bool]:
    """(cosine_to_centroid, is_outlier). is_outlier when below the calibrated
    floor -> the endpoint flags it for confirmation."""
    c = cosine(first_chunk_embedding, centroid)
    return c, c < config.KB_RELEVANCE_MIN_COSINE


# ---------------------------------------------------------------------------
# Duplicate detection (exact hash hard-block + embedding soft-warn)
# ---------------------------------------------------------------------------
@dataclass
class DedupResult:
    exact_match: Optional[Dict[str, Any]] = None   # existing doc with identical content hash
    near_match: Optional[Dict[str, Any]] = None    # existing doc with cosine >= warn threshold
    near_cosine: float = 0.0


async def check_duplicate(content_hash: str, first_chunk_embedding: List[float]) -> DedupResult:
    """Exact normalized-text hash (hard) + nearest KB first-chunk by cosine (soft).

    The near-dup scan compares against the ~one-embedding-per-KB-file set
    (chunkIndex 0), which is small; for a much larger KB this would move to ANN.
    """
    exact = await files_collection.find_one(
        {**KB, "contentHash": content_hash},
        {"filename": 1, "canonicalTitle": 1, "batchId": 1, "uploaderName": 1},
    )
    best: Optional[Dict[str, Any]] = None
    best_cos = 0.0
    async for d in files_collection.find({**KB, "chunkIndex": 0}, {"embedding": 1, "filename": 1, "canonicalTitle": 1}):
        e = d.get("embedding")
        if not e:
            continue
        c = cosine(first_chunk_embedding, e)
        if c > best_cos:
            best_cos, best = c, d
    near = best if best_cos >= config.KB_DUP_WARN_COSINE else None
    return DedupResult(exact_match=exact, near_match=near, near_cosine=best_cos)


# ---------------------------------------------------------------------------
# Concurrency guard: two simultaneous uploads of the SAME file must not both pass
# ---------------------------------------------------------------------------
import threading

_inflight_hashes: set = set()
_inflight_lock = threading.Lock()


def reserve_hash(content_hash: str) -> bool:
    """Reserve a content hash for the duration of one upload. False when another
    upload of the same content is already in flight (single-worker guard; a
    multi-worker deploy would additionally need a unique-indexed registry doc)."""
    with _inflight_lock:
        if content_hash in _inflight_hashes:
            return False
        _inflight_hashes.add(content_hash)
        return True


def release_hash(content_hash: str) -> None:
    with _inflight_lock:
        _inflight_hashes.discard(content_hash)
