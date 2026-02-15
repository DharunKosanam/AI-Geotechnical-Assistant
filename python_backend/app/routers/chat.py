"""
Chat endpoints for handling messages with simple JSON responses using Groq + RAG
"""
import asyncio
import json
import re
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from datetime import datetime
from typing import List

from models import ChatRequest, ChatResponse, RAGChatRequest, RAGChatResponse
from app.core.config import USER_ID
from app.core.database import conversations_collection, files_collection, messages_collection
from app.services.llm_service import get_llm, generate_answer_with_groq
from app.services.rag_service import query_with_context, query_vector_store
from app.services.cache_service import get_redis_client

router = APIRouter(tags=["chat"])

# In-memory storage for thread messages (used by threads.py)
_thread_messages = {}


@router.get("/chat/{thread_id}/history")
async def get_chat_history(thread_id: str):
    """
    Get chat history for a specific thread from MongoDB.
    Returns all messages sorted by timestamp (oldest first).
    """
    try:
        print(f"[HISTORY] Fetching chat history for thread: {thread_id}")
        
        # Query MongoDB for messages in this thread
        cursor = messages_collection.find({
            "threadId": thread_id,
            "userId": USER_ID
        }).sort("createdAt", 1)  # Oldest first
        
        messages = []
        async for doc in cursor:
            message = {
                "role": doc.get("role", "user"),
                "content": doc.get("content", ""),
                "sources": doc.get("sources", []),
                "createdAt": doc.get("createdAt").isoformat() if doc.get("createdAt") else None
            }
            messages.append(message)
        
        print(f"[OK] Retrieved {len(messages)} messages for thread {thread_id}")
        return {"messages": messages, "count": len(messages)}
        
    except Exception as error:
        print(f"[ERROR] Error fetching chat history: {error}")
        import traceback
        traceback.print_exc()
        return {"messages": [], "count": 0}


@router.post("/chat", response_model=RAGChatResponse)
async def chat_with_rag(request: RAGChatRequest):
    """
    Main RAG endpoint with simple JSON response.
    Returns: { "answer": "...", "sources": [...] }
    """
    try:
        print(f"[RECEIVED] Received query: {request.query}")
        
        # Extract threadId from request
        thread_id = None
        if hasattr(request, 'threadId') and request.threadId:
            thread_id = request.threadId
        elif hasattr(request, 'thread_id') and request.thread_id:
            thread_id = request.thread_id
        
        print(f"[THREAD] Thread ID: {thread_id}")
        if not thread_id:
            print("[WARNING] No threadId provided - messages will NOT be saved!")
        
        # Step 0: Check Redis cache
        cached_answer = None
        try:
            redis_client = get_redis_client()
            cached_answer = await redis_client.get_cached_answer(request.query)
            
            if cached_answer:
                print("[CACHED] Found cached answer")
                # IMPORTANT: Still save messages to DB even for cached answers
                # so chat history works when switching threads
                if thread_id:
                    try:
                        user_msg = {
                            "threadId": thread_id,
                            "userId": USER_ID,
                            "role": "user",
                            "content": request.query,
                            "createdAt": datetime.now()
                        }
                        await messages_collection.insert_one(user_msg)
                        
                        assistant_msg = {
                            "threadId": thread_id,
                            "userId": USER_ID,
                            "role": "assistant",
                            "content": cached_answer,
                            "sources": [],
                            "createdAt": datetime.now()
                        }
                        await messages_collection.insert_one(assistant_msg)
                        print(f"[SAVE] Saved cached messages to thread {thread_id}")
                    except Exception as save_err:
                        print(f"[WARNING] Failed to save cached messages: {save_err}")
                
                return RAGChatResponse(
                    answer=cached_answer,
                    sources=[]
                )
        except Exception as cache_error:
            print(f"[WARNING]  Cache check failed: {cache_error}")
        
        # Step 1: Query vector store with prioritized search
        # Note: query_vector_store now returns up to 8 results (5 user + 3 KB)
        chunks = await query_vector_store(request.query, top_k=8)
        print(f"[SEARCH] Retrieved {len(chunks)} total chunks (user uploads prioritized)")
        
        # Step 2: Format context and extract sources
        if chunks and len(chunks) > 0:
            context = "\n\n".join([
                f"[Source: {chunk['filename']}]\n{chunk['text']}"
                for chunk in chunks
            ])
            sources = list(set([chunk['filename'] for chunk in chunks]))
            print(f"   Sources: {', '.join(sources)}")
        else:
            context = ""
            sources = []
            print("   [WARNING]  No relevant chunks found")
        
        # Step 3: Generate answer with Groq
        print("[AI] Generating answer with Groq...")
        answer = await generate_answer_with_groq(
            query=request.query,
            context=context,
            history=request.history
        )
        
        print(f"   [RESULT] Answer from LLM service: {len(answer)} chars")
        
        # The answer is already cleaned in llm_service, so use it directly
        clean_answer = answer
        
        print(f"    Final answer to return ({len(clean_answer)} chars)")
        
        # Step 4: Save messages to database for history
        if thread_id:
            try:
                print(f"[SAVE] Attempting to save messages for thread {thread_id}")
                
                # Save user message
                user_message = {
                    "threadId": thread_id,
                    "userId": USER_ID,
                    "role": "user",
                    "content": request.query,
                    "createdAt": datetime.now()
                }
                user_result = await messages_collection.insert_one(user_message)
                print(f"[SAVE] User message saved with ID: {user_result.inserted_id}")
                
                # Save assistant message
                assistant_message = {
                    "threadId": thread_id,
                    "userId": USER_ID,
                    "role": "assistant",
                    "content": clean_answer,
                    "sources": sources,
                    "createdAt": datetime.now()
                }
                assistant_result = await messages_collection.insert_one(assistant_message)
                print(f"[SAVE] Assistant message saved with ID: {assistant_result.inserted_id}")
                
                print(f"[OK] Successfully saved 2 messages to MongoDB for thread {thread_id}")
            except Exception as save_error:
                print(f"[ERROR] Failed to save messages: {save_error}")
                import traceback
                traceback.print_exc()
        else:
            print("[SKIP] Not saving messages - no threadId provided")
        
        # Step 5: Cache the answer
        try:
            await redis_client.set_cached_answer(request.query, clean_answer, ttl=3600)
        except Exception as cache_error:
            print(f"[WARNING]  Failed to cache answer: {cache_error}")
        
        # Step 6: Return simple JSON response
        return RAGChatResponse(
            answer=clean_answer,
            sources=sources
        )
        
    except Exception as error:
        print(f"[ERROR] Error in chat endpoint: {error}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate answer: {str(error)}"
        )
