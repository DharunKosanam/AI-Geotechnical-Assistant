"""
FastAPI backend for AI Geotechnical Chat Application
Migrated from Next.js TypeScript to Python FastAPI

This endpoint handles chat messages by:
1. Accepting a user message and thread ID
2. Adding the message to the OpenAI thread
3. Saving the conversation to MongoDB
4. Creating a Run with the Assistant
5. Returning the run_id immediately (fire-and-forget pattern)
"""

import os
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from dotenv import load_dotenv
from openai import AsyncOpenAI
from motor.motor_asyncio import AsyncIOMotorClient

from models import (
    ChatRequest,
    ChatResponse,
    ConversationDocument,
    ErrorResponse
)

# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI(
    title="AI Geotechnical Chat API",
    description="Python FastAPI backend for OpenAI Assistant chat",
    version="1.0.0"
)

# Configure CORS - Allow frontend at localhost:3000
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize OpenAI client
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is not set in environment variables")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Initialize MongoDB client
MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise ValueError("MONGODB_URI is not set in environment variables")

mongo_client = AsyncIOMotorClient(
    MONGODB_URI,
    maxPoolSize=10,
    minPoolSize=2,
    maxIdleTimeMS=30000
)
db = mongo_client["ai-geotech-db"]
conversations_collection = db["conversations"]

# Assistant configuration
ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID", "asst_mbSUwnJtQTwDYQYESHzeiHtM")
VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID")
KNOWLEDGE_STORE_ID = os.getenv("OPENAI_KNOWLEDGE_STORE_ID")

USER_ID = "default-user"  # Hardcoded user ID to match Next.js implementation


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "ok",
        "message": "AI Geotechnical Chat API is running",
        "version": "1.0.0"
    }


