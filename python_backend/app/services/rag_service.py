"""
RAG (Retrieval-Augmented Generation) service for querying the vector store.

CHUNKING (v2 — structure-aware recursive):
  * Page-aware PDF extraction; chunks carry page_start + section_header metadata.
  * Recursive split priority: section headers > paragraph (\\n\\n) > line >
    sentence > word > hard cut. Greedy-packed toward CHUNK_TARGET_SIZE,
    capped at CHUNK_MAX_SIZE, with CHUNK_OVERLAP char tail-prefix between
    adjacent chunks.
  * Old v1 chunks (500-char fixed, no metadata) still work — they live in the
    same 384-dim embedding space. New chunks are tagged chunkingVersion='v2'.

RETRIEVAL:
  * Two-stage Atlas vector search (user_upload then knowledge_base) — pool
    sizes widen when the reranker is on so it has enough candidates to work with.
  * Optional cross-encoder rerank (fastembed TextCrossEncoder). When enabled,
    user + KB candidates are reranked TOGETHER on pure query relevance — the
    explicit "user uploads first" preference is dropped in this mode by design.
  * When disabled, falls back to the original 5/3 limits + score-floor +
    user-first ordering.
"""
import re
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import quote
import gc
from datetime import datetime

import fitz  # PyMuPDF
from fastembed import TextEmbedding

from app.core.database import files_collection
from app.core import config  # module import for call-time flags (INGEST_OFFLOAD_ENABLED)
from app.core.config import (
    USER_ID,
    CHUNKING_VERSION,
    CHUNK_TARGET_SIZE,
    CHUNK_MAX_SIZE,
    CHUNK_OVERLAP,
    RERANKER_ENABLED,
    RERANKER_MODEL,
    RERANK_TOP_K,
    RERANK_SCORE_THRESHOLD,
    THREAD_RERANK_SCORE_THRESHOLD,
    THREAD_DOC_MIN_CANDIDATES_PER_DOC,
    THREAD_DOC_MIN_CHUNKS_PER_DOC,
    LOW_CONF_CONTEXT_CHUNKS,
    COMBINED_SEARCH_LIMIT,
    HYBRID_SEARCH_ENABLED,
    RRF_K,
    HYBRID_POOL,
    OCR_ENABLED,
    OCR_MIN_TEXT_LEN,
    PDF_IMAGE_OCR_MIN_DIM,
)


# ---------------------------------------------------------------------------
# Academic citation title mapping
# ---------------------------------------------------------------------------
FILENAME_TO_TITLE: Dict[str, str] = {
    "StrengthanddilatancyofsandsBolton1986discussion1987.pdf":
        "Bolton (1986) - Strength and Dilatancy of Sands",
    "Bonelli (2013) Book-Erosion in geomechanics ap.pdf":
        "Bonelli (2013) - Erosion in Geomechanics",
    "Applications of enzyme induced carbonate precipitation (EICP) for soil_2015_Hamdan.pdf":
        "Hamdan (2015) - Applications of EICP for Soil Improvement",
    "Critical-State-Of-Soil-Mechanics-Schofield-Wroth.pdf":
        "Schofield & Wroth - Critical State Soil Mechanics",
    "Scour effects on the response of laterally loaded piles considering stress-Chenglin.pdf":
        "Chenglin et al. - Scour Effects on Laterally Loaded Piles",
}


def get_clean_title(filename: str) -> Dict[str, str]:
    """
    Convert a raw PDF filename into an academic citation title and a
    Google Scholar search URL so students can find the original paper.
    """
    if filename in FILENAME_TO_TITLE:
        title = FILENAME_TO_TITLE[filename]
    else:
        name = filename.rsplit(".", 1)[0] if "." in filename else filename
        name = name.replace("_", " ").replace("-", " ")
        title = " ".join(name.split())

    url = f"https://scholar.google.com/scholar?q={quote(title)}"
    return {"title": title, "url": url}


# ---------------------------------------------------------------------------
# Lazy-loaded models (embedding + reranker)
# ---------------------------------------------------------------------------
_embedding_model = None
_embedding_model_lock = threading.Lock()
_reranker_model = None


def get_embedding_model():
    """Get or initialize the embedding model (lazy loading, thread-safe)."""
    global _embedding_model
    if _embedding_model is None:
        # Ingestion now runs in a worker-thread pool, so several threads (plus the
        # loop's query path) can reach this on a cold start. Double-checked lock
        # -> the model loads exactly once.
        with _embedding_model_lock:
            if _embedding_model is None:
                print("[LOADING] Initializing embedding model (BAAI/bge-small-en-v1.5)...")
                _embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
                print("[OK] Embedding model loaded successfully")
    return _embedding_model


def get_reranker():
    """Get or initialize the cross-encoder reranker (lazy loading)."""
    global _reranker_model
    if _reranker_model is None:
        # Import here so disabling the reranker via env doesn't pay this import cost
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        print(f"[LOADING] Initializing reranker ({RERANKER_MODEL})...")
        _reranker_model = TextCrossEncoder(model_name=RERANKER_MODEL)
        print("[OK] Reranker loaded successfully")
    return _reranker_model


# ---------------------------------------------------------------------------
# PDF extraction — page-aware
# ---------------------------------------------------------------------------
def extract_pages_from_pdf(file_content: bytes) -> List[Tuple[int, str]]:
    """
    Extract text from a PDF as a list of (page_number, page_text).
    Page numbers are 1-indexed. Empty/image-only pages are skipped.
    Uses a per-page try/except and a re-open retry for pages that error.
    """
    doc = None
    try:
        doc = fitz.open(stream=file_content, filetype="pdf")
        total_pages = len(doc)
        pages: List[Tuple[int, str]] = []
        empty_pages: List[int] = []
        failed_pages: List[int] = []

        for i in range(total_pages):
            try:
                page_text = doc[i].get_text()
                if page_text and page_text.strip():
                    pages.append((i + 1, page_text))
                else:
                    empty_pages.append(i + 1)
                    print(f"      [WARNING] Page {i + 1} is empty or image-based")
            except Exception as page_err:
                failed_pages.append(i + 1)
                print(f"      [ERROR] Failed to extract page {i + 1}: {page_err}")

        doc.close()
        doc = None

        # Re-open retry for any pages that errored
        if failed_pages:
            print(f"      [RETRY] Re-opening PDF to retry {len(failed_pages)} failed pages...")
            try:
                retry_doc = fitz.open(stream=file_content, filetype="pdf")
                for pn in failed_pages:
                    try:
                        page_text = retry_doc[pn - 1].get_text()
                        if page_text and page_text.strip():
                            pages.append((pn, page_text))
                            print(f"      [OK] Retry succeeded for page {pn}")
                        else:
                            empty_pages.append(pn)
                    except Exception as retry_err:
                        print(f"      [ERROR] Retry also failed for page {pn}: {retry_err}")
                retry_doc.close()
            except Exception as reopen_err:
                print(f"      [ERROR] Could not re-open PDF for retry: {reopen_err}")
            pages.sort(key=lambda p: p[0])  # keep page order after retry insertions

        if empty_pages:
            if len(empty_pages) >= total_pages:
                print(f"      [ERROR] All {total_pages} pages are empty or image-based!")
            else:
                print(f"      [INFO] {len(empty_pages)}/{total_pages} pages empty: {empty_pages[:10]}")

        return pages
    except Exception as e:
        print(f"[ERROR] Error extracting text from PDF with PyMuPDF: {e}")
        raise ValueError(f"Failed to extract text from PDF: {str(e)}")
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def extract_text_from_pdf(file_content: bytes) -> str:
    """
    BACK-COMPAT: returns the concatenated text of all pages.
    Used by files.py::extract_text_from_file for the legacy single-document
    upload path that doesn't carry page metadata.
    """
    return "\n".join(text for _, text in extract_pages_from_pdf(file_content))


