"""
Thread management endpoints
"""
from fastapi import APIRouter, HTTPException, status
from datetime import datetime
from typing import Dict

from models import (
    ThreadCreateResponse,
    ThreadHistoryResponse,
    UpdateThreadRequest,
    DeleteThreadRequest,
    CreateThreadHistoryRequest,
    TitleGenerationRequest,
    SubmitActionsRequest
)
from app.core.config import USER_ID
from app.core.database import conversations_collection
from app.services.openai_service import get_openai_client

router = APIRouter(prefix="/api/assistants/threads", tags=["threads"])
openai_client = get_openai_client()

# Cache for file ID to filename mapping (to avoid repeated API calls)
_file_id_cache: Dict[str, str] = {}


def clear_file_cache():
    """Clear the entire file ID cache"""
    global _file_id_cache
    _file_id_cache = {}
    print("🗑️  Cleared entire file cache")


def remove_from_cache(file_id: str):
    """Remove a specific file from cache"""
    if file_id in _file_id_cache:
        del _file_id_cache[file_id]
        print(f"🗑️  Removed {file_id} from cache")


@router.post("", response_model=ThreadCreateResponse)
async def create_thread():
    """Create a new OpenAI thread"""
    try:
        thread = await openai_client.beta.threads.create()
        print(f"✅ Created new thread: {thread.id}")
        return ThreadCreateResponse(threadId=thread.id)
    except Exception as error:
        print(f"❌ Error creating thread: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create thread: {str(error)}"
        )


@router.get("/history", response_model=ThreadHistoryResponse)
async def get_thread_history():
    """Get all conversation threads for the user"""
    try:
        cursor = conversations_collection.find(
            {"userId": USER_ID}
        ).sort("updatedAt", -1)
        
        threads = []
        async for doc in cursor:
            thread_data = {
                "threadId": doc.get("threadId"),
                "name": doc.get("name"),
                "isGroup": doc.get("isGroup", False),
                "createdAt": doc.get("createdAt").isoformat() if doc.get("createdAt") else None,
                "updatedAt": doc.get("updatedAt").isoformat() if doc.get("updatedAt") else None,
            }
            threads.append(thread_data)
        
        print(f"✅ Retrieved {len(threads)} threads from history")
        return ThreadHistoryResponse(threads=threads)
        
    except Exception as error:
        print(f"❌ Error fetching thread history: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch thread history: {str(error)}"
        )


@router.post("/history")
async def create_thread_history(request: CreateThreadHistoryRequest):
    """Create a new thread entry in MongoDB history"""
    try:
        conversation_doc = {
            "userId": USER_ID,
            "threadId": request.threadId,
            "name": request.name,
            "isGroup": request.isGroup,
            "createdAt": datetime.now(),
            "updatedAt": datetime.now()
        }
        
        await conversations_collection.insert_one(conversation_doc)
        print(f"✅ Created thread history entry: {request.threadId}")
        
        return {"success": True, "message": "Thread created in history"}
        
    except Exception as error:
        print(f"❌ Error creating thread history: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create thread history: {str(error)}"
        )


@router.put("/history")
async def update_thread(request: UpdateThreadRequest):
    """Update thread metadata"""
    try:
        if not request.threadId or request.threadId == "null":
            raise HTTPException(
                status_code=400,
                detail=f"Invalid threadId: '{request.threadId}'"
            )
        
        update_fields = {"updatedAt": datetime.now()}
        
        if request.newName is not None:
            update_fields["name"] = request.newName
        
        if request.isGroup is not None:
            update_fields["isGroup"] = request.isGroup
        
        result = await conversations_collection.update_one(
            {"userId": USER_ID, "threadId": request.threadId},
            {"$set": update_fields}
        )
        
        print(f"✅ Updated thread: {request.threadId}")
        return {"success": True, "message": "Thread updated successfully"}
        
    except Exception as error:
        print(f"❌ Error updating thread: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update thread: {str(error)}"
        )


@router.delete("/history")
async def delete_thread(request: DeleteThreadRequest):
    """Delete a thread from MongoDB history"""
    try:
        result = await conversations_collection.delete_one(
            {"userId": USER_ID, "threadId": request.threadId}
        )
        
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread not found"
            )
        
        print(f"✅ Deleted thread: {request.threadId}")
        return {"success": True, "message": "Thread deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as error:
        print(f"❌ Error deleting thread: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete thread: {str(error)}"
        )


@router.post("/{thread_id}/title")
async def generate_thread_title(thread_id: str, request: TitleGenerationRequest):
    """Generate a concise title for a thread"""
    try:
        message_text = request.text
        if not message_text:
            return {"title": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        
        completion = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Generate a very short (3-6 words) title for this conversation. Just return the title, nothing else."
                },
                {
                    "role": "user",
                    "content": message_text
                }
            ],
            max_tokens=20,
            temperature=0.7
        )
        
        title = completion.choices[0].message.content.strip()
        title = title.strip('"').strip("'")
        
        print(f"✅ Generated title for thread {thread_id}: {title}")
        return {"title": title}
        
    except Exception as error:
        print(f"❌ Error generating title: {error}")
        fallback_title = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"title": fallback_title}


