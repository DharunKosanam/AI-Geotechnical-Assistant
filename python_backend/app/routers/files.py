"""
File management endpoints
"""
from fastapi import APIRouter, HTTPException, status, File, UploadFile, Body
from fastapi.responses import StreamingResponse, Response
from datetime import datetime
from typing import Optional

from app.core.config import USER_ID, VECTOR_STORE_ID
from app.core.database import files_collection
from app.services.openai_service import get_openai_client
from app.services.file_processing import (
    convert_image_to_pdf,
    needs_image_conversion,
    determine_media_type
)

router = APIRouter(prefix="/api", tags=["files"])
openai_client = get_openai_client()


@router.get("/assistants/files")
async def list_files(type: Optional[str] = "user"):
    """List files from OpenAI"""
    try:
        mongo_files = {}
        cursor = files_collection.find({"userId": USER_ID})
        async for doc in cursor:
            file_id = doc.get("fileId")
            category = doc.get("category", "knowledge_base")
            mongo_files[file_id] = {
                "category": category,
                "filename": doc.get("filename"),
            }
        
        openai_files = await openai_client.files.list()
        
        file_list = []
        for file in openai_files.data:
            file_id = file.id
            
            if file_id in mongo_files:
                category = mongo_files[file_id]["category"]
            else:
                category = "knowledge_base"
            
            if type == "user" and category != "user_upload":
                continue
            elif type == "knowledge_base" and category != "knowledge_base":
                continue
            
            file_data = {
                "file_id": file.id,
                "filename": file.filename,
                "purpose": file.purpose,
                "bytes": file.bytes,
                "created_at": file.created_at,
                "status": getattr(file, 'status', 'processed'),
                "category": category
            }
            file_list.append(file_data)
        
        print(f"✅ Retrieved {len(file_list)} files (type={type})")
        return {"files": file_list}
        
    except Exception as error:
        print(f"❌ Error listing files: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list files: {str(error)}"
        )


@router.post("/assistants/files")
async def upload_file(files: UploadFile = File(...)):
    """Upload a file to OpenAI with image conversion support"""
    try:
        file_content = await files.read()
        original_filename = files.filename
        
        # Check if file needs conversion
        if needs_image_conversion(original_filename):
            print(f"🔄 Converting image to PDF: {original_filename}")
            file_content, original_filename = await convert_image_to_pdf(
                file_content,
                original_filename
            )
            print(f"✅ Converted to PDF: {original_filename}")
        
        # Upload to OpenAI
        openai_file = await openai_client.files.create(
            file=(original_filename, file_content),
            purpose="assistants"
        )
        
        print(f"✅ Uploaded file: {original_filename} ({openai_file.id})")
        
        # Attach file to Vector Store (CRITICAL: This ensures the AI can use the file)
        if VECTOR_STORE_ID:
            try:
                vector_file = await openai_client.beta.vector_stores.files.create(
                    vector_store_id=VECTOR_STORE_ID,
                    file_id=openai_file.id
                )
                print(f"✅ Attached file to Vector Store: {openai_file.id} → {VECTOR_STORE_ID}")
                print(f"   Status: {vector_file.status}")
            except Exception as vs_error:
                print(f"⚠️  Warning: Could not attach file to Vector Store: {vs_error}")
                # Don't fail the upload if vector store attachment fails
        else:
            print(f"⚠️  Warning: VECTOR_STORE_ID not configured. File uploaded but not attached to vector store.")
        
        # Save metadata to MongoDB
        file_doc = {
            "fileId": openai_file.id,
            "filename": original_filename,
            "purpose": "assistants",
            "bytes": len(file_content),
            "userId": USER_ID,
            "category": "user_upload",
            "createdAt": datetime.now()
        }
        await files_collection.insert_one(file_doc)
        
        return {
            "success": True,
            "file": {
                "id": openai_file.id,
                "filename": original_filename,
                "bytes": len(file_content),
                "created_at": openai_file.created_at
            }
        }
        
    except HTTPException:
        raise
    except Exception as error:
        print(f"❌ Error uploading file: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {str(error)}"
        )


@router.delete("/assistants/files")
async def delete_file(fileId: str = Body(..., embed=True)):
    """Delete a file from OpenAI, Vector Store, MongoDB, and cache"""
    try:
        # Import the cache from threads module to clear it
        from app.routers import threads
        
        # Try to remove from Vector Store first (if configured)
        if VECTOR_STORE_ID:
            try:
                # List files in vector store to find the vector store file ID
                vs_files = await openai_client.beta.vector_stores.files.list(
                    vector_store_id=VECTOR_STORE_ID
                )
                
                # Find and delete the file from vector store
                for vs_file in vs_files.data:
                    if vs_file.id == fileId:
                        await openai_client.beta.vector_stores.files.delete(
                            vector_store_id=VECTOR_STORE_ID,
                            file_id=fileId
                        )
                        print(f"✅ Removed file from Vector Store: {fileId}")
                        break
            except Exception as vs_error:
                print(f"⚠️  Warning: Could not remove file from Vector Store: {vs_error}")
                # Continue with file deletion even if vector store removal fails
        
        # Delete from OpenAI
        await openai_client.files.delete(fileId)
        
        # Delete from MongoDB
        await files_collection.delete_one({"fileId": fileId})
        
        # IMPORTANT: Clear from cache so it's not referenced anymore
        if fileId in threads._file_id_cache:
            del threads._file_id_cache[fileId]
            print(f"🗑️  Removed {fileId} from cache")
        
        print(f"✅ Deleted file: {fileId}")
        return {"success": True, "message": "File deleted successfully"}
        
    except Exception as error:
        print(f"❌ Error deleting file: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {str(error)}"
        )


@router.get("/files/{file_id}")
async def get_file(file_id: str):
    """Download a file from OpenAI"""
    try:
        file_content = await openai_client.files.content(file_id)
        file_info = await openai_client.files.retrieve(file_id)
        
        return StreamingResponse(
            iter([file_content.content]),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{file_info.filename}"'
            }
        )
        
    except Exception as error:
        print(f"❌ Error retrieving file: {error}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {str(error)}"
        )


@router.get("/files/{file_id}/content")
async def get_file_content(file_id: str):
    """View a file from OpenAI in the browser"""
    try:
        print(f"📄 Fetching file content for: {file_id}")
        
        file_content = await openai_client.files.content(file_id)
        file_info = await openai_client.files.retrieve(file_id)
        
        print(f"✅ Retrieved file: {file_info.filename} ({len(file_content.content)} bytes)")
        
        media_type = determine_media_type(file_info.filename)
        print(f"📤 Serving file as: {media_type}")
        
        return Response(
            content=file_content.content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'inline; filename="{file_info.filename}"',
                "Cache-Control": "public, max-age=3600"
            }
        )
        
    except Exception as error:
        print(f"❌ Error retrieving file content for {file_id}: {error}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {str(error)}"
        )

