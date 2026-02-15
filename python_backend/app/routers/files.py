"""
File management endpoints - MongoDB storage with vector embeddings
"""
from fastapi import APIRouter, HTTPException, status, File, UploadFile, Form, Body, BackgroundTasks
from fastapi.responses import StreamingResponse, Response
from datetime import datetime
from typing import Optional, List
import io
from bson import ObjectId
from fastembed import TextEmbedding

from app.core.config import USER_ID
from app.core.database import files_collection
from app.services.file_processing import (
    convert_image_to_pdf,
    needs_image_conversion,
    determine_media_type
)
from app.services.rag_service import ingest_document, delete_document

router = APIRouter(prefix="/api", tags=["files"])

# Initialize embedding model
embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")


@router.get("/files")
async def list_files_simple(category: Optional[str] = None):
    """
    Simple files list endpoint for frontend compatibility
    
    Args:
        category: Optional filter by category ("user_upload" or "knowledge_base")
    """
    try:
        query = {"userId": USER_ID}
        
        # Add category filter if specified
        if category:
            query["category"] = category
        
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
    Extract text from file content (simple implementation)
    Can be enhanced with PDF parsing, OCR, etc.
    """
    try:
        # For text files
        if filename.endswith(('.txt', '.md', '.csv')):
            return file_content.decode('utf-8', errors='ignore')
        
        # For PDFs and other formats, you'd need additional libraries
        # For now, return empty string for binary files
        return ""
    except Exception as e:
        print(f"[WARNING]  Could not extract text from {filename}: {e}")
        return ""


async def generate_embeddings(text: str) -> List[float]:
    """Generate embeddings for text using FastEmbed"""
    if not text or not text.strip():
        return [0.0] * 384  # Return zero vector for empty text
    
    embeddings = list(embedding_model.embed([text]))
    return embeddings[0].tolist()


async def process_file_ingestion(filename: str, file_content: bytes, category: str = "user_upload"):
    """
    Background task to process file ingestion.
    This runs asynchronously so the user doesn't have to wait.
    
    Args:
        filename: Name of the file
        file_content: File content as bytes
        category: Category of the file ("user_upload" or "knowledge_base")
    """
    try:
        print(f"[LOADING] Background processing started for: {filename} (category: {category})")
        result = await ingest_document(filename, file_content, category)
        print(f"[OK] Background processing completed: {result}")
    except Exception as e:
        print(f"[ERROR] Background processing failed for {filename}: {e}")


@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    category: str = Form("user_upload")
):
    """
    Upload a PDF document for ingestion and vector embedding.
    The file is processed in the background, so this endpoint returns immediately.
    
    Args:
        file: PDF file to upload
        background_tasks: FastAPI background tasks
        category: Category of the file ("user_upload" or "knowledge_base")
        
    Returns:
        Success message with file information
    """
    try:
        # Validate file type
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only PDF files are supported. Please upload a PDF file."
            )
        
        # Read file content
        file_content = await file.read()
        
        if len(file_content) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File is empty"
            )
        
        print(f" Received file: {file.filename} ({len(file_content)} bytes, category: {category})")
        
        # Schedule background ingestion with category
        background_tasks.add_task(
            process_file_ingestion,
            file.filename,
            file_content,
            category
        )
        
        return {
            "success": True,
            "message": "File uploaded and processing started.",
            "filename": file.filename,
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


@router.get("/assistants/files")
async def list_files(type: Optional[str] = None, category: Optional[str] = None):
    """
    List files from MongoDB with deduplication by filename
    
    Args:
        type: Legacy parameter for filtering ("user" or "knowledge_base")
        category: Direct category filter ("user_upload" or "knowledge_base")
    """
    try:
        query = {"userId": USER_ID}
        
        # Support both 'category' and legacy 'type' parameter
        if category:
            query["category"] = category
        elif type == "user":
            query["category"] = "user_upload"
        elif type == "knowledge_base":
            query["category"] = "knowledge_base"
        
        # CRITICAL FIX: Exclude heavy fields and enable disk usage
        # Projection excludes: text content, embeddings (prevents 32MB memory limit)
        projection = {
            "text": 0,           # Exclude text content
            "embedding": 0,      # Exclude vector embeddings
            "content": 0         # Exclude binary file content
        }
        
        # Enable allow_disk_use to prevent QueryExceededMemoryLimitNoDiskUseAllowed error
        # This allows MongoDB to use disk for sorting large result sets (>32MB)
        cursor = (
            files_collection
            .find(query, projection)
            .sort("createdAt", -1)
            .allow_disk_use(True)  # CRITICAL: Prevents memory limit error
        )
        
        # Use dict to deduplicate by filename (keeps latest version)
        files_by_name = {}
        async for doc in cursor:
            filename = doc.get("filename")
            # Only keep the first occurrence (most recent due to sort)
            if filename not in files_by_name:
                file_data = {
                    "file_id": str(doc["_id"]),
                    "filename": filename,
                    "purpose": doc.get("purpose", "assistants"),
                    "bytes": doc.get("bytes", 0),
                    "created_at": int(doc.get("createdAt", datetime.now()).timestamp()),
                    "status": "processed",
                    "category": doc.get("category", "knowledge_base")
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
async def upload_file(files: UploadFile = File(...)):
    """Upload a file to MongoDB with vector embeddings"""
    try:
        file_content = await files.read()
        original_filename = files.filename
        
        # Check if file needs conversion
        if needs_image_conversion(original_filename):
            print(f"[LOADING] Converting image to PDF: {original_filename}")
            file_content, original_filename = await convert_image_to_pdf(
                file_content,
                original_filename
            )
            print(f"[OK] Converted to PDF: {original_filename}")
        
        # Extract text from file
        print(f"[FILE] Extracting text from: {original_filename}")
        text_content = extract_text_from_file(file_content, original_filename)
        
        # Generate embeddings
        print(f" Generating embeddings...")
        embedding = await generate_embeddings(text_content)
        
        # Store in MongoDB
        file_doc = {
            "filename": original_filename,
            "content": file_content,  # Store file content as binary
            "text": text_content,  # Store extracted text
            "embedding": embedding,  # Store 384-dim embedding
            "purpose": "assistants",
            "bytes": len(file_content),
            "userId": USER_ID,
            "category": "user_upload",
            "createdAt": datetime.now(),
            "metadata": {
                "mimetype": files.content_type,
                "size": len(file_content)
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
async def delete_file(fileId: str = Body(..., embed=True)):
    """Delete a file from MongoDB"""
    try:
        # Convert string ID to ObjectId
        try:
            object_id = ObjectId(fileId)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file ID format"
            )
        
        # Delete from MongoDB
        result = await files_collection.delete_one({"_id": object_id, "userId": USER_ID})
        
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )
        
        print(f"[OK] Deleted file: {fileId}")
        return {"success": True, "message": "File deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as error:
        print(f"[ERROR] Error deleting file: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {str(error)}"
        )


@router.get("/files/{file_id}")
async def get_file(file_id: str):
    """Download a file from MongoDB"""
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
        file_doc = await files_collection.find_one({"_id": object_id})
        
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
async def delete_file_by_name(filename: str):
    """
    Delete a file and all its vector chunks from MongoDB by filename.
    This removes ALL chunks associated with the file from the vector store.
    """
    try:
        print(f"[DELETE] Request to delete file: {filename}")
        print(f"[DELETE] Filename repr: {repr(filename)}")
        print(f"[DELETE] Filename bytes: {filename.encode('utf-8')}")
        
        # Delete all vector chunks for this file
        result = await delete_document(filename)
        
        if result["deleted_count"] == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No documents found for file: {filename}"
            )
        
        return {
            "success": True,
            "message": f"File {filename} deleted successfully",
            "deleted_count": result["deleted_count"]
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
async def get_file_content(file_id: str):
    """View a file from MongoDB in the browser"""
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
        file_doc = await files_collection.find_one({"_id": object_id})
        
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
