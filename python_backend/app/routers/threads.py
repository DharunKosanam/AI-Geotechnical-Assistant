"""
Thread management endpoints - MongoDB-based storage
"""
from fastapi import APIRouter, HTTPException, status
from datetime import datetime
from typing import Dict
import uuid

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
from app.services.llm_service import get_llm

router = APIRouter(prefix="/api/assistants/threads", tags=["threads"])

# Import thread messages storage from chat router
from app.routers.chat import _thread_messages


@router.post("", response_model=ThreadCreateResponse)
async def create_thread():
    """Create a new thread (stored in MongoDB)"""
    try:
        # Generate a unique thread ID
        thread_id = f"thread_{uuid.uuid4().hex}"
        print(f"✅ Created new thread: {thread_id}")
        return ThreadCreateResponse(threadId=thread_id)
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
        
        # Also delete the thread messages from memory
        if request.threadId in _thread_messages:
            del _thread_messages[request.threadId]
        
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
    """Generate a concise title for a thread using Groq"""
    try:
        message_text = request.text
        if not message_text:
            return {"title": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        
        # Use Groq to generate title
        llm = get_llm()
        
        prompt = f"""Generate a very short title (3-6 words) for this conversation. 
Just return the title, nothing else.

Message: {message_text[:200]}"""
        
        response = await llm.acomplete(prompt)
        title = response.text.strip()
        
        # Clean up the title
        title = title.strip('"').strip("'").strip()
        
        # Truncate if too long
        if len(title) > 50:
            title = title[:47] + "..."
        
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
        # Get messages from in-memory storage
        if thread_id not in _thread_messages:
            return {"messages": []}
        
        messages = _thread_messages[thread_id]
        
        # Format messages for frontend
        message_list = []
        for msg in messages:
            message_data = {
                "id": msg["id"],
                "role": msg["role"],
                "content": [{
                    "type": "text",
                    "text": {
                        "value": msg["content"],
                        "annotations": []
                    }
                }],
                "created_at": msg["created_at"],
                "metadata": {}
            }
            message_list.append(message_data)
        
        print(f"✅ Retrieved {len(message_list)} messages for thread {thread_id}")
        return {"messages": message_list}
        
    except Exception as error:
        print(f"❌ Error fetching messages: {error}")
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
    """Clear cache (placeholder for compatibility)"""
    try:
        print("🗑️  Cache clear requested (no-op in current implementation)")
        return {
            "success": True,
            "message": "Cache cleared successfully"
        }
    except Exception as error:
        print(f"❌ Error clearing cache: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear cache: {str(error)}"
        )


@router.post("/{thread_id}/actions")
async def submit_tool_actions(thread_id: str, request: SubmitActionsRequest):
    """Submit tool actions (placeholder for compatibility)"""
    try:
        print(f"⚠️  Tool actions not implemented in Groq migration")
        return {"success": True, "message": "Tool outputs acknowledged"}
        
    except Exception as error:
        print(f"❌ Error submitting tool outputs: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit tool outputs: {str(error)}"
        )
