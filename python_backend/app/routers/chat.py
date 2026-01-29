"""
Chat endpoints for handling messages and streaming responses
"""
import asyncio
import json
import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from models import ChatRequest, ChatResponse
from app.core.config import ASSISTANT_ID, VECTOR_STORE_ID, OPENAI_API_KEY, USER_ID
from app.core.database import conversations_collection, files_collection
from app.services.openai_service import get_openai_client
from datetime import datetime

router = APIRouter(prefix="/chat", tags=["chat"])
openai_client = get_openai_client()


async def get_user_uploaded_files():
    """Get a list of user-uploaded files for context"""
    try:
        cursor = files_collection.find({"userId": USER_ID, "category": "user_upload"})
        files = []
        async for doc in cursor:
            files.append({
                "id": doc.get("fileId"),
                "filename": doc.get("filename")
            })
        return files
    except Exception as e:
        print(f"⚠️  Could not retrieve user files: {e}")
        return []


async def save_conversation_to_db(thread_id: str):
    """Helper function to save/update conversation in MongoDB"""
    try:
        existing_conversation = await conversations_collection.find_one({
            "userId": USER_ID,
            "threadId": thread_id
        })
        
        if not existing_conversation:
            conversation_doc = {
                "userId": USER_ID,
                "threadId": thread_id,
                "name": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "isGroup": False,
                "createdAt": datetime.now(),
                "updatedAt": datetime.now()
            }
            await conversations_collection.insert_one(conversation_doc)
            print(f"✅ Created new conversation in MongoDB: {thread_id}")
        else:
            await conversations_collection.update_one(
                {"userId": USER_ID, "threadId": thread_id},
                {"$set": {"updatedAt": datetime.now()}}
            )
            print(f"✅ Updated conversation timestamp in MongoDB: {thread_id}")
            
    except Exception as db_error:
        print(f"⚠️  MongoDB error (non-fatal): {db_error}")


@router.post("/stream")
async def send_chat_message_stream(request: ChatRequest):
    """
    Send a message to the OpenAI Assistant with streaming response.
    """
    try:
        thread_id = request.threadId
        content = request.content
        assistant_id = request.assistantId or ASSISTANT_ID
        
        # Validate inputs
        if not content or not content.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content is required"
            )
            
        if not thread_id or not thread_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Thread ID is required"
            )
            
        if not assistant_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Assistant ID is not configured"
            )
        
        print(f"📨 Received message for thread: {thread_id}")
        
        # Check for active runs and cancel them
        try:
            runs = await openai_client.beta.threads.runs.list(
                thread_id=thread_id,
                limit=5
            )
            
            active_statuses = ['in_progress', 'queued', 'requires_action']
            active_run = next(
                (run for run in runs.data if run.status in active_statuses),
                None
            )
            
            if active_run:
                print(f"⚠️  Found active run ({active_run.status}): {active_run.id}. Cancelling...")
                try:
                    await openai_client.beta.threads.runs.cancel(
                        thread_id=thread_id,
                        run_id=active_run.id
                    )
                    print(f"✅ Cancelled run {active_run.id}")
                    await asyncio.sleep(1)
                except Exception as cancel_error:
                    print(f"⚠️  Could not cancel run: {cancel_error}")
                    
        except Exception as check_error:
            print(f"⚠️  Error checking for active runs: {check_error}")
        
        # Get list of user-uploaded files to provide context
        user_files = await get_user_uploaded_files()
        
        # Add the message to the thread
        try:
            # If user has uploaded files, add context to help the AI use them
            message_content = content
            if user_files:
                file_names = [f["filename"] for f in user_files]
                file_context = f"\n\n[Available files: {', '.join(file_names)}]"
                print(f"📎 Including context about {len(user_files)} uploaded files")
                # Add file context as metadata but keep message content clean
                message_content = content
            
            await openai_client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=message_content
            )
            print("✅ Message added to thread successfully")
            
        except Exception as create_error:
            print(f"❌ Error creating message: {create_error}")
            
            if hasattr(create_error, 'status_code'):
                error_status = create_error.status_code
            elif hasattr(create_error, 'code'):
                error_status = 401 if create_error.code == 'invalid_api_key' else 500
            else:
                error_status = 500
            
            if error_status == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key"
                )
            elif error_status == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Thread not found"
                )
            elif error_status == 400:
                error_msg = str(create_error)
                if 'run' in error_msg.lower():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="AI is still processing. Please wait."
                    )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid request: {error_msg}"
                )
            else:
                raise
        
        # Save conversation async
        asyncio.create_task(save_conversation_to_db(thread_id))
        
        # Start streaming run
        async def proxy_openai_stream():
            try:
                url = f"https://api.openai.com/v1/threads/{thread_id}/runs"
                headers = {
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                    "OpenAI-Beta": "assistants=v2"
                }
                
                # Build additional instructions with file context
                additional_instructions = None
                if user_files:
                    file_list = ", ".join([f["filename"] for f in user_files])
                    additional_instructions = (
                        f"IMPORTANT: The user has uploaded the following files: {file_list}. "
                        f"Use file_search to access and reference these files when answering. "
                        f"If the question relates to any of these files, search them for relevant information."
                    )
                
                payload = {
                    "assistant_id": assistant_id,
                    "model": "gpt-4o-mini",
                    "stream": True,
                    "truncation_strategy": {
                        "type": "last_messages",
                        "last_messages": 10
                    },
                    "max_completion_tokens": 1000
                }
                
                # Add additional instructions if we have file context
                if additional_instructions:
                    payload["additional_instructions"] = additional_instructions
                
                if VECTOR_STORE_ID:
                    payload["tool_resources"] = {
                        "file_search": {
                            "vector_store_ids": [VECTOR_STORE_ID]
                        }
                    }
                
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        if response.status_code != 200:
                            error_body = await response.aread()
                            print(f"❌ OpenAI API error {response.status_code}: {error_body}")
                            yield f"data: {json.dumps({'error': error_body.decode()})}\n\n".encode()
                            return
                        
                        buffer = b""
                        async for chunk in response.aiter_raw():
                            if chunk:
                                buffer += chunk
                                while b"\n" in buffer:
                                    line, buffer = buffer.split(b"\n", 1)
                                    line_str = line.decode('utf-8', errors='ignore').strip()
                                    
                                    if line_str.startswith("data:") or line_str == "":
                                        yield (line + b"\n")
                                    elif line_str.startswith("event:"):
                                        continue
                                    else:
                                        yield (line + b"\n")
                                
            except Exception as error:
                print(f"❌ Proxy stream error: {error}")
                error_msg = f"data: {json.dumps({'error': str(error)})}\n\n"
                yield error_msg.encode()
        
        return StreamingResponse(
            proxy_openai_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
                
    except HTTPException:
        raise
    except Exception as error:
        print(f"❌ Unexpected error: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(error)}"
        )