# ---------------------------------------------------------------------------
# PDF extraction WITH OCR fallback — used by the multi-format ingest pipeline
# ---------------------------------------------------------------------------
def _ocr_pdf_page(page) -> str:
    """Render a PDF page to a PNG bytes blob and OCR it."""
    from app.services.file_processing import ocr_image_bytes, tesseract_available
    if not tesseract_available():
        return ""
    try:
        # 2x zoom — better OCR accuracy than the default 72dpi render
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        return ocr_image_bytes(png_bytes)
    except Exception as e:
        print(f"      [OCR ERROR] Page render/OCR failed: {e}")
        return ""


def _ocr_embedded_images(doc, page, min_dim: int) -> str:
    """OCR every embedded image on a page that meets the min-dim threshold."""
    from app.services.file_processing import ocr_image_bytes, tesseract_available
    if not tesseract_available():
        return ""
    pieces: List[str] = []
    try:
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base = doc.extract_image(xref)
                width = base.get("width", 0)
                height = base.get("height", 0)
                if width < min_dim or height < min_dim:
                    continue
                img_bytes = base.get("image", b"")
                if not img_bytes:
                    continue
                text = ocr_image_bytes(img_bytes).strip()
                if text:
                    pieces.append(text)
            except Exception as inner:
                print(f"      [OCR ERROR] Embedded image OCR failed: {inner}")
    except Exception as e:
        print(f"      [OCR ERROR] Listing embedded images failed: {e}")
    return "\n".join(pieces)


def _pdf_page_count(file_content: bytes) -> int:
    """Page count only — fitz parses the xref lazily, so this is cheap."""
    doc = None
    try:
        doc = fitz.open(stream=file_content, filetype="pdf")
        return len(doc)
    except Exception:
        return 0
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def _no_text_layer_error(filename: str, total_pages: int) -> "Exception":
    """The user-facing explanation for a PDF whose pages carry no text."""
    from app.services.file_processing import UnreadableDocumentError, tesseract_available

    if tesseract_available():
        return UnreadableDocumentError(
            f"{filename} has no readable text layer and OCR could not read its "
            f"{total_pages} page(s). Please upload a text-based PDF."
        )
    return UnreadableDocumentError(
        f"{filename} looks like a scanned PDF: all {total_pages} page(s) are images "
        f"with no readable text layer, and this server cannot run OCR. Please upload "
        f"a text-based PDF, or re-export it as a searchable PDF first."
    )


def extract_pages_from_pdf_with_ocr(
    file_content: bytes,
    filename: str = "This PDF",
    stats: Optional[Dict[str, Any]] = None,
) -> List[Tuple[int, str, bool]]:
    """
    Page-aware PDF extraction with OCR fallback.

    For each page:
      1. PyMuPDF text layer (fast path).
      2. If that yields < OCR_MIN_TEXT_LEN chars AND OCR is actually usable,
         render the page and OCR it.
      3. Independently, OCR any large embedded images and append.

    Returns (page_number, text, ocr_extracted). ``ocr_extracted`` is True
    when any OCR contributed to the page text.

    A PDF with pages but no readable text on ANY of them raises
    UnreadableDocumentError, which names the real problem (a scan) instead of
    letting it surface downstream as a generic "no extractable text".
    ``stats`` (optional) receives total/unreadable page counts so the caller can
    warn when only PART of the document could be read.
    """
    from app.services.file_processing import tesseract_available

    pages_pairs = extract_pages_from_pdf(file_content)  # uses existing retry logic
    text_by_page = {p: t for p, t in pages_pairs}

    # Steps 2-3 shell out to the tesseract binary. When it is missing or OCR is
    # off, every one of those calls raises and is swallowed -- so skip them
    # outright rather than re-opening the document and rendering each page at 2x
    # zoom for nothing (measured: ~28 s of ingest CPU on a 120-page scan).
    if not tesseract_available():
        total_pages = _pdf_page_count(file_content)
        readable = [(p, t, False) for p, t in pages_pairs if t and t.strip()]
        if stats is not None:
            stats["total_pages"] = total_pages
            stats["unreadable_pages"] = max(0, total_pages - len(readable))
        if total_pages and not readable:
            raise _no_text_layer_error(filename, total_pages)
        if total_pages > len(readable):
            print(
                f"      [INFO] {total_pages - len(readable)}/{total_pages} page(s) have no "
                f"text layer and OCR is unavailable — they are NOT indexed."
            )
        return readable

    # Re-open once to drive OCR over all pages (we need fitz Page objects)
    triples: List[Tuple[int, str, bool]] = []
    doc = None
    try:
        doc = fitz.open(stream=file_content, filetype="pdf")
        total_pages = len(doc)
        for i in range(total_pages):
            pn = i + 1
            base_text = text_by_page.get(pn, "")
            ocr_used = False

            # (2) Page OCR fallback for sparse pages
            if len(base_text.strip()) < OCR_MIN_TEXT_LEN:
                try:
                    page_obj = doc[i]
                    ocr_text = _ocr_pdf_page(page_obj).strip()
                    if ocr_text:
                        print(f"      [OCR] Page {pn}: fallback OCR added {len(ocr_text)} chars")
                        base_text = (base_text + "\n" + ocr_text).strip()
                        ocr_used = True
                except Exception as e:
                    print(f"      [OCR ERROR] Page {pn} fallback failed: {e}")

            # (3) Large embedded image OCR (figures, diagrams, scanned tables)
            try:
                page_obj = doc[i]
                emb_text = _ocr_embedded_images(doc, page_obj, PDF_IMAGE_OCR_MIN_DIM).strip()
                if emb_text:
                    print(f"      [OCR] Page {pn}: embedded-image OCR added {len(emb_text)} chars")
                    base_text = (base_text + "\n" + emb_text).strip() if base_text else emb_text
                    ocr_used = True
            except Exception as e:
                print(f"      [OCR ERROR] Page {pn} embedded-image scan failed: {e}")

            if base_text:
                triples.append((pn, base_text, ocr_used))
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    triples.sort(key=lambda t: t[0])
    if stats is not None:
        stats["total_pages"] = total_pages
        stats["unreadable_pages"] = max(0, total_pages - len(triples))
    if total_pages and not triples:
        # Text layer empty AND OCR read nothing — say so, don't fall through to
        # the generic "no extractable text".
        raise _no_text_layer_error(filename, total_pages)
    return triples


# ---------------------------------------------------------------------------
# Chunking v2 — structure-aware recursive
# ---------------------------------------------------------------------------
# Section header detection: markdown #+, numbered "1.2.3 Title" (period optional),
# or short ALL-CAPS lines.
_HEADER_RE = re.compile(
    r"^(?:"
    r"#{1,6}\s+.{1,120}"                                  # markdown
    r"|\d+(?:\.\d+){0,3}\.?\s+[A-Z][^\n]{1,120}"          # 1. INTRODUCTION  /  1.2 Title
    r"|[A-Z][A-Z0-9 \-]{3,80}"                            # ALL CAPS standalone
    r")$"
)


