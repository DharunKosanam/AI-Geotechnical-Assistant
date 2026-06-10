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
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import quote
import gc
from datetime import datetime

import fitz  # PyMuPDF
from fastembed import TextEmbedding

from app.core.database import files_collection
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
    LOW_CONF_CONTEXT_CHUNKS,
    PRE_RERANK_POOL_USER,
    PRE_RERANK_POOL_KB,
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
_reranker_model = None


def get_embedding_model():
    """Get or initialize the embedding model (lazy loading)."""
    global _embedding_model
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


def extract_pages_from_pdf_with_ocr(file_content: bytes) -> List[Tuple[int, str, bool]]:
    """
    Page-aware PDF extraction with OCR fallback.

    For each page:
      1. PyMuPDF text layer (fast path).
      2. If that yields < OCR_MIN_TEXT_LEN chars AND OCR is enabled, render
         the page and OCR it.
      3. Independently, OCR any large embedded images and append.

    Returns (page_number, text, ocr_extracted). ``ocr_extracted`` is True
    when any OCR contributed to the page text.
    """
    pages_pairs = extract_pages_from_pdf(file_content)  # uses existing retry logic
    text_by_page = {p: t for p, t in pages_pairs}

    if not OCR_ENABLED:
        return [(p, t, False) for p, t in pages_pairs]

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
async def _search_by_category(
    query_vector: List[float],
    category: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """
    Vector search filtered by category. We over-fetch and filter client-side
    because the Atlas vector index doesn't include ``category`` as a
    filterable field — see project README for the index spec.
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
        {
            "$match": {
                "category": category,
                "userId": USER_ID,
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
            "score": doc.get("score", 0.0),
        })

    return results[:limit]


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


async def query_vector_store(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Two-stage vector search with optional cross-encoder reranking.

    When RERANKER_ENABLED:
      * Pulls PRE_RERANK_POOL_USER (15) + PRE_RERANK_POOL_KB (10) candidates.
      * Reranks all survivors TOGETHER on query relevance.
      * Returns the top RERANK_TOP_K (5) regardless of original category — the
        cross-encoder picks purely on relevance, so the explicit user-first
        ordering is dropped in this mode by design.

    When RERANKER_ENABLED is False (fallback):
      * Uses the legacy 5/3 pools, MIN_SCORE 0.5 floor, and user-first ordering.

    ``top_k`` is retained for signature stability but is effectively superseded
    by RERANK_TOP_K when the reranker is on.
    """
    print(f"[SEARCH] Two-stage search for: {query[:50]}...")

    model = get_embedding_model()
    query_vector = list(model.embed([query]))[0].tolist()

    user_pool = PRE_RERANK_POOL_USER if RERANKER_ENABLED else 5
    kb_pool = PRE_RERANK_POOL_KB if RERANKER_ENABLED else 3

    # STEP 1: user uploads
    print(f"[SEARCH] Step 1: Searching user uploads (limit {user_pool})...")
    user_results = await _search_by_category(query_vector, "user_upload", user_pool)
    print(f"   Found {len(user_results)} chunks from user uploads")
    if user_results:
        user_files = list({r['filename'] for r in user_results})
        print(f"   Files: {', '.join(user_files[:3])}")

    # STEP 2: knowledge base
    print(f"[SEARCH] Step 2: Searching knowledge base (limit {kb_pool})...")
    kb_results = await _search_by_category(query_vector, "knowledge_base", kb_pool)
    print(f"   Found {len(kb_results)} chunks from knowledge base")
    if kb_results:
        kb_files = list({r['filename'] for r in kb_results})
        print(f"   Files: {', '.join(kb_files[:3])}")

    # STEP 3a: reranker path
    if RERANKER_ENABLED:
        combined = user_results + kb_results
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

    # STEP 3b: legacy path — score floors + user-first ordering
    MIN_SCORE = 0.5
    user_results = [r for r in user_results if r.get("score", 0) >= MIN_SCORE]
    if user_results:
        kb_results = [r for r in kb_results if r.get("score", 0) >= 0.75]
    else:
        kb_results = [r for r in kb_results if r.get("score", 0) >= MIN_SCORE]

    combined = user_results + kb_results
    print(f"[SEARCH] After relevance filtering: {len(combined)} chunks")
    print(f"   Kept: {len(user_results)} user + {len(kb_results)} knowledge base")
    return combined


async def query_with_context(query: str, top_k: int = 5) -> Dict[str, Any]:
    """Query the vector store and return results with formatted context."""
    results = await query_vector_store(query, top_k)

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


async def ingest_document(
    filename: str,
    file_content: bytes,
    category: str = "user_upload",
) -> Dict[str, Any]:
    """
    Multi-format ingest: extract pages, v2 chunk, embed, store.

    Supported types are defined in file_processing.SUPPORTED_EXTENSIONS.
    Chunks carry chunkingVersion, pageStart, sectionHeader, fileType, and
    (for OCR-derived pages) metadata.ocrExtracted = True.
    """
    # Lazy import to avoid a circular dependency at module load
    from app.services.file_processing import (
        extract_pages_from_file,
        get_file_type,
        is_supported_file,
        SUPPORTED_EXTENSIONS,
    )

    file_type = get_file_type(filename)
    if not is_supported_file(filename):
        raise ValueError(
            f"Unsupported file type: {file_type}. "
            f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    print(f"[FILE] Ingesting document: {filename} (type: {file_type})")

    # 1. Unified page-aware extraction (returns triples with OCR flag)
    print(f"  1. Extracting text ({file_type})...")
    page_triples = extract_pages_from_file(file_content, filename)
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
            "userId": USER_ID,
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
        documents.append(doc)

    print(f"      Prepared {len(documents)} documents")

    # 5. Insert
    print("  5. Inserting into MongoDB...")
    result = await files_collection.insert_many(documents)
    inserted_count = len(result.inserted_ids)
    print(f"      Inserted {inserted_count} documents")

    print(f"[OK] Document ingestion complete: {filename}")

    chunks_created = len(chunk_records)
    # Free large objects before returning
    del pages, chunk_records, texts, embeddings_list, embeddings, documents
    gc.collect()

    return {
        "filename": filename,
        "chunks_created": chunks_created,
        "total_characters": total_chars,
        "documents_inserted": inserted_count,
        "chunking_version": CHUNKING_VERSION,
        "status": "success",
    }
