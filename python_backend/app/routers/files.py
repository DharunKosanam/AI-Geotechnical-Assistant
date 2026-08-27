"""
File management endpoints - MongoDB storage with vector embeddings
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status, File, UploadFile, Form, Body, BackgroundTasks
from fastapi.responses import StreamingResponse, Response
from datetime import datetime
from typing import Optional, List
import io
from bson import ObjectId
from app.core import config
from app.core.config import RATE_LIMIT_UPLOAD, RATE_LIMIT_UPLOAD_HOURLY
from app.core.database import conversations_collection, files_collection
from app.core.rate_limit import limiter, rate_limit_identify, user_id_key
from app.dependencies.auth import get_current_user
from app.services.file_processing import (
    convert_image_to_pdf,
    needs_image_conversion,
    determine_media_type,
    is_image_file,
    is_supported_file,
    get_file_type,
    extract_pages_from_file,
    tesseract_available,
    SUPPORTED_EXTENSIONS,
    TEXT_FORMATS_LABEL,
    VISION_IMAGE_EXTS,
)
from app.services.rag_service import (
    effective_ingest_status,
    ingest_document,
    extract_text_from_pdf,
    get_embedding_model,
    ingest_try_acquire,
    ingest_release,
)
from models import User

router = APIRouter(prefix="/api", tags=["files"])

# Hard ceiling for any uploaded file. Bigger payloads must be split client-side.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# Friendly format list reused in error messages so the UI can echo it back.
# Images are only genuinely accepted when something can actually read them --
# AI vision (flag on) or OCR -- so the list is built from capability rather
# than hardcoded; otherwise a rejection message would advertise PNG/JPG on a
# server that cannot read either.
def _supported_list_label() -> str:
    if config.VISION_EXTRACTION_ENABLED:
        label = f"{TEXT_FORMATS_LABEL}, PNG, JPG, JPEG, WEBP"
        if tesseract_available():
            label += ", TIFF"
        return label
    if tesseract_available():
        return f"{TEXT_FORMATS_LABEL}, PNG, JPG, JPEG, TIFF"
    return TEXT_FORMATS_LABEL


def _vision_image_upload_ok(filename: str) -> bool:
    """True when this upload is a JPEG/PNG/WebP that the vision path will
    read. Gated on the flag at call time: with vision off, .webp stays
    unsupported and PNG/JPG keep today's OCR-only handling, byte-identical."""
    return (
        config.VISION_EXTRACTION_ENABLED
        and get_file_type(filename) in VISION_IMAGE_EXTS
    )


def _validate_upload(filename: Optional[str], size_bytes: int) -> None:
    """
    Centralized upload validation. Extension is the source of truth for type
    (browsers send wrong MIME for .docx / .xlsx) — MIME stays advisory.
    """
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing filename",
        )
    if not is_supported_file(filename) and not _vision_image_upload_ok(filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Supported: {_supported_list_label()}",
        )
    if size_bytes <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty",
        )
    if size_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large ({size_bytes / 1024 / 1024:.1f} MB). Max: 50 MB.",
        )


def _reject_unreadable_image(filename: Optional[str]) -> None:
    """An image's content can only be reached by AI vision (flag on) or OCR.
    Where neither is available, say so NOW rather than accepting the file,
    spending a background task on it and failing a few seconds later with a
    vaguer message."""
    if not filename or not is_image_file(filename):
        return
    if _vision_image_upload_ok(filename):
        return  # vision reads JPEG/PNG (and WebP) without OCR
    if not tesseract_available():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{filename} is an image, and this server cannot read text from "
                f"images (OCR is not available). Please upload a text document "
                f"instead ({TEXT_FORMATS_LABEL})."
            ),
        )


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _validate_diagram_upload(
    filename: Optional[str],
    size_bytes: int,
    file_content: bytes,
    diagram_xml: Optional[str],
) -> None:
    """Entry validation for a diagram upload (DIAGRAM_EDITOR_ENABLED): a PNG
    for display plus the draw.io XML as the ONLY extraction source.

    Replaces _validate_upload/_reject_unreadable_image for this source type:
    those gates decide whether PIXELS are readable (OCR/vision), which is
    irrelevant here — the text comes from the XML, and a diagram must stay
    valid on a server with neither OCR nor vision. Size/emptiness rules match
    _validate_upload exactly.
    """
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing filename",
        )
    if get_file_type(filename) != ".png":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A diagram upload must be a .png file.",
        )
    if size_bytes <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty",
        )
    if size_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large ({size_bytes / 1024 / 1024:.1f} MB). Max: 50 MB.",
        )
    if not file_content.startswith(_PNG_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The diagram image is not a valid PNG.",
        )
    if not diagram_xml or not diagram_xml.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A diagram upload must include its source XML (diagramXml).",
        )