@router.get("/{thread_id}/messages-history")
async def get_messages_history(thread_id: str):
    """Get the message history for a specific thread"""
    try:
        messages = await openai_client.beta.threads.messages.list(
            thread_id=thread_id,
            order="asc"
        )
        
        # Track file IDs that are actually referenced in this thread
        referenced_file_ids = set()
        
        message_list = []
        for msg in messages.data:
            # Process annotations to include filenames
            processed_content = []
            for content in msg.content:
                if content.type == "text" and hasattr(content, 'text'):
                    annotations = content.text.annotations if hasattr(content.text, 'annotations') else []
                    
                    enriched_annotations = []
                    for annotation in annotations:
                        annotation_dict = {
                            "type": annotation.type,
                            "text": annotation.text,
                        }
                        
                        if annotation.type == 'file_citation' and hasattr(annotation, 'file_citation'):
                            file_id = annotation.file_citation.file_id
                            annotation_dict["file_citation"] = {"file_id": file_id}
                            
                            # Track this file as referenced
                            referenced_file_ids.add(file_id)
                            
                            # Check cache first to avoid repeated API calls
                            if file_id in _file_id_cache:
                                filename = _file_id_cache[file_id]
                                annotation_dict["file_citation"]["filename"] = filename
                            else:
                                # Fetch from API and cache it
                                try:
                                    file_info = await openai_client.files.retrieve(file_id)
                                    filename = file_info.filename
                                    _file_id_cache[file_id] = filename
                                    annotation_dict["file_citation"]["filename"] = filename
                                    # Only log when fetching for the first time
                                    print(f"📎 Cached citation: {file_id} → {filename}")
                                except Exception as e:
                                    # File might have been deleted
                                    print(f"⚠️  Could not retrieve filename for {file_id}: {e}")
                                    filename = "[Deleted File]"
                                    # Don't cache deleted files
                                    annotation_dict["file_citation"]["filename"] = filename
                        
                        enriched_annotations.append(annotation_dict)
                    
                    processed_content.append({
                        "type": content.type,
                        "text": {
                            "value": content.text.value,
                            "annotations": enriched_annotations
                        }
                    })
                else:
                    processed_content.append({"type": content.type})
            
            message_data = {
                "id": msg.id,
                "role": msg.role,
                "content": processed_content,
                "created_at": msg.created_at,
                "metadata": msg.metadata
            }
            message_list.append(message_data)
        
        # Clean up cache: Remove entries for files that are no longer referenced
        # This prevents stale cache entries from deleted files
        if referenced_file_ids:
            cached_ids = set(_file_id_cache.keys())
            stale_ids = cached_ids - referenced_file_ids
            if stale_ids:
                for stale_id in stale_ids:
                    if stale_id in _file_id_cache:
                        del _file_id_cache[stale_id]
                print(f"🧹 Cleaned {len(stale_ids)} stale cache entries")
        
        print(f"✅ Retrieved {len(message_list)} messages for thread {thread_id}")
        return {"messages": message_list}
        
    except Exception as error:
        print(f"❌ Error fetching messages: {error}")
        if "No thread found" in str(error) or "404" in str(error):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread not found"
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch messages: {str(error)}"
        )


@router.get("/{thread_id}/history")
async def get_thread_messages_history(thread_id: str):
    """Alias for get_messages_history"""
    return await get_messages_history(thread_id)


@router.post("/cache/clear")
async def clear_citation_cache():
    """Clear the file citation cache (useful after deleting files)"""
    try:
        clear_file_cache()
        return {
            "success": True,
            "message": "File citation cache cleared successfully"
        }
    except Exception as error:
        print(f"❌ Error clearing cache: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear cache: {str(error)}"
        )


@router.post("/{thread_id}/actions")
async def submit_tool_actions(thread_id: str, request: SubmitActionsRequest):
    """Submit tool call outputs back to OpenAI"""
    try:
        await openai_client.beta.threads.runs.submit_tool_outputs(
            thread_id=thread_id,
            run_id=request.runId,
            tool_outputs=request.toolCallOutputs
        )
        
        print(f"✅ Submitted tool outputs for run {request.runId}")
        return {"success": True, "message": "Tool outputs submitted"}
        
    except Exception as error:
        print(f"❌ Error submitting tool outputs: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit tool outputs: {str(error)}"
        )