@app.post("/chat/stream")
async def send_chat_message_stream(request: ChatRequest):
    """
    Send a message to the OpenAI Assistant with streaming response.
    
    This endpoint implements streaming for real-time responses:
    - Adds the user message to the thread
    - Saves conversation to MongoDB
    - Starts the assistant run with streaming
    - Returns SSE stream for real-time updates
    
    Args:
        request: ChatRequest with content, threadId, and optional assistantId
        
    Returns:
        StreamingResponse with OpenAI Assistant stream
        
    Raises:
        HTTPException: For various error conditions (401, 400, 404, 500)
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
        
        # STEP 1: Check for active runs and cancel them
        try:
            print(f"Checking for active runs on thread: {thread_id}")
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
        
        # STEP 2: Add the message to the thread
        try:
            await openai_client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=content
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
                print("🔑 API KEY INVALID OR MISSING!")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key. Please check your OPENAI_API_KEY in the .env file."
                )
            elif error_status == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Thread not found. Please create a new chat."
                )
            elif error_status == 400:
                error_msg = str(create_error)
                if 'run' in error_msg.lower():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="AI is still processing. Please wait a moment and try again."
                    )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid request to OpenAI API: {error_msg}"
                )
            else:
                raise
        
        # STEP 3: Save/Update conversation in MongoDB (async, don't wait)
        asyncio.create_task(save_conversation_to_db(thread_id))
        
        # STEP 4: Start streaming run
        try:
            print(f"Starting streaming run for thread: {thread_id}")
            
            # Note: We don't pass tool_resources to create_and_stream because:
            # 1. The Assistant already has tools configured via OpenAI Dashboard
            # 2. Vector stores are attached to the Assistant's default configuration
            # 3. The SDK version doesn't support tool_resources in create_and_stream
            # 4. Files attached to the thread/message are automatically accessible
            # 5. This approach relies on the Assistant's built-in configuration
            
            async with openai_client.beta.threads.runs.create_and_stream(
                thread_id=thread_id,
                assistant_id=assistant_id,
                model="gpt-4o-mini",
                truncation_strategy={
                    "type": "last_messages",
                    "last_messages": 10
                },
                max_completion_tokens=1000,
            ) as stream:
                
                # Convert the OpenAI stream to SSE format for frontend
                async def event_generator():
                    async for event in stream:
                        # The frontend AssistantStream expects the raw OpenAI stream format
                        if hasattr(event, 'model_dump_json'):
                            event_data = event.model_dump_json()
                            yield f"data: {event_data}\n\n"
                        elif hasattr(event, 'json'):
                            yield f"data: {event.json()}\n\n"
                        else:
                            # Fallback: send as string
                            yield f"data: {str(event)}\n\n"
                
                return StreamingResponse(
                    event_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",  # Disable nginx buffering
                    }
                )
            
        except Exception as run_error:
            print(f"❌ Error creating run: {run_error}")
            
            if hasattr(run_error, 'status_code'):
                error_status = run_error.status_code
            elif hasattr(run_error, 'code'):
                error_status = 401 if run_error.code == 'invalid_api_key' else 500
            else:
                error_status = 500
            
            if error_status == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key. Please check your OPENAI_API_KEY."
                )
            elif error_status == 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid request to OpenAI API: {run_error}"
                )
            else:
                raise
                
    except HTTPException:
        raise
    except Exception as error:
        print(f"❌ Unexpected error in /chat/stream endpoint: {error}")
        print(f"Error type: {type(error).__name__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(error)}"
        )


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


@app.post("/chat", response_model=ChatResponse)
async def send_chat_message(request: ChatRequest):
    """
    Send a message to the OpenAI Assistant and save to MongoDB.
    
    This endpoint implements the "fire-and-forget" pattern:
    - Adds the user message to the thread
    - Saves conversation to MongoDB
    - Starts the assistant run
    - Returns immediately with the run_id (doesn't wait for completion)
    
    Args:
        request: ChatRequest with content, threadId, and optional assistantId
        
    Returns:
        ChatResponse with run_id and thread_id
        
    Raises:
        HTTPException: For various error conditions (401, 400, 404, 500)
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
        
        # STEP 1: Check for active runs and cancel them to prevent race condition
        try:
            print(f"Checking for active runs on thread: {thread_id}")
            runs = await openai_client.beta.threads.runs.list(
                thread_id=thread_id,
                limit=5
            )
            
            # Find any active runs
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
                    # Wait 1 second for cancellation to process
                    await asyncio.sleep(1)
                except Exception as cancel_error:
                    print(f"⚠️  Could not cancel run: {cancel_error}")
                    # Continue anyway - the run might have completed naturally
                    
        except Exception as check_error:
            print(f"⚠️  Error checking for active runs: {check_error}")
            # Continue anyway - better to try than to fail completely
        
        # STEP 2: Add the message to the thread
        try:
            await openai_client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=content
            )
            print("✅ Message added to thread successfully")
            
        except Exception as create_error:
            print(f"❌ Error creating message: {create_error}")
            
            # Handle specific error types
            if hasattr(create_error, 'status_code'):
                error_status = create_error.status_code
            elif hasattr(create_error, 'code'):
                error_status = 401 if create_error.code == 'invalid_api_key' else 500
            else:
                error_status = 500
            
            if error_status == 401:
                print("🔑 API KEY INVALID OR MISSING!")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key. Please check your OPENAI_API_KEY in the .env file."
                )
            elif error_status == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Thread not found. Please create a new chat."
                )
            elif error_status == 400:
                error_msg = str(create_error)
                if 'run' in error_msg.lower():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="AI is still processing. Please wait a moment and try again."
                    )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid request to OpenAI API: {error_msg}"
                )
            else:
                raise
        
        # STEP 3: Save/Update conversation in MongoDB
        try:
            existing_conversation = await conversations_collection.find_one({
                "userId": USER_ID,
                "threadId": thread_id
            })
            
            if not existing_conversation:
                # Create new conversation document
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
                # Update existing conversation timestamp
                await conversations_collection.update_one(
                    {"userId": USER_ID, "threadId": thread_id},
                    {"$set": {"updatedAt": datetime.now()}}
                )
                print(f"✅ Updated conversation timestamp in MongoDB: {thread_id}")
                
        except Exception as db_error:
            print(f"⚠️  MongoDB error (non-fatal): {db_error}")
            # Don't fail the request if MongoDB fails - continue with OpenAI
        
        # STEP 4: Create the run configuration
        run_config = {
            "assistant_id": assistant_id,
            # Truncate history to save tokens (only keep last 10 messages)
            "truncation_strategy": {
                "type": "last_messages",
                "last_messages": 10
            },
            # Limit response length to prevent expensive run-on answers
            "max_completion_tokens": 1000,
            # Use faster, cheaper model
            "model": "gpt-4o-mini"
        }
        
        # Add user vector store if configured
        if VECTOR_STORE_ID:
            print(f"Attaching user vector store: {VECTOR_STORE_ID}")
            run_config["tool_resources"] = {
                "file_search": {
                    "vector_store_ids": [VECTOR_STORE_ID]
                }
            }
        
        # STEP 5: Start the run (fire-and-forget)
        try:
            print(f"Starting run for thread: {thread_id}")
            run = await openai_client.beta.threads.runs.create(
                thread_id=thread_id,
                **run_config
            )
            print(f"✅ Run created: {run.id}")
            
            # Return immediately with the run_id (don't wait for completion)
            return ChatResponse(
                success=True,
                run_id=run.id,
                thread_id=thread_id,
                message="Message sent successfully"
            )
            
        except Exception as run_error:
            print(f"❌ Error creating run: {run_error}")
            
            if hasattr(run_error, 'status_code'):
                error_status = run_error.status_code
            elif hasattr(run_error, 'code'):
                error_status = 401 if run_error.code == 'invalid_api_key' else 500
            else:
                error_status = 500
            
            if error_status == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key. Please check your OPENAI_API_KEY."
                )
            elif error_status == 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid request to OpenAI API: {run_error}"
                )
            else:
                raise
                
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as error:
        print(f"❌ Unexpected error in /chat endpoint: {error}")
        print(f"Error type: {type(error).__name__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(error)}"
        )


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown"""
    mongo_client.close()
    print("🔌 MongoDB connection closed")


if __name__ == "__main__":
    import uvicorn
    
    # Run the server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # Enable auto-reload during development
        log_level="info"
    )