# Extensions the upload UI offers with vision OFF -- exactly today's static
# frontend list. Deliberately narrower than SUPPORTED_EXTENSIONS (no TIFF/PNG/
# JPG): the UI has never offered images, even where OCR could read them, and
# the flag-off UI must stay byte-identical.
_UI_TEXT_EXTENSIONS = [".pdf", ".docx", ".xlsx", ".xls", ".csv", ".pptx"]
_UI_TEXT_LABEL = "PDF, DOCX, XLSX, XLS, CSV, PPTX"


@router.get("/upload/config")
async def upload_config(current_user: User = Depends(get_current_user)):
    """Capability handshake for the upload UI: which file types to offer in
    the picker. The frontend cannot probe this per-request the way it probes
    streaming (the accept filter must be right BEFORE a file is chosen), so it
    fetches this once and falls back to the text-only list on any failure --
    making flag-off rendering identical to today either way."""
    extensions = list(_UI_TEXT_EXTENSIONS)
    label = _UI_TEXT_LABEL
    if config.VISION_EXTRACTION_ENABLED:
        extensions += sorted(VISION_IMAGE_EXTS)
        label += ", PNG, JPG, WEBP"
    payload = {"extensions": extensions, "label": label}
    # Diagram editor capability (DIAGRAM_EDITOR_ENABLED). Present only when ON:
    # a flag-off server returns byte-identical bytes to before the feature, and
    # the frontend treats an absent field as off (fails closed).
    if config.DIAGRAM_EDITOR_ENABLED:
        payload["diagramEditor"] = True
    # Message highlights capability (HIGHLIGHTS_ENABLED). Same contract:
    # present only when ON, absent (not false) when off.
    if config.HIGHLIGHTS_ENABLED:
        payload["highlights"] = True
    return payload


