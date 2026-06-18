"""
File management endpoints - MongoDB storage with vector embeddings
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status, File, UploadFile, Form, Body, BackgroundTasks
from fastapi.responses import StreamingResponse, Response
from datetime import datetime
from typing import Optional, List
import io
from bson import ObjectId
from app.core.config import RATE_LIMIT_UPLOAD
from app.core.database import files_collection
from app.core.rate_limit import limiter, rate_limit_identify, user_id_key
from app.dependencies.auth import get_current_user
from app.services.file_processing import (
    convert_image_to_pdf,
    needs_image_conversion,
    determine_media_type,
    is_supported_file,
    get_file_type,
    extract_pages_from_file,
    SUPPORTED_EXTENSIONS,
)
from app.services.rag_service import ingest_document, extract_text_from_pdf, get_embedding_model
from models import User

router = APIRouter(prefix="/api", tags=["files"])

# Hard ceiling for any uploaded file. Bigger payloads must be split client-side.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# Friendly format list reused in error messages so the UI can echo it back.
SUPPORTED_LIST_LABEL = "PDF, DOCX, XLSX, XLS, CSV, PPTX, PNG, JPG, JPEG, TIFF"


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
    if not is_supported_file(filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Supported: {SUPPORTED_LIST_LABEL}",
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


@router.get("/files")
async def list_files_simple(
    category: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """
    Simple files list endpoint for frontend compatibility

    Args:
        category: Optional filter by category ("user_upload" or "knowledge_base")
    """
    try:
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


async def process_file_ingestion(
    filename: str,
    file_content: bytes,
    category: str = "user_upload",
    parent_id: Optional[ObjectId] = None,
    user_id: Optional[str] = None,
):
    """
    Background task to process file ingestion.

    If parent_id is provided, the parent file-metadata doc's status is
    updated to "processed" on success or "failed" on error, so the UI
    can stop showing "Processing..." once chunks land in Mongo.
    """
    import gc
    try:
        print(f"[LOADING] Background processing started for: {filename} (category: {category})")
        result = await ingest_document(filename, file_content, category, user_id=user_id)
        print(f"[OK] Background processing completed: {result}")
        if parent_id is not None:
            await files_collection.update_one(
                {"_id": parent_id},
                {"$set": {
                    "status": "processed",
                    "chunkCount": result.get("chunks_created", 0),
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
                    "error": str(e),
                    "processedAt": datetime.now(),
                }},
            )
    finally:
        del file_content
        gc.collect()


@router.post("/upload")
@limiter.limit(RATE_LIMIT_UPLOAD, key_func=user_id_key)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    category: str = Form("user_upload"),
    current_user: User = Depends(rate_limit_identify),
):
    """
    Upload a document (PDF/DOCX/XLSX/CSV/PPTX/image) for background ingestion.

    Always uses background processing so the request returns immediately —
    large Excel/PPT files can take a while to chunk + embed.
    """
    try:
        # Read first so we can size-check; extension is the source of truth for type.
        file_content = await file.read()
        _validate_upload(file.filename, len(file_content))

        print(f" Received file: {file.filename} ({len(file_content)} bytes, category: {category})")

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
            "category": category,
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
        insert_result = await files_collection.insert_one(parent_doc)
        parent_id = insert_result.inserted_id

        # Schedule background ingestion with category
        background_tasks.add_task(
            process_file_ingestion,
            file.filename,
            file_content,
            category,
            parent_id,
            current_user.id,
        )

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
            detail=f"Failed to upload file: {str(error)}"
        )


@router.get("/upload/status")
async def upload_status(
    filename: str,
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
    upload status.
    """
    try:
        # Latest parent (file-level) doc for this filename. Chunks are excluded
        # via chunkIndex, matching the listing endpoints.
        doc = await files_collection.find_one(
            {
                "userId": current_user.id,
                "filename": filename,
                "chunkIndex": {"$exists": False},
            },
            {"status": 1, "error": 1},
            sort=[("createdAt", -1)],
        )

        if not doc:
            # Upload row not visible yet (race right after POST) — treat as processing.
            return {"filename": filename, "status": "processing", "stage": "processing"}

        raw_status = doc.get("status", "processing")

        if raw_status == "processed":
            return {"filename": filename, "status": "ready", "stage": "done"}

        if raw_status == "failed":
            return {
                "filename": filename,
                "status": "error",
                "stage": "error",
                "error": doc.get("error") or "Ingestion failed",
            }

        # Still working. The background task doesn't expose fine-grained stages
        # (that would require instrumenting the ingest pipeline), so report a
        # coarse "processing" stage; the UI falls back to "Processing...".
        return {"filename": filename, "status": "processing", "stage": "processing"}

    except Exception as error:
        print(f"[ERROR] Error getting upload status for {filename}: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get upload status: {str(error)}",
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
            detail=f"Failed to list files: {str(error)}"
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
            detail=f"Failed to upload file: {str(error)}"
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
            detail=f"Failed to delete file: {str(error)}"
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
            detail=f"File not found: {str(error)}"
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
            detail=f"Failed to delete file: {str(error)}"
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
            detail=f"File not found: {str(error)}"
        )