def _detect_section_header(text: str) -> Optional[str]:
    """
    Return the first heading-like line found anywhere in the chunk, or None.
    Scans all lines because the chunk often starts with an overlap prefix
    from the previous chunk, pushing the real section header further down.
    """
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if _HEADER_RE.match(s):
            return s[:120]
    return None


_DEFAULT_SEPARATORS = [
    "\n## ", "\n### ", "\n#### ",   # markdown-style headings (uncommon in PDFs but cheap)
    "\n\n",                          # paragraph
    "\n",                            # line
    ". ", "! ", "? ",                # sentence
    " ",                             # word
    "",                              # hard cut (last resort)
]


def _recursive_split(
    text: str,
    max_size: int,
    separators: Optional[List[str]] = None,
) -> List[str]:
    """
    Recursive character splitter. Walks down a separator hierarchy until
    every fragment fits in ``max_size``, falling back to a hard cut.

    Critical: each recursive call receives only the separators AFTER the one
    just used. Without that, re-attaching the separator to each split part
    would let the next level re-split on the same separator and loop forever.
    """
    if len(text) <= max_size:
        return [text]

    if separators is None:
        separators = _DEFAULT_SEPARATORS

    for idx, sep in enumerate(separators):
        if sep == "":
            # Last resort: hard cut at max_size
            return [text[i:i + max_size] for i in range(0, len(text), max_size)]
        if sep not in text:
            continue

        parts = text.split(sep)
        # Re-attach the separator to every part except the last so text
        # round-trips cleanly when chunks are concatenated.
        reattached = [p + sep for p in parts[:-1]] + [parts[-1]]

        remaining = separators[idx + 1:]
        result: List[str] = []
        for part in reattached:
            if not part:
                continue
            if len(part) <= max_size:
                result.append(part)
            else:
                result.extend(_recursive_split(part, max_size, remaining))
        return result

    # No separator helped — hard cut
    return [text[i:i + max_size] for i in range(0, len(text), max_size)]


def _merge_with_target(parts: List[str], target: int, max_size: int) -> List[str]:
    """
    Greedily pack small fragments toward ``target`` size, never exceeding
    ``max_size``. Preserves order. No overlap added here.
    """
    merged: List[str] = []
    current = ""
    for p in parts:
        if not p:
            continue
        if not current:
            current = p
            continue
        if len(current) + len(p) <= max_size:
            current += p
            # Emit once we've reached target
            if len(current) >= target:
                merged.append(current)
                current = ""
        else:
            merged.append(current)
            current = p
    if current:
        merged.append(current)
    return merged