@router.get("/files")
async def list_files_simple(
    category: Optional[str] = None,
    threadId: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """
    Simple files list endpoint for frontend compatibility

    Args:
        category: Optional filter by category ("user_upload" or "knowledge_base")
        threadId: List THIS conversation's uploaded documents (parent docs
            only), each with its lifecycle status (pending/ready/failed) and
            failure reason. Scoped to the caller's own threads.
    """
    try:
        # Thread-documents listing (Phase 1): parent docs with lifecycle state.
        if threadId:
            docs = []
            async for doc in files_collection.find(
                {"userId": current_user.id, "threadId": threadId,
                 "category": "thread_upload", "chunkIndex": {"$exists": False}},
                {"filename": 1, "chunkCount": 1, "status": 1, "error": 1,
                 "warning": 1, "createdAt": 1},
            ).sort("createdAt", 1):
                life_status, life_reason = effective_ingest_status(doc)
                docs.append({
                    "id": str(doc.get("_id")),
                    "filename": doc.get("filename", "Unknown"),
                    "status": life_status,
                    "error": life_reason,
                    "warning": doc.get("warning"),
                    "chunkCount": doc.get("chunkCount", 0),
                    "createdAt": doc.get("createdAt").isoformat() if doc.get("createdAt") else None,
                })
            return {"files": docs}

        # knowledge_base is shared across all users (never userId-scoped);
        # everything else is the caller's own data.
        if category == "knowledge_base":
            query = {"category": "knowledge_base"}
        elif category:
            query = {"userId": current_user.id, "category": category}
        else:
            query = {"userId": current_user.id}

        # CRITICAL FIX: Exclude heavy fields to prevent memory limit error
        # Projection excludes: text content, embeddings (can be 100s of MB)
        projection = {
            "text": 0,           # Exclude text content
            "embedding": 0,      # Exclude vector embeddings
            "content": 0         # Exclude binary file content (if present)
        }
        
        # Use projection and allow disk use for large datasets
        cursor = files_collection.find(query, projection)
        
        files = []
        async for doc in cursor:
            files.append({
                "id": str(doc.get("_id")),
                "filename": doc.get("filename", "Unknown"),
                "category": doc.get("category", "user_upload"),
                "createdAt": doc.get("createdAt").isoformat() if doc.get("createdAt") else None,
            })
        return {"files": files}
    except Exception as e:
        print(f"[ERROR] Error listing files: {e}")
        import traceback
        traceback.print_exc()
        return {"files": []}


def extract_text_from_file(file_content: bytes, filename: str) -> str:
    """
    Concatenate text across all pages via the unified multi-format extractor.
    Used by the legacy synchronous upload path so we get a single text blob
    for the file-level embedding stored on the parent file doc.
    """
    try:
        if is_supported_file(filename):
            triples = extract_pages_from_file(file_content, filename)
            return "\n".join(t for _, t, _ in triples)
        if filename.lower().endswith((".txt", ".md")):
            return file_content.decode("utf-8", errors="ignore")
        return ""
    except Exception as e:
        print(f"[WARNING] Could not extract text from {filename}: {e}")
        return ""


async def generate_embeddings(text: str) -> List[float]:
    """Generate embeddings for text using FastEmbed (lazy-loaded model)"""
    if not text or not text.strip():
        return [0.0] * 384
    
    model = get_embedding_model()
    embeddings = list(model.embed([text]))
    return embeddings[0].tolist()


def _user_facing_ingest_error(filename: str, error: Exception) -> str:
    """Turn an unexpected ingest exception into something the uploader can act on.

    The chip shows this string verbatim, so it must never be a raw library
    message ("Failed to open stream", "cannot find loader for this WMF file").
    We keep a short, specific line per known failure shape and fall back to a
    generic one; the real exception is always in the server log.
    """
    text = str(error).lower()
    ext = get_file_type(filename)

    if "no text chunks" in text or "no extractable text" in text or "empty" in text:
        return f"No readable text could be extracted from {filename}."
    if ext == ".pdf" and ("open stream" in text or "cannot open" in text or "format error" in text):
        return (
            f"{filename} could not be opened as a PDF — the file may be damaged "
            f"or incomplete. Try re-saving or re-downloading it."
        )
    if "password" in text or "encrypted" in text:
        return f"{filename} is password-protected. Please upload an unlocked copy."
    if "is not installed" in text:
        # A missing optional extractor library is a server problem, not the
        # user's file — don't tell them to fix their document.
        return (
            f"{filename} could not be processed: this file type is not fully "
            f"supported on the server yet. Please try a PDF or DOCX."
        )
    return (
        f"{filename} could not be processed. It may be damaged or in an "
        f"unsupported variant of its format."
    )


async def process_file_ingestion(
    filename: str,
    file_content: bytes,
    category: str = "user_upload",
    parent_id: Optional[ObjectId] = None,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    source_type: Optional[str] = None,
    diagram_xml: Optional[str] = None,
):
    """
    Background task to process file ingestion.

    If parent_id is provided, the parent file-metadata doc's status is
    updated to "processed" on success or "failed" on error, so the UI
    can stop showing "Processing..." once chunks land in Mongo.

    ``thread_id`` (with category "thread_upload") scopes the upload to a single
    conversation thread so it is retrievable only in THREAD_DOC mode.

    ``source_type == "diagram"`` (DIAGRAM_EDITOR_ENABLED): ``diagram_xml`` is
    flattened to text by pure parsing (zero LLM calls) and fed into the NORMAL
    chunk/embed path via ingest_document's pre-extracted-pages hook, with
    provenance stamping sourceType on every chunk. The PNG in ``file_content``
    is display-only; because pre_extracted_pages is set, ingest_document's
    vision branch (which requires it to be None) can never see a diagram.
    """
    import gc
    from app.services.file_processing import UnreadableDocumentError
    from app.services.diagram_extraction import EmptyDiagramError, extract_diagram_text
    try:
        print(f"[LOADING] Background processing started for: {filename} (category: {category})")
        if source_type == "diagram":
            flat_text = extract_diagram_text(diagram_xml or "")
            result = await ingest_document(
                filename,
                file_content,
                category,
                user_id=user_id,
                thread_id=thread_id,
                pre_extracted_pages=[(1, flat_text, False)],
                provenance={"sourceType": "diagram"},
            )
        else:
            result = await ingest_document(filename, file_content, category, user_id=user_id, thread_id=thread_id)
        print(f"[OK] Background processing completed: {result}")
        if parent_id is not None:
            update = {
                "status": "processed",
                "chunkCount": result.get("chunks_created", 0),
                "processedAt": datetime.now(),
            }
            # Indexed, but only partially readable (e.g. scanned figure pages).
            # The chip shows this next to the success tick so nobody assumes the
            # assistant can see the whole document.
            if result.get("warning"):
                update["warning"] = result["warning"]
            await files_collection.update_one({"_id": parent_id}, {"$set": update})
    except (UnreadableDocumentError, EmptyDiagramError) as e:
        # Expected, explainable outcome (a scan, an image with no OCR, a
        # shapeless diagram) — not a crash. Its message is written for the
        # user, so pass it through as-is and skip the traceback.
        print(f"[INFO] Cannot read {filename}: {e}")
        if parent_id is not None:
            await files_collection.update_one(
                {"_id": parent_id},
                {"$set": {
                    "status": "failed",
                    "error": str(e),
                    "processedAt": datetime.now(),
                }},
            )
    except Exception as e:
        print(f"[ERROR] Background processing failed for {filename}: {e}")
        import traceback
        traceback.print_exc()
        if parent_id is not None:
            await files_collection.update_one(
                {"_id": parent_id},
                {"$set": {
                    "status": "failed",
                    # Internal exception text is not for the user — see
                    # _user_facing_ingest_error.
                    "error": _user_facing_ingest_error(filename, e),
                    "processedAt": datetime.now(),
                }},
            )
    finally:
        # Release the queue-depth slot reserved by the upload route (Phase 0.5).
        ingest_release()
        del file_content
        gc.collect()


async def _require_thread_upload_access(thread_id: Optional[str], current_user: User) -> None:
    """Audit F-01 (2026-08-26): ``threadId`` on /api/upload was accepted
    unchecked, so any account could park documents under any thread id and
    then generate a format document into that thread. A thread upload must
    target a thread that is the caller's own or, with CHAT_SHARING_ENABLED,
    one they have joined:

      * no threadId            -> plain user_upload, nothing to check;
      * no conversations row   -> 404 (an id is not a thread until POST
                                  /api/assistants/threads/history registers
                                  it; the frontend's ensureThread() awaits
                                  that registration BEFORE the first upload);
      * caller is the owner    -> allowed, in every flag state;
      * flag on, caller in members -> allowed;
      * anyone else            -> 403.

    Runs BEFORE the ingest slot is reserved, the body is read or the parent
    doc is inserted, so a rejected upload persists nothing. Reads only
    userId/members from the row.
    """
    if not thread_id:
        return
    conv = await conversations_collection.find_one(
        {"threadId": thread_id}, {"userId": 1, "members": 1}
    )
    if conv is None:
        print(
            f"[WARNING] Upload REJECTED: thread {thread_id} is not registered "
            f"(user {current_user.id}) (404)"
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    if conv.get("userId") == current_user.id:
        return
    if config.CHAT_SHARING_ENABLED and current_user.id in (conv.get("members") or []):
        return
    print(
        f"[WARNING] Upload REJECTED: user {current_user.id} is not the owner"
        f"{' or a member' if config.CHAT_SHARING_ENABLED else ''} of thread "
        f"{thread_id} (403)"
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to this thread.",
    )


@router.post("/upload")
@limiter.limit(RATE_LIMIT_UPLOAD, key_func=user_id_key)
@limiter.limit(RATE_LIMIT_UPLOAD_HOURLY, key_func=user_id_key)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    category: str = Form("user_upload"),
    threadId: Optional[str] = Form(None),
    sourceType: Optional[str] = Form(None),
    diagramXml: Optional[str] = Form(None),
    current_user: User = Depends(rate_limit_identify),
):
    """
    Upload a document (PDF/DOCX/XLSX/CSV/PPTX/image) for background ingestion.

    Always uses background processing so the request returns immediately —
    large Excel/PPT files can take a while to chunk + embed.

    When ``threadId`` is provided the upload is scoped to that conversation
    thread: it is stored as category "thread_upload" and tagged with the
    threadId, so it is retrievable only in THREAD_DOC mode for that thread and
    never mixed into the shared knowledge base or the user's general uploads.
    Omitting threadId preserves the existing user_upload behavior exactly.

    ``sourceType == "diagram"`` + ``diagramXml`` (both honored ONLY while
    DIAGRAM_EDITOR_ENABLED is on): the PNG is stored on the parent doc for
    display and the XML becomes the indexed text. With the flag off both
    fields are ignored entirely, so the request validates and ingests exactly
    as any other upload — byte-identical to pre-diagram behavior.
    """
    # Thread access gate (audit F-01) -- before the slot, the body and the
    # parent doc: a threadId that is not the caller's own/joined thread is
    # refused with nothing persisted. A blank threadId is a plain user_upload.
    await _require_thread_upload_access(threadId or None, current_user)

    # Queue-depth cap (Phase 0.5): reject before buffering the file when the
    # ingest backlog is already full, so a burst can't exhaust memory/CPU.
    if not ingest_try_acquire():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The server is busy processing uploads. Please try again shortly.",
        )
    handed_off = False  # becomes True once the background task owns the slot
    try:
        # Read first so we can size-check; extension is the source of truth for type.
        file_content = await file.read()
        # Diagram uploads (flag-gated) validate as PNG + XML instead of the
        # image-readability gates: their text source is the XML, not pixels,
        # so OCR/vision availability must not decide their fate. Flag off,
        # is_diagram is always False and this branch cannot be reached.
        is_diagram = config.DIAGRAM_EDITOR_ENABLED and sourceType == "diagram"
        if is_diagram:
            _validate_diagram_upload(
                file.filename, len(file_content), file_content, diagramXml
            )
        else:
            _validate_upload(file.filename, len(file_content))
            _reject_unreadable_image(file.filename)

        # Thread-scoped uploads become their own category so they never leak into
        # the shared KB / user_upload search. A blank threadId is treated as absent.
        thread_id = threadId or None
        effective_category = "thread_upload" if thread_id else category

        print(
            f" Received file: {file.filename} ({len(file_content)} bytes, "
            f"category: {effective_category}, thread: {thread_id})"
        )

        # Insert a parent file-metadata doc up front. This is what the listing
        # endpoint surfaces to the UI — without it the frontend would never
        # see the file (chunks live in the same collection but are filtered out
        # of the listing). status flips to "processed" when the background
        # task finishes.
        file_type = get_file_type(file.filename)
        parent_doc = {
            "docType": "file",
            "filename": file.filename,
            "userId": current_user.id,
            "category": effective_category,
            "bytes": len(file_content),
            "purpose": "assistants",
            "status": "processing",
            "createdAt": datetime.now(),
            "metadata": {
                "mimetype": file.content_type,
                "size": len(file_content),
                "fileType": file_type,
            },
        }
        if thread_id:
            parent_doc["threadId"] = thread_id
            parent_doc["metadata"]["threadId"] = thread_id
        if is_diagram:
            parent_doc["sourceType"] = "diagram"
            parent_doc["metadata"]["sourceType"] = "diagram"
            # PNG bytes for the human, served by GET /api/files/{id}/content
            # exactly like legacy stored uploads — NEVER sent to any model.
            parent_doc["content"] = file_content
            # The XML is the (only) extraction source; kept for provenance.
            parent_doc["diagramXml"] = diagramXml
        insert_result = await files_collection.insert_one(parent_doc)
        parent_id = insert_result.inserted_id

        # Schedule background ingestion with category (+ thread scope if any)
        background_tasks.add_task(
            process_file_ingestion,
            file.filename,
            file_content,
            effective_category,
            parent_id,
            current_user.id,
            thread_id,
            "diagram" if is_diagram else None,
            diagramXml if is_diagram else None,
        )
        # Ownership of the reserved slot passes to the background task, which
        # releases it in its finally once ingestion completes or fails.
        handed_off = True

        return {
            "success": True,
            "message": "File uploaded and processing started.",
            "filename": file.filename,
            "file_id": str(parent_id),
            "size": len(file_content),
            "status": "processing"
        }

    except HTTPException:
        raise
    except Exception as error:
        print(f"[ERROR] Error uploading file: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )
    finally:
        # If the slot was never handed to a background task (validation failed,
        # insert error, or a rejection), release it so the backlog count stays true.
        if not handed_off:
            ingest_release()


@router.get("/upload/status")
async def upload_status(
    filename: str,
    threadId: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """
    Report ingest progress for an uploaded file so the UI can keep its chip
    spinner running until embeddings actually land.

    `/api/upload` returns 200 immediately and runs extraction/OCR/chunking/
    embedding in a background task. That task flips the parent file-metadata
    doc's `status` to "processed" only AFTER ingest_document() fully completes
    (embeddings written) -- or "failed" with an `error`. So the parent doc's
    status is an accurate signal that needs no extra state and no changes to
    the ingest pipeline.

    Scoped to the authenticated user so one user cannot poll another user's
    upload status. ``threadId``, when given, narrows it further to that thread's
    upload: the same filename can be attached to several conversations, and the
    chip must reflect ITS file's progress, not a namesake in another thread.
    """
    try:
        # Latest parent (file-level) doc for this filename. Chunks are excluded
        # via chunkIndex, matching the listing endpoints.
        query = {
            "userId": current_user.id,
            "filename": filename,
            "chunkIndex": {"$exists": False},
        }
        if threadId:
            query["threadId"] = threadId
            query["category"] = "thread_upload"

        doc = await files_collection.find_one(
            query,
            {"status": 1, "error": 1, "warning": 1, "createdAt": 1},
            sort=[("createdAt", -1)],
        )

        if not doc:
            # Upload row not visible yet (race right after POST) — treat as processing.
            return {"filename": filename, "status": "processing", "stage": "processing"}

        # Lifecycle derivation (Phase 1) -- shared with the chat path's document
        # inventory. Applies the staleness rule: a doc "processing" past
        # INGEST_PENDING_TIMEOUT_SECONDS reports failed with a timeout reason,
        # so a backend restart mid-ingest can't leave the chip spinning on a
        # doc that will never finish.
        life_status, life_reason = effective_ingest_status(doc)

        if life_status == "ready":
            payload = {"filename": filename, "status": "ready", "stage": "done"}
            # Indexed, but part of the document was unreadable — surfaced next to
            # the success state, not as a failure.
            if doc.get("warning"):
                payload["warning"] = doc["warning"]
            return payload

        if life_status == "failed":
            return {
                "filename": filename,
                "status": "error",
                "stage": "error",
                "error": life_reason or "Ingestion failed",
            }

        # Still working. The background task doesn't expose fine-grained stages
        # (that would require instrumenting the ingest pipeline), so report a
        # coarse "processing" stage; the UI falls back to "Processing...".
        return {"filename": filename, "status": "processing", "stage": "processing"}

    except Exception as error:
        print(f"[ERROR] Error getting upload status for {filename}: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again.",
        )


@router.get("/assistants/files")
async def list_files(
    type: Optional[str] = None,
    category: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """
    List files from MongoDB with deduplication by filename

    Args:
        type: Legacy parameter for filtering ("user" or "knowledge_base")
        category: Direct category filter ("user_upload" or "knowledge_base")
    """
    try:
        # Resolve the requested category from the direct param or legacy `type`.
        requested = category
        if not requested:
            if type == "user":
                requested = "user_upload"
            elif type == "knowledge_base":
                requested = "knowledge_base"

        # knowledge_base is shared across all users (never userId-scoped); any
        # other category is the caller's own data.
        if requested == "knowledge_base":
            query = {"category": "knowledge_base"}
        elif requested:
            query = {"userId": current_user.id, "category": requested}
        else:
            query = {"userId": current_user.id}

        # Restrict to file-level docs (skip chunk records). Chunks have
        # chunkIndex set; parent file docs do not. We accept both the explicit
        # docType="file" marker (new uploads) and legacy parent docs that
        # simply lack chunkIndex (old /api/assistants/files POST flow).
        query["chunkIndex"] = {"$exists": False}

        # Exclude heavy fields so the listing stays small even when legacy
        # parent docs carry full text/binary/embedding payloads.
        projection = {
            "text": 0,
            "embedding": 0,
            "content": 0,
        }

        cursor = (
            files_collection
            .find(query, projection)
            .sort("createdAt", -1)
            .allow_disk_use(True)
        )

        # Use dict to deduplicate by filename (keeps latest version)
        files_by_name = {}
        async for doc in cursor:
            filename = doc.get("filename")
            # Only keep the first occurrence (most recent due to sort)
            if filename not in files_by_name:
                # Prefer the metadata.fileType written by the new ingester;
                # fall back to the filename extension so older docs still report a type.
                meta = doc.get("metadata") or {}
                file_type = meta.get("fileType") or get_file_type(filename or "")
                file_data = {
                    "file_id": str(doc["_id"]),
                    "filename": filename,
                    "purpose": doc.get("purpose", "assistants"),
                    "bytes": doc.get("bytes", 0),
                    "created_at": int(doc.get("createdAt", datetime.now()).timestamp()),
                    "status": doc.get("status", "processed"),
                    "category": doc.get("category", "knowledge_base"),
                    "fileType": file_type,
                }
                files_by_name[filename] = file_data
        
        file_list = list(files_by_name.values())
        print(f"[OK] Retrieved {len(file_list)} unique files (type={type})")
        return {"files": file_list}
        
    except Exception as error:
        print(f"[ERROR] Error listing files: {error}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


@router.post("/assistants/files")
async def upload_file(
    files: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    Legacy synchronous upload: stores the file binary + a file-level embedding.
    Now accepts every supported format via the unified extractor.
    """
    try:
        file_content = await files.read()
        original_filename = files.filename

        # Validate BEFORE any image conversion (so we reject .mp4/.zip up front).
        _validate_upload(original_filename, len(file_content))

        # Legacy TIS/TIF/TIFF -> PDF conversion (kept for back-compat with the
        # old image-as-PDF flow). For .png/.jpg/.jpeg/.tiff we now OCR directly
        # via the unified extractor, so we only convert the TIS/TIF aliases here.
        if needs_image_conversion(original_filename):
            print(f"[LOADING] Converting image to PDF: {original_filename}")
            file_content, original_filename = await convert_image_to_pdf(
                file_content,
                original_filename
            )
            print(f"[OK] Converted to PDF: {original_filename}")

        # Extract text from file (multi-format via unified extractor)
        print(f"[FILE] Extracting text from: {original_filename}")
        text_content = extract_text_from_file(file_content, original_filename)

        # Generate embeddings
        print(f" Generating embeddings...")
        embedding = await generate_embeddings(text_content)

        file_type = get_file_type(original_filename)
        # Store in MongoDB
        file_doc = {
            "docType": "file",
            "status": "processed",
            "filename": original_filename,
            "content": file_content,  # Store file content as binary
            "text": text_content,  # Store extracted text
            "embedding": embedding,  # Store 384-dim embedding
            "purpose": "assistants",
            "bytes": len(file_content),
            "userId": current_user.id,
            "category": "user_upload",
            "createdAt": datetime.now(),
            "metadata": {
                "mimetype": files.content_type,
                "size": len(file_content),
                "fileType": file_type,
            }
        }
        
        result = await files_collection.insert_one(file_doc)
        file_id = str(result.inserted_id)
        
        print(f"[OK] Uploaded file: {original_filename} (ID: {file_id})")
        print(f"   Text length: {len(text_content)} chars")
        print(f"   Embedding dimensions: {len(embedding)}")
        
        return {
            "success": True,
            "file": {
                "id": file_id,
                "filename": original_filename,
                "bytes": len(file_content),
                "created_at": int(datetime.now().timestamp())
            }
        }
        
    except HTTPException:
        raise
    except Exception as error:
        print(f"[ERROR] Error uploading file: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


@router.delete("/assistants/files")
async def delete_file(
    fileId: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a user-uploaded file and all of its chunks from MongoDB.
    Scoped to category="user_upload" — knowledge_base chunks are never
    touched, even if the parent doc somehow matched.
    """
    try:
        # Convert string ID to ObjectId
        try:
            object_id = ObjectId(fileId)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file ID format"
            )

        # Look up the parent doc first so we know its filename for chunk cleanup.
        parent = await files_collection.find_one(
            {"_id": object_id, "userId": current_user.id},
            {"filename": 1, "category": 1},
        )
        if not parent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )

        filename = parent.get("filename")
        if parent.get("category") == "knowledge_base":
            # Hard refusal — the student-facing delete endpoint must never
            # remove curated knowledge_base material.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot delete knowledge_base files from this endpoint"
            )

        # Delete the parent doc.
        parent_result = await files_collection.delete_one(
            {"_id": object_id, "userId": current_user.id}
        )

        # Delete ALL chunks for this filename, scoped to user_upload only.
        chunk_result = await files_collection.delete_many({
            "userId": current_user.id,
            "category": "user_upload",
            "$or": [{"filename": filename}, {"source": filename}],
            "chunkIndex": {"$exists": True},
        })

        total_deleted = parent_result.deleted_count + chunk_result.deleted_count
        print(
            f"[OK] Deleted file {fileId} ({filename}): "
            f"1 parent + {chunk_result.deleted_count} chunks "
            f"(total {total_deleted} docs)"
        )
        return {
            "success": True,
            "message": "File deleted successfully",
            "deleted_chunks": chunk_result.deleted_count,
        }
        
    except HTTPException:
        raise
    except Exception as error:
        print(f"[ERROR] Error deleting file: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


@router.get("/files/{file_id}")
async def get_file(file_id: str, current_user: User = Depends(get_current_user)):
    """Download a file from MongoDB (own upload or shared knowledge_base only)"""
    try:
        # Convert string ID to ObjectId
        try:
            object_id = ObjectId(file_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file ID format"
            )
        
        # Retrieve from MongoDB
        # A user may fetch a shared knowledge_base file or their OWN upload --
        # never another user's upload.
        file_doc = await files_collection.find_one(
            {"_id": object_id, "$or": [{"category": "knowledge_base"}, {"userId": current_user.id}]}
        )
        
        if not file_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )
        
        file_content = file_doc.get("content", b"")
        filename = file_doc.get("filename", "download")
        
        return StreamingResponse(
            io.BytesIO(file_content),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
        
    except HTTPException:
        raise
    except Exception as error:
        print(f"[ERROR] Error retrieving file: {error}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found."
        )


@router.delete("/files/delete/{filename:path}")
async def delete_file_by_name(filename: str, current_user: User = Depends(get_current_user)):
    """
    Delete a user-uploaded file and all of its vector chunks by filename.
    Scoped to category="user_upload" — knowledge_base material is never
    touched by this endpoint.
    """
    try:
        print(f"[DELETE] Request to delete file: {filename}")

        # Scoped delete: parent file doc + all chunks, user_upload only.
        result = await files_collection.delete_many({
            "userId": current_user.id,
            "category": "user_upload",
            "$or": [{"filename": filename}, {"source": filename}],
        })

        deleted_count = result.deleted_count
        print(f"[OK] Deleted {deleted_count} docs for user_upload file: {filename}")

        if deleted_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No user-uploaded documents found for file: {filename}"
            )

        return {
            "success": True,
            "message": f"File {filename} deleted successfully",
            "deleted_count": deleted_count,
        }
        
    except HTTPException:
        raise
    except Exception as error:
        print(f"[ERROR] Error deleting file {filename}: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


@router.get("/files/{file_id}/content")
async def get_file_content(file_id: str, current_user: User = Depends(get_current_user)):
    """View a file from MongoDB in the browser (own upload or shared knowledge_base only)"""
    try:
        print(f"[FILE] Fetching file content for: {file_id}")
        
        # Convert string ID to ObjectId
        try:
            object_id = ObjectId(file_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file ID format"
            )
        
        # Retrieve from MongoDB
        # A user may fetch a shared knowledge_base file or their OWN upload --
        # never another user's upload.
        file_doc = await files_collection.find_one(
            {"_id": object_id, "$or": [{"category": "knowledge_base"}, {"userId": current_user.id}]}
        )
        
        if not file_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )
        
        file_content = file_doc.get("content", b"")
        filename = file_doc.get("filename", "file")
        
        print(f"[OK] Retrieved file: {filename} ({len(file_content)} bytes)")
        
        media_type = determine_media_type(filename)
        print(f" Serving file as: {media_type}")
        
        return Response(
            content=file_content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Cache-Control": "public, max-age=3600"
            }
        )
        
    except HTTPException:
        raise
    except Exception as error:
        print(f"[ERROR] Error retrieving file content for {file_id}: {error}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found."
        )