def chunk_text_v2(
    pages: List[Tuple[int, str]],
    target: int = CHUNK_TARGET_SIZE,
    max_size: int = CHUNK_MAX_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[Dict[str, Any]]:
    """
    Structure-aware recursive chunker.

    Returns a list of dicts with keys:
        text            — chunk text (with overlap prefix from prior chunk)
        page_start      — 1-indexed page where this chunk begins
        section_header  — detected heading line for this chunk, or None
        chunk_index     — zero-based ordinal
    """
    if not pages:
        return []

    # Build full text and remember each page's starting char-offset so we
    # can later map any character position back to its source page.
    parts: List[str] = []
    page_offsets: List[Tuple[int, int]] = []  # (start_offset, page_num)
    offset = 0
    for page_num, page_text in pages:
        page_offsets.append((offset, page_num))
        parts.append(page_text)
        sep = "" if page_text.endswith("\n") else "\n"
        parts.append(sep)
        offset += len(page_text) + len(sep)
    full_text = "".join(parts)

    def offset_to_page(off: int) -> int:
        page = page_offsets[0][1]
        for start, pn in page_offsets:
            if start <= off:
                page = pn
            else:
                break
        return page

    # 1. Recursively split into fragments <= max_size
    # NOTE: keep whitespace-only fragments (e.g. "\n") — they preserve
    # document structure (line/paragraph breaks). Only drop truly empty strings.
    fragments = _recursive_split(full_text, max_size)
    fragments = [f for f in fragments if f]

    # 2. Greedy-pack into chunks near target size
    raw_chunks = _merge_with_target(fragments, target=target, max_size=max_size)

    # 3. Assemble final chunks: assign page_start, detect section, prepend overlap
    chunks: List[Dict[str, Any]] = []
    cursor = 0
    prev_tail = ""
    for i, body in enumerate(raw_chunks):
        # Locate this chunk's start in the full text (sequential scan)
        idx = full_text.find(body, cursor)
        if idx == -1:
            idx = cursor
        page_start = offset_to_page(idx)

        text = (prev_tail + body).strip() if prev_tail else body.strip()
        section_header = _detect_section_header(text)

        chunks.append({
            "text": text,
            "page_start": page_start,
            "section_header": section_header,
            "chunk_index": i,
        })

        prev_tail = body[-overlap:] if overlap > 0 else ""
        cursor = idx + len(body)

    return chunks


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """
    DEPRECATED v1 chunker. Kept for backward compatibility with any external
    callers. New ingestion uses chunk_text_v2().
    """
    if not text or len(text.strip()) == 0:
        return []

    chunks: List[str] = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = start + chunk_size
        chunk = text[start:end]

        if end < text_length:
            last_period = chunk.rfind('.')
            last_newline = chunk.rfind('\n')
            break_point = max(last_period, last_newline)
            if break_point > chunk_size * 0.5:
                chunk = text[start:start + break_point + 1]
                end = start + break_point + 1

        chunks.append(chunk.strip())
        start = end - overlap
        if start <= end - chunk_size + overlap:
            start = end

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Vector search helpers
# ---------------------------------------------------------------------------
def _combined_search_filter(user_id: Optional[str]) -> Dict[str, Any]:
    """
    Mongo ``$match`` filter for the single combined search.

    Knowledge-base chunks are GLOBAL: the knowledge_base branch carries NO
    userId, so every authenticated user retrieves the shared KB. user_upload
    chunks are PRIVATE: they are included only when scoped to ``user_id``, so a
    user never sees another user's uploads.

    ``user_id`` is the authenticated user's id (a stringified ObjectId). When it
    is None (e.g. the offline eval harness), the user_upload branch is omitted
    entirely and the search returns knowledge_base chunks only.

    Factored out so the query construction can be unit-tested without a live DB.
    """
    user_upload_branch = (
        [{"category": "user_upload", "userId": user_id}] if user_id else []
    )
    return {"$or": [{"category": "knowledge_base"}, *user_upload_branch]}


async def _search_combined(
    query_vector: List[float],
    limit: int,
    user_id: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Single vector search across KB + the current user's uploads, ranked purely
    by vector similarity. There are no per-category slots and no upload
    prioritization: a user_upload chunk only enters the pool if it scores well
    on the query.

    We over-fetch and filter client-side because the Atlas vector index doesn't
    include ``category`` as a filterable field — see project README for the
    index spec.
    """
    search_limit = limit * 20

    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_index",
                "path": "embedding",
                "queryVector": query_vector,
                "numCandidates": search_limit * 2,
                "limit": search_limit,
            }
        },
        {"$match": _combined_search_filter(user_id)},
        {"$limit": limit},
        {
            "$project": {
                "_id": 1,
                "text": 1,
                "filename": 1,
                "category": 1,
                "metadata": 1,
                "chunkingVersion": 1,
                "pageStart": 1,
                "sectionHeader": 1,
                "canonicalTitle": 1,
                "uploaderName": 1,
                "projectTag": 1,
                "version": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]

    results: List[Dict[str, Any]] = []
    async for doc in files_collection.aggregate(pipeline):
        results.append({
            "id": str(doc.get("_id")),
            "text": doc.get("text", ""),
            "filename": doc.get("filename", "unknown"),
            "category": doc.get("category", "unknown"),
            "metadata": doc.get("metadata", {}),
            "chunkingVersion": doc.get("chunkingVersion"),   # may be None for old v1 chunks
            "pageStart": doc.get("pageStart"),
            "sectionHeader": doc.get("sectionHeader"),
            "canonicalTitle": doc.get("canonicalTitle"),
            "uploaderName": doc.get("uploaderName"),
            "projectTag": doc.get("projectTag"),
            "version": doc.get("version"),
            "score": doc.get("score", 0.0),
        })

    return results[:limit]


def _fulltext_scope_filter(user_id: Optional[str]) -> Dict[str, Any]:
    """
    Atlas $search ``compound`` filter clause mirroring _combined_search_filter,
    but expressed natively for $search so the scope is applied INSIDE the BM25
    query (not as a post-$match). KB chunks are global; user_upload chunks are
    included only when scoped to ``user_id``. When ``user_id`` is None only the
    knowledge_base branch is present (eval-harness / KB-only path).

    ``category`` and ``userId`` must be indexed as ``token`` in text_index for
    the ``equals`` operator to match them.
    """
    scope_should: List[Dict[str, Any]] = [
        {"equals": {"path": "category", "value": "knowledge_base"}}
    ]
    if user_id:
        scope_should.append({
            "compound": {
                "must": [
                    {"equals": {"path": "category", "value": "user_upload"}},
                    {"equals": {"path": "userId", "value": user_id}},
                ]
            }
        })
    return {"compound": {"should": scope_should, "minimumShouldMatch": 1}}


async def _search_fulltext(
    query: str,
    limit: int,
    user_id: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Atlas full-text (Lucene BM25) search over the chunk ``text`` field, scoped
    to KB + the current user's uploads via a native $search compound filter.

    Returns the SAME dict shape as _search_combined (id, text, filename,
    category, metadata, chunkingVersion, pageStart, sectionHeader, score) so the
    two result lists are interchangeable for RRF fusion. Here ``score`` carries
    this search's native relevance ($meta searchScore / BM25); _rrf_merge is
    responsible for resetting the vector ``score`` to 0.0 for BM25-only docs.
    """
    pipeline = [
        {
            "$search": {
                "index": "text_index",
                "compound": {
                    "must": [{"text": {"query": query, "path": "text"}}],
                    "filter": [_fulltext_scope_filter(user_id)],
                },
            }
        },
        {"$limit": limit},
        {
            "$project": {
                "_id": 1,
                "text": 1,
                "filename": 1,
                "category": 1,
                "metadata": 1,
                "chunkingVersion": 1,
                "pageStart": 1,
                "sectionHeader": 1,
                "canonicalTitle": 1,
                "uploaderName": 1,
                "projectTag": 1,
                "version": 1,
                "score": {"$meta": "searchScore"},
            }
        },
    ]

    results: List[Dict[str, Any]] = []
    async for doc in files_collection.aggregate(pipeline):
        results.append({
            "id": str(doc.get("_id")),
            "text": doc.get("text", ""),
            "filename": doc.get("filename", "unknown"),
            "category": doc.get("category", "unknown"),
            "metadata": doc.get("metadata", {}),
            "chunkingVersion": doc.get("chunkingVersion"),
            "pageStart": doc.get("pageStart"),
            "sectionHeader": doc.get("sectionHeader"),
            "canonicalTitle": doc.get("canonicalTitle"),
            "uploaderName": doc.get("uploaderName"),
            "projectTag": doc.get("projectTag"),
            "version": doc.get("version"),
            "score": doc.get("score", 0.0),
        })

    return results[:limit]


def _rrf_merge(
    vec_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    limit: int,
    k: int = RRF_K,
) -> List[Dict[str, Any]]:
    """
    Reciprocal Rank Fusion of the vector and BM25 result lists.

    For each list a doc appears in, it contributes 1/(k + rank) to its fused
    score (rank is 1-based in list order). Docs are deduped by chunk id; the
    fused list is sorted by rrf_score descending and trimmed to ``limit``.

    The vector list is folded in FIRST so a doc that appears in both keeps its
    real vector ``score`` (vectorSearchScore). A doc found ONLY by BM25 has its
    ``score`` forced to 0.0 — it has no vector similarity, and downstream the
    legacy reranker-OFF fallback floors on that field (score >= 0.5). This keeps
    that floor a pure vector-similarity gate rather than leaking a BM25 score
    into it (edge noted in Phase 1).
    """
    fused: Dict[str, Dict[str, Any]] = {}

    for rank, doc in enumerate(vec_results, start=1):
        d = dict(doc)
        d["rrf_score"] = 1.0 / (k + rank)
        fused[doc["id"]] = d

    for rank, doc in enumerate(bm25_results, start=1):
        doc_id = doc["id"]
        contribution = 1.0 / (k + rank)
        if doc_id in fused:
            fused[doc_id]["rrf_score"] += contribution
        else:
            d = dict(doc)
            d["rrf_score"] = contribution
            d["score"] = 0.0  # BM25-only: no vector score; keep the legacy floor honest
            fused[doc_id] = d

    merged = sorted(fused.values(), key=lambda c: c["rrf_score"], reverse=True)
    return merged[:limit]


def _apply_rerank_threshold(
    ranked: List[Dict[str, Any]],
    threshold: float = RERANK_SCORE_THRESHOLD,
    top_k: int = RERANK_TOP_K,
    low_conf_context: int = LOW_CONF_CONTEXT_CHUNKS,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Drop reranked chunks the cross-encoder scored as irrelevant.

    ``ranked`` must already be sorted by ``rerank_score`` descending. We cap to
    the top ``top_k`` (as before), then keep only chunks scoring >= ``threshold``.
    ms-marco-MiniLM scores go negative for "not relevant", so sub-threshold
    chunks are retrieval noise that must not be shown to the user as sources.

    Returns ``(chunks, no_high_confidence)``:
      * Some chunk clears the threshold -> those chunks, each tagged
        ``low_confidence = False``; ``no_high_confidence = False``.
      * Nothing clears it -> the top ``low_conf_context`` chunks tagged
        ``low_confidence = True`` so the LLM still gets *some* context to attempt
        an answer; ``no_high_confidence = True``. Callers must exclude
        low-confidence chunks from the displayed sources.
    """
    top = ranked[:top_k]
    high_conf = [c for c in top if c.get("rerank_score", 0.0) >= threshold]

    if high_conf:
        for c in high_conf:
            c["low_confidence"] = False
        dropped = len(top) - len(high_conf)
        print(
            f"[RERANK] Threshold {threshold} applied: kept {len(high_conf)}, "
            f"dropped {dropped} (lowest kept: {high_conf[-1]['rerank_score']:+.2f})"
        )
        return high_conf, False

    # Every candidate is below the threshold — no high-confidence sources. Keep a
    # tiny low-confidence context set for the LLM; the caller hides these.
    fallback = ranked[:low_conf_context]
    for c in fallback:
        c["low_confidence"] = True
    print(
        f"[RERANK] All {len(top)} chunks below threshold {threshold} "
        f"- no high-confidence sources"
    )
    return fallback, True


async def query_vector_store(
    query: str, top_k: int = 5, user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Single combined vector search with optional cross-encoder reranking.

    KB chunks and the current user's uploads compete in ONE search
    (COMBINED_SEARCH_LIMIT = 25) ranked purely by vector similarity — no
    per-category slots, no user-upload prioritization (Problem 5).

    When RERANKER_ENABLED:
      * Reranks all combined candidates TOGETHER on query relevance.
      * Returns the top RERANK_TOP_K (5) after the score threshold, regardless
        of original category.

    When RERANKER_ENABLED is False (fallback):
      * Applies a single MIN_SCORE floor over the combined pool, still purely by
        vector relevance (no category priority).

    ``top_k`` is retained for signature stability but is effectively superseded
    by RERANK_TOP_K when the reranker is on.
    """
    print(f"[SEARCH] Combined search (limit {COMBINED_SEARCH_LIMIT})...")

    model = get_embedding_model()
    query_vector = list(model.embed([query]))[0].tolist()

    if HYBRID_SEARCH_ENABLED:
        # Run $vectorSearch and $search (BM25) in parallel, fuse via RRF, then
        # hand the fused pool to the SAME reranker path below (unchanged).
        print(f"[SEARCH] Hybrid mode: vector + BM25 (pool {HYBRID_POOL} each, RRF k={RRF_K})")
        vec, bm25 = await asyncio.gather(
            _search_combined(query_vector, HYBRID_POOL, user_id),
            _search_fulltext(query, HYBRID_POOL, user_id),
        )
        combined = _rrf_merge(vec, bm25, COMBINED_SEARCH_LIMIT, RRF_K)
        print(f"   Vector {len(vec)} + BM25 {len(bm25)} -> fused {len(combined)}")
    else:
        combined = await _search_combined(query_vector, COMBINED_SEARCH_LIMIT, user_id)

    # Category split is informational only — useful for debugging "why is this
    # upload still showing up." It does not affect ranking.
    kb_results = [c for c in combined if c.get("category") == "knowledge_base"]
    user_results = [c for c in combined if c.get("category") == "user_upload"]
    print(
        f"   Found {len(combined)} candidates: {len(kb_results)} from knowledge "
        f"base, {len(user_results)} from user uploads"
    )
    if kb_results:
        kb_files = list(dict.fromkeys(r["filename"] for r in kb_results))
        print(f"   KB files (top): {', '.join(kb_files[:3])}")
    if user_results:
        upload_files = list(dict.fromkeys(r["filename"] for r in user_results))
        print(f"   Upload files (top): {', '.join(upload_files[:3])}")

    # STEP 3a: reranker path
    if RERANKER_ENABLED:
        if not combined:
            print("[RERANK] No candidates to rerank")
            return []
        try:
            reranker = get_reranker()
            documents = [c["text"] for c in combined]
            scores = list(reranker.rerank(query, documents))
            for c, s in zip(combined, scores):
                c["rerank_score"] = float(s)
            combined.sort(key=lambda c: c["rerank_score"], reverse=True)
            top = combined[:RERANK_TOP_K]
            print(f"[RERANK] Reranked {len(combined)} candidates -> kept top {len(top)}")
            # Drop sub-threshold (noise) chunks. When nothing clears the bar the
            # helper returns the top 1-2 chunks tagged low_confidence=True so the
            # LLM still gets context; chat.py hides those from the sources list.
            kept, _no_high_conf = _apply_rerank_threshold(combined)
            for c in kept[:3]:
                print(f"   {c['rerank_score']:+.3f} | {c['filename']} (vec {c['score']:.3f})")
            return kept
        except Exception as rerank_err:
            print(f"[WARNING] Rerank failed ({rerank_err}); falling back to vector order")
            # Fall through to legacy filtering path below

    # STEP 3b: legacy fallback — single relevance floor, no category priority
    MIN_SCORE = 0.5
    filtered = [c for c in combined if c.get("score", 0) >= MIN_SCORE]
    print(
        f"[SEARCH] After relevance filtering: {len(filtered)} chunks "
        f"(vector-ranked, no category priority)"
    )
    return filtered


async def query_with_context(
    query: str, top_k: int = 5, user_id: Optional[str] = None
) -> Dict[str, Any]:
    """Query the vector store and return results with formatted context."""
    results = await query_vector_store(query, top_k, user_id)

    context = "\n\n".join([
        f"[Source: {r['filename']}]\n{r['text']}"
        for r in results
    ])

    return {
        "query": query,
        "results": results,
        "context": context,
        "num_results": len(results),
    }


# ---------------------------------------------------------------------------
# Thread-scoped retrieval (THREAD_DOC mode)
# ---------------------------------------------------------------------------
def _thread_scope_filter(user_id: Optional[str], thread_id: str) -> Dict[str, Any]:
    """Mongo ``find`` filter selecting ONLY one thread's uploaded chunks.

    The isolation boundary lives HERE, at the DB query: it matches
    ``category == "thread_upload"`` AND this exact ``threadId`` (AND ``userId``
    when provided), and requires ``chunkIndex`` to exist so the parent file-metadata
    doc (which has no embedding) is skipped. It therefore can NEVER return
    knowledge_base chunks, plain user_upload chunks, or another thread's chunks --
    regardless of how similar their text is. Factored out so it is unit-testable
    without a DB.
    """
    f: Dict[str, Any] = {
        "category": "thread_upload",
        "threadId": thread_id,
        "chunkIndex": {"$exists": True},
    }
    if user_id:
        f["userId"] = user_id
    return f


def _cosine(a: List[float], b: List[float]) -> float:
    """Plain cosine similarity. Used for in-Python scoring of the small
    thread-document set (see query_thread_documents for why not $vectorSearch)."""
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


async def thread_has_documents(thread_id: Optional[str], user_id: Optional[str]) -> bool:
    """True when this thread has at least one uploaded (thread_upload) document.

    Used by the router to decide whether THREAD_DOC is even possible for the
    current thread. Matches the parent file-metadata doc OR any chunk (both carry
    category/threadId/userId), so it is True as soon as an upload is registered,
    before background chunking finishes.
    """
    if not thread_id:
        return False
    query: Dict[str, Any] = {"category": "thread_upload", "threadId": thread_id}
    if user_id:
        query["userId"] = user_id
    doc = await files_collection.find_one(query, {"_id": 1})
    return doc is not None


def _log_thread_doc_mix(stage: str, chunks: List[Dict[str, Any]], score_key: str) -> None:
    """One ASCII line describing which documents make up ``chunks``: filename,
    chunks contributed, best score. This is the line to read when a THREAD_DOC
    retrieval question comes up. (The codebase logs via print; there is no
    leveled logger to attach a debug level to.)"""
    by_doc: Dict[str, Dict[str, Any]] = {}
    for c in chunks:
        entry = by_doc.setdefault(c.get("filename", "unknown"), {"n": 0, "best": None})
        entry["n"] += 1
        s = c.get(score_key)
        if s is not None and (entry["best"] is None or s > entry["best"]):
            entry["best"] = s
    parts = "; ".join(
        f"'{fn}' n={v['n']} best={v['best']:+.3f}" if v["best"] is not None
        else f"'{fn}' n={v['n']}"
        for fn, v in by_doc.items()
    )
    print(f"[THREAD] {stage} per-doc composition: {parts}")


def _apply_per_doc_candidate_quota(
    candidates: List[Dict[str, Any]],
    limit: int,
    per_doc: int,
) -> List[Dict[str, Any]]:
    """Stage-B quota: cap ``candidates`` (sorted by cosine ``score`` desc) to
    ``limit`` while reserving each document's top ``per_doc`` chunks first, so
    one large document cannot push another out of the set the reranker sees.

    Reservation is round-robin by per-document rank (every document's #1 chunk,
    then every #2, ...) in best-document order, so if the reservations alone
    exceed ``limit`` every document still gets its strongest chunks in. The
    remaining slots fill by global score order -- identical to the old plain cap.
    A single-document thread short-circuits to exactly the old ``[:limit]``.
    """
    if per_doc <= 0 or len(candidates) <= limit:
        return candidates[:limit]
    by_doc: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        by_doc.setdefault(c["filename"], []).append(c)
    if len(by_doc) <= 1:
        return candidates[:limit]

    doc_order = sorted(by_doc, key=lambda f: by_doc[f][0]["score"], reverse=True)
    selected: List[Dict[str, Any]] = []
    seen: set = set()
    for rank in range(per_doc):
        for fn in doc_order:
            if len(selected) >= limit:
                break
            chunks = by_doc[fn]
            if rank < len(chunks) and chunks[rank]["id"] not in seen:
                selected.append(chunks[rank])
                seen.add(chunks[rank]["id"])
        if len(selected) >= limit:
            break
    for c in candidates:
        if len(selected) >= limit:
            break
        if c["id"] not in seen:
            selected.append(c)
            seen.add(c["id"])

    selected.sort(key=lambda c: c["score"], reverse=True)
    return selected


def _apply_per_doc_context_quota(
    ranked: List[Dict[str, Any]],
    top_k: int,
    per_doc: int,
    threshold: float,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Stage-C quota: choose the final ``top_k`` context chunks from ``ranked``
    (sorted by ``rerank_score`` desc) with per-document representation.

    The threshold is applied FIRST: a document whose best chunk fails it
    contributes nothing -- the quota guarantees consideration, not inclusion.
    Among passing chunks, each document holds ``per_doc`` slots (its own best
    chunks) and the rest fill by global rerank order. When more documents pass
    than there are slots, slots fill one per document in best-score order and
    the excluded documents are logged by name.

    Mirrors _apply_rerank_threshold's contract: returns (chunks, no_high_conf),
    tags low_confidence, and delegates to it verbatim for the single-document
    and nothing-passes cases so those behave exactly as before.
    """
    passing = [c for c in ranked if c.get("rerank_score", 0.0) >= threshold]
    by_doc: Dict[str, List[Dict[str, Any]]] = {}
    for c in passing:
        by_doc.setdefault(c["filename"], []).append(c)
    if len(by_doc) <= 1:
        # Zero passing docs -> identical low-confidence fallback; one passing
        # doc -> identical plain top_k + threshold. No behavior change.
        return _apply_rerank_threshold(ranked, threshold=threshold, top_k=top_k)

    doc_order = sorted(by_doc, key=lambda f: by_doc[f][0]["rerank_score"], reverse=True)
    selected: List[Dict[str, Any]] = []
    seen: set = set()

    if len(doc_order) > top_k:
        # More documents than context slots: one chunk per document, filling in
        # best-document order, rather than silently dropping the quota.
        for fn in doc_order[:top_k]:
            best = by_doc[fn][0]
            selected.append(best)
            seen.add(best["id"])
        excluded = doc_order[top_k:]
        print(
            f"[THREAD] doc quota: {len(excluded)} document(s) excluded from the "
            f"{top_k}-slot context (one slot per document, best score first): "
            + ", ".join(f"'{fn}' best={by_doc[fn][0]['rerank_score']:+.2f}" for fn in excluded)
        )
    else:
        # Reserve each document's top per_doc passing chunks (round-robin by
        # per-document rank so reservations degrade fairly if they exceed
        # top_k), then fill the remainder by global rerank order.
        for rank in range(per_doc):
            for fn in doc_order:
                if len(selected) >= top_k:
                    break
                chunks = by_doc[fn]
                if rank < len(chunks) and chunks[rank]["id"] not in seen:
                    selected.append(chunks[rank])
                    seen.add(chunks[rank]["id"])
            if len(selected) >= top_k:
                break
        for c in passing:
            if len(selected) >= top_k:
                break
            if c["id"] not in seen:
                selected.append(c)
                seen.add(c["id"])

    selected.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
    for c in selected:
        c["low_confidence"] = False
    print(
        f"[RERANK] Threshold {threshold} applied with per-doc quota: kept "
        f"{len(selected)} across {len(set(c['filename'] for c in selected))} document(s) "
        f"(lowest kept: {selected[-1]['rerank_score']:+.2f})"
    )
    return selected, False


async def query_thread_documents(
    query: str,
    thread_id: str,
    user_id: Optional[str] = None,
    top_k: int = RERANK_TOP_K,
) -> List[Dict[str, Any]]:
    """Retrieve ONLY the current thread's uploaded document chunks.

    Isolation: candidates come from a plain Mongo ``find`` scoped by
    _thread_scope_filter, so KB chunks, plain user_upload chunks and other
    threads' chunks are never even fetched -- the near-identical-text case can't
    leak because those rows are excluded at the query, not merely ranked lower.

    Ranking: thread-document sets are small, and a global Atlas ``$vectorSearch``
    would risk post-filtering those few chunks out of its top candidates, so we
    score them with in-Python cosine here, then hand them to the SAME
    cross-encoder rerank + threshold path as the KB search (so THREAD_DOC and
    KB_QUERY share the same low-confidence semantics and confidence fallback).
    """
    filter_ = _thread_scope_filter(user_id, thread_id)
    projection = {
        "text": 1,
        "filename": 1,
        "category": 1,
        "metadata": 1,
        "chunkingVersion": 1,
        "pageStart": 1,
        "sectionHeader": 1,
        "threadId": 1,
        "embedding": 1,
    }

    docs: List[Dict[str, Any]] = []
    async for doc in files_collection.find(filter_, projection):
        docs.append(doc)

    if not docs:
        print(f"[THREAD] No documents found for thread {thread_id}")
        return []

    print(f"[THREAD] Scoring {len(docs)} chunk(s) for thread {thread_id}")
    model = get_embedding_model()
    qv = list(model.embed([query]))[0]
    qv = qv.tolist() if hasattr(qv, "tolist") else list(qv)

    candidates: List[Dict[str, Any]] = []
    for doc in docs:
        emb = doc.get("embedding") or []
        emb = emb.tolist() if hasattr(emb, "tolist") else list(emb)
        candidates.append({
            "id": str(doc.get("_id")),
            "text": doc.get("text", ""),
            "filename": doc.get("filename", "unknown"),
            "category": doc.get("category", "thread_upload"),
            "metadata": doc.get("metadata", {}),
            "chunkingVersion": doc.get("chunkingVersion"),
            "pageStart": doc.get("pageStart"),
            "sectionHeader": doc.get("sectionHeader"),
            "threadId": doc.get("threadId"),
            "score": _cosine(qv, emb),
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    # Stage-B cap. With the router on, reserve each document's top cosine
    # chunks first (per-doc quota) so one large document cannot monopolise the
    # candidate set the reranker sees; flag off keeps the old plain cap.
    if config.ROUTER_ENABLED:
        candidates = _apply_per_doc_candidate_quota(
            candidates, COMBINED_SEARCH_LIMIT, THREAD_DOC_MIN_CANDIDATES_PER_DOC
        )
        _log_thread_doc_mix("candidate set", candidates, "score")
    else:
        candidates = candidates[:COMBINED_SEARCH_LIMIT]

    # Same rerank path as the KB search, but with the PERMISSIVE thread threshold
    # (THREAD_RERANK_SCORE_THRESHOLD) instead of the KB threshold. The candidate
    # set is a single user-uploaded document, so we keep the thread's own chunk
    # for on-target questions rather than dropping it as "noise"; only the most
    # clearly off-topic questions (scoring at the cross-encoder floor) fall
    # through. _apply_rerank_threshold still tags low_confidence exactly as for
    # KB, so chat.py's retrieval-confidence fallback treats THREAD_DOC the same.
    if RERANKER_ENABLED:
        try:
            reranker = get_reranker()
            scores = list(reranker.rerank(query, [c["text"] for c in candidates]))
            for c, s in zip(candidates, scores):
                c["rerank_score"] = float(s)
            candidates.sort(key=lambda c: c["rerank_score"], reverse=True)
            # Stage-C cap. With the router on, every document whose best chunk
            # clears the threshold holds a context slot (per-doc quota); flag
            # off keeps the old plain top_k + threshold, byte-identical.
            if config.ROUTER_ENABLED:
                kept, _no_high_conf = _apply_per_doc_context_quota(
                    candidates,
                    top_k=top_k,
                    per_doc=THREAD_DOC_MIN_CHUNKS_PER_DOC,
                    threshold=THREAD_RERANK_SCORE_THRESHOLD,
                )
                _log_thread_doc_mix("final context", kept, "rerank_score")
            else:
                kept, _no_high_conf = _apply_rerank_threshold(
                    candidates, threshold=THREAD_RERANK_SCORE_THRESHOLD
                )
            return kept
        except Exception as rerank_err:
            print(f"[WARNING] Thread rerank failed ({rerank_err}); falling back to vector order")

    # Legacy fallback (reranker off/failed): single relevance floor, mirroring
    # query_vector_store's STEP 3b.
    MIN_SCORE = 0.5
    return [c for c in candidates if c.get("score", 0) >= MIN_SCORE]


# ---------------------------------------------------------------------------
# Ingestion + deletion
# ---------------------------------------------------------------------------
async def delete_document(filename: str) -> Dict[str, Any]:
    """Delete all vector chunks associated with a filename from MongoDB."""
    try:
        print(f"[DELETE] Deleting document: {filename}")
        print(f"[DELETE] Searching for filename: {repr(filename)}")

        sample = await files_collection.find_one({"userId": USER_ID})
        if sample:
            print(f"[DELETE] Sample filename in DB: {repr(sample.get('filename'))}")

        result = await files_collection.delete_many({
            "$or": [
                {"filename": filename},
                {"source": filename},
            ],
            "userId": USER_ID,
        })

        deleted_count = result.deleted_count
        print(f"[OK] Deleted {deleted_count} chunks for file: {filename}")

        return {
            "filename": filename,
            "deleted_count": deleted_count,
            "status": "success",
        }
    except Exception as e:
        print(f"[ERROR] Failed to delete document {filename}: {e}")
        raise ValueError(f"Failed to delete document: {str(e)}")


# ---------------------------------------------------------------------------
# Ingestion thread pool (Phase 0 — CPU work off the event loop)
# ---------------------------------------------------------------------------
_ingest_pool: Optional[ThreadPoolExecutor] = None
_ingest_pool_lock = threading.Lock()


def _get_ingest_pool() -> ThreadPoolExecutor:
    """Lazily create the dedicated, bounded pool that runs CPU-bound ingestion
    off the event loop. Kept separate from Starlette's default threadpool so a
    burst of large uploads can't starve sync route work. Sized by INGEST_WORKERS.
    """
    global _ingest_pool
    if _ingest_pool is None:
        with _ingest_pool_lock:
            if _ingest_pool is None:
                _ingest_pool = ThreadPoolExecutor(
                    max_workers=max(1, config.INGEST_WORKERS),
                    thread_name_prefix="ingest",
                )
    return _ingest_pool


# In-process backlog counter for the queue-depth cap (Phase 0.5). Reserved when
# an upload is admitted, released when its ingest finishes. One uvicorn worker
# today, but the lock keeps it correct if the pool ever grows.
_ingest_inflight = 0
_ingest_inflight_lock = threading.Lock()


def ingest_queue_depth() -> int:
    """Number of ingests currently queued or running in this process."""
    return _ingest_inflight


def ingest_try_acquire() -> bool:
    """Reserve one ingest slot. Returns False when the backlog is already at
    INGEST_MAX_QUEUE, so the caller can reject the upload instead of piling on."""
    global _ingest_inflight
    with _ingest_inflight_lock:
        if _ingest_inflight >= config.INGEST_MAX_QUEUE:
            return False
        _ingest_inflight += 1
        return True


def ingest_release() -> None:
    """Release a slot reserved by ingest_try_acquire (floors at 0)."""
    global _ingest_inflight
    with _ingest_inflight_lock:
        _ingest_inflight = max(0, _ingest_inflight - 1)


def _ingest_compute(
    filename: str,
    file_content: bytes,
    category: str,
    owner_id: str,
    thread_id: Optional[str],
    file_type: str,
    pre_extracted_pages: Optional[List[Tuple[int, str, bool]]] = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], int, int, Optional[str]]:
    """CPU-bound half of ingest: extract -> chunk -> embed -> build doc dicts.

    Pure compute with no event-loop or motor access, so it runs safely in a
    worker thread. The heavy calls here (PyMuPDF, the tesseract OCR subprocess,
    fastembed/ONNX embedding) release the GIL, so the event loop stays responsive
    while this runs. Returns (documents, chunks_created, total_chars, warning) --
    ``warning`` is a user-facing note when the document was only PARTIALLY
    readable (e.g. a report whose figure pages are scans); the async Mongo insert
    stays with the caller on the loop.

    ``pre_extracted_pages`` (KB path) supplies already-extracted (page, text, ocr)
    triples from the kb_formats registry, skipping step 1. ``provenance`` (KB
    path) is merged onto every chunk doc so each chunk carries its uploader /
    project / batch / canonicalTitle / contentHash.
    """
    # 1. Extraction — use the caller's pages (KB) or extract here (live path).
    extract_stats: Dict[str, Any] = {}
    if pre_extracted_pages is not None:
        page_triples = pre_extracted_pages
        print(f"  1. Using {len(page_triples)} pre-extracted page(s) ({file_type})...")
    else:
        # Lazy import to avoid a circular dependency at module load
        from app.services.file_processing import extract_pages_from_file
        print(f"  1. Extracting text ({file_type})...")
        page_triples = extract_pages_from_file(file_content, filename, stats=extract_stats)
    pages = [(p, t) for p, t, _ in page_triples]
    ocr_by_page = {p: ocr for p, _, ocr in page_triples}
    total_chars = sum(len(t) for _, t in pages)
    print(f"      Extracted {len(pages)} pages, {total_chars} characters")

    if not pages or total_chars < 10:
        raise ValueError(f"{filename} appears to be empty or contains no extractable text")

    # 2. Structure-aware chunking (v2) — same chunker for every file type
    print(
        f"  2. Chunking text (v2, target={CHUNK_TARGET_SIZE}, "
        f"max={CHUNK_MAX_SIZE}, overlap={CHUNK_OVERLAP})..."
    )
    chunk_records = chunk_text_v2(pages)
    print(f"      Created {len(chunk_records)} chunks")
    if not chunk_records:
        raise ValueError("No text chunks could be created from the file")

    # 3. Embeddings (batch)
    print("  3. Generating embeddings...")
    model = get_embedding_model()
    texts = [c["text"] for c in chunk_records]
    embeddings_list = list(model.embed(texts))
    embeddings = [e.tolist() for e in embeddings_list]
    print(f"      Generated {len(embeddings)} embeddings (384-dim)")

    # 4. Build documents — propagate fileType + per-page OCR flag
    print("  4. Creating document objects...")
    documents: List[Dict[str, Any]] = []
    for c, embedding in zip(chunk_records, embeddings):
        page_start = c["page_start"]
        ocr_extracted = bool(ocr_by_page.get(page_start, False))
        doc = {
            "text": c["text"],
            "filename": filename,
            "source": filename,
            "embedding": embedding,
            "userId": owner_id,
            "category": category,
            "chunkIndex": c["chunk_index"],
            "totalChunks": len(chunk_records),
            "chunkingVersion": CHUNKING_VERSION,
            "pageStart": page_start,
            "sectionHeader": c["section_header"],
            "metadata": {
                "chunkSize": len(c["text"]),
                "chunkIndex": c["chunk_index"],
                "totalChunks": len(chunk_records),
                "originalFilename": filename,
                "category": category,
                "chunkingVersion": CHUNKING_VERSION,
                "pageStart": page_start,
                "sectionHeader": c["section_header"],
                "fileType": file_type,
                "ocrExtracted": ocr_extracted,
            },
            "createdAt": datetime.now(),
        }
        # Thread-scoped uploads carry their threadId so retrieval can filter to
        # exactly this conversation thread. Omitted entirely for shared KB and
        # per-user uploads, so their documents are byte-identical to before.
        if thread_id is not None:
            doc["threadId"] = thread_id
            doc["metadata"]["threadId"] = thread_id
        # KB provenance (uploader/project/batch/canonicalTitle/contentHash/...)
        # stamped identically on every chunk so any single chunk is traceable.
        if provenance:
            doc.update(provenance)
        documents.append(doc)

    print(f"      Prepared {len(documents)} documents")

    chunks_created = len(chunk_records)

    # Partially-readable document: some pages carried no text (scanned figure
    # pages are the common case) and are therefore invisible to the assistant.
    # Surfacing this stops a user trusting an answer drawn from half a report.
    warning: Optional[str] = None
    skipped = extract_stats.get("unreadable_pages") or 0
    total_p = extract_stats.get("total_pages") or 0
    if skipped and total_p:
        warning = (
            f"{skipped} of {total_p} pages had no readable text (scanned or "
            f"image-only) and were not indexed."
        )
        print(f"      [WARNING] {warning}")

    # Free the large intermediates; the caller only needs `documents`.
    del pages, chunk_records, texts, embeddings_list, embeddings
    return documents, chunks_created, total_chars, warning


async def ingest_document(
    filename: str,
    file_content: bytes,
    category: str = "user_upload",
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    pre_extracted_pages: Optional[List[Tuple[int, str, bool]]] = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Multi-format ingest: extract pages, v2 chunk, embed, store.

    Supported types are defined in file_processing.SUPPORTED_EXTENSIONS.
    Chunks carry chunkingVersion, pageStart, sectionHeader, fileType, and
    (for OCR-derived pages) metadata.ocrExtracted = True.

    user_upload chunks are tagged with ``user_id`` (the uploading user's id) so
    retrieval can scope them per-user. KB ingestion via the kb_admin CLI passes
    no user_id and falls back to the legacy shared-KB owner id (config.USER_ID),
    so that admin tooling -- which locates KB chunks by that id -- keeps working
    unchanged. No HTTP route relies on this fallback; routes always pass an
    explicit user_id.

    ``thread_id`` scopes an upload to a single conversation thread: when set, each
    chunk additionally carries ``threadId`` (and ``metadata.threadId``) so
    thread-scoped retrieval (query_thread_documents) can filter to exactly this
    thread. The caller passes ``category="thread_upload"`` for these so they are a
    distinct scope -- never mixed into the shared knowledge_base or the per-user
    ``user_upload`` corpus, and therefore never surfaced by the KB search.

    Steps 1-4 (extract -> chunk -> embed -> build docs) are CPU-bound and run in
    a dedicated worker-thread pool so the single uvicorn event loop stays
    responsive during a large ingest; the motor insert (step 5) stays on the
    loop. Set INGEST_OFFLOAD_ENABLED=false to run inline (pre-Phase-0 behaviour).
    """
    owner_id = user_id if user_id is not None else USER_ID
    # Lazy import to avoid a circular dependency at module load
    from app.services.file_processing import (
        get_file_type,
        is_supported_file,
        SUPPORTED_EXTENSIONS,
    )

    file_type = get_file_type(filename)
    # The KB path pre-extracts via the kb_formats registry (which validates the
    # format itself and supports .txt/.md that file_processing does not), so the
    # supported-type check only applies when we extract here.
    if pre_extracted_pages is None and not is_supported_file(filename):
        raise ValueError(
            f"Unsupported file type: {file_type}. "
            f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    print(f"[FILE] Ingesting document: {filename} (type: {file_type})")

    # CPU-bound steps 1-4 off the loop (or inline when the flag is off).
    if config.INGEST_OFFLOAD_ENABLED:
        loop = asyncio.get_running_loop()
        documents, chunks_created, total_chars, warning = await loop.run_in_executor(
            _get_ingest_pool(),
            _ingest_compute,
            filename,
            file_content,
            category,
            owner_id,
            thread_id,
            file_type,
            pre_extracted_pages,
            provenance,
        )
    else:
        documents, chunks_created, total_chars, warning = _ingest_compute(
            filename, file_content, category, owner_id, thread_id, file_type,
            pre_extracted_pages, provenance,
        )

    # 5. Insert (motor — on the event loop)
    print("  5. Inserting into MongoDB...")
    result = await files_collection.insert_many(documents)
    inserted_count = len(result.inserted_ids)
    print(f"      Inserted {inserted_count} documents")

    print(f"[OK] Document ingestion complete: {filename}")

    # Free the built documents before returning
    del documents
    gc.collect()

    return {
        "filename": filename,
        "chunks_created": chunks_created,
        "total_characters": total_chars,
        "documents_inserted": inserted_count,
        "chunking_version": CHUNKING_VERSION,
        "status": "success",
        # None unless part of the document could not be read (see _ingest_compute).
        "warning": warning,
    }
