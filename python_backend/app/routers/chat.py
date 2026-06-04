"""
Chat endpoints for handling messages with simple JSON responses using Groq + RAG
"""
import asyncio
import json
import re
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from datetime import datetime
from typing import List, Dict

from models import ChatRequest, ChatResponse, RAGChatRequest, RAGChatResponse
from app.core.config import USER_ID
from app.core.database import conversations_collection, files_collection, messages_collection
from app.services.llm_service import get_llm, generate_answer_with_groq, rewrite_query_with_history
from app.services.rag_service import query_with_context, query_vector_store, get_clean_title
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
        redis_client = None
        cached_answer = None
        try:
            redis_client = get_redis_client()
            cached_answer = await redis_client.get_cached_answer(request.query)
            
            if cached_answer:
                cached_text = cached_answer["answer"]
                cached_sources = cached_answer.get("sources", [])
                print(f"[CACHED] Found cached answer with {len(cached_sources)} sources")
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
                            "content": cached_text,
                            "sources": cached_sources,
                            "createdAt": datetime.now()
                        }
                        await messages_collection.insert_one(assistant_msg)
                        print(f"[SAVE] Saved cached messages to thread {thread_id}")
                    except Exception as save_err:
                        print(f"[WARNING] Failed to save cached messages: {save_err}")
                
                return RAGChatResponse(
                    answer=cached_text,
                    sources=cached_sources
                )
        except Exception as cache_error:
            print(f"[WARNING]  Cache check failed: {cache_error}")
        
        # Step 1: Load prior conversation turns for this thread so follow-up
        # questions ("ok", "summarize everything") have context. History lives in
        # MongoDB; the current user turn is saved later, so it is not yet in the
        # DB and won't be duplicated here. (request.history is ignored — the
        # frontend sends []; the DB is the source of truth.)
        conversation_history: List[Dict[str, str]] = []
        if thread_id:
            try:
                # Pull the most recent 20 messages (newest first), then restore
                # chronological order for the prompt.
                cursor = (
                    messages_collection
                    .find({"threadId": thread_id, "userId": USER_ID})
                    .sort("createdAt", -1)
                    .limit(20)
                )
                loaded: List[Dict[str, str]] = []
                async for doc in cursor:
                    content = doc.get("content", "")
                    if content:
                        loaded.append({"role": doc.get("role", "user"), "content": content})
                loaded.reverse()  # oldest -> newest

                # Token-budget guard: rough len//4 heuristic. Drop the OLDEST
                # turns until under the cap, always keeping the most recent ones.
                HISTORY_TOKEN_CAP = 6000

                def _est_tokens(msgs: List[Dict[str, str]]) -> int:
                    return sum(len(m["content"]) // 4 for m in msgs)

                dropped = 0
                while len(loaded) > 1 and _est_tokens(loaded) > HISTORY_TOKEN_CAP:
                    loaded.pop(0)
                    dropped += 1

                conversation_history = loaded
                print(
                    f"[HISTORY] Loaded {len(conversation_history)} prior messages for "
                    f"thread {thread_id} (~{_est_tokens(conversation_history)} tokens; "
                    f"dropped {dropped} oldest over {HISTORY_TOKEN_CAP}-token cap)"
                )
            except Exception as hist_err:
                print(f"[WARNING] Failed to load conversation history: {hist_err}")
                conversation_history = []
        else:
            print("[HISTORY] No thread_id provided — sending empty history")

        # Step 1.5: History-aware query rewrite. Vague follow-ups ("ok",
        # "how does it differ") retrieve poorly on their own, so rewrite them into
        # a standalone query using the conversation context. With no history we
        # keep the raw message (no extra LLM call). A "NONE" result means a
        # summary request — skip retrieval and answer from history alone.
        retrieval_query = request.query
        skip_retrieval = False
        if conversation_history:
            rewritten = await rewrite_query_with_history(request.query, conversation_history)
            if rewritten == "NONE":
                skip_retrieval = True
                print(f'[QUERY_REWRITE] Original: "{request.query}" -> SKIPPED (no retrieval)')
            elif rewritten and rewritten != request.query:
                retrieval_query = rewritten
                print(f'[QUERY_REWRITE] Original: "{request.query}" -> Rewritten: "{retrieval_query}"')
            else:
                print(f'[QUERY_REWRITE] Original: "{request.query}" -> unchanged')

        # Step 2: Query vector store with prioritized search using the (possibly
        # rewritten) standalone query. query_vector_store returns up to 8 results
        # (5 user + 3 KB). For a summary request we skip retrieval entirely.
        if skip_retrieval:
            chunks = []
            print("[SEARCH] Retrieval skipped for summary request - relying on conversation history")
        else:
            chunks = await query_vector_store(retrieval_query, top_k=8)
            print(f"[SEARCH] Retrieved {len(chunks)} total chunks (user uploads prioritized)")
        
        # Step 2b: Format context with academic titles and extract unique sources
        if chunks and len(chunks) > 0:
            context = "\n\n".join([
                f"[Source: {get_clean_title(chunk['filename'])['title']}]\n{chunk['text']}"
                for chunk in chunks
            ])
            seen_titles: set[str] = set()
            sources: list[dict] = []
            for chunk in chunks:
                info = get_clean_title(chunk["filename"])
                if info["title"] in seen_titles:
                    continue
                seen_titles.add(info["title"])
                # Expose fileType so the UI can render the right icon next to
                # each citation (PDF vs Word vs Excel vs slide vs OCR'd image).
                meta = chunk.get("metadata") or {}
                file_type = meta.get("fileType")
                if not file_type:
                    fn = chunk.get("filename", "")
                    file_type = ("." + fn.rsplit(".", 1)[-1].lower()) if "." in fn else ""
                sources.append({
                    "title": info["title"],
                    "url": info["url"],
                    "filename": chunk.get("filename"),
                    "fileType": file_type,
                })
            print(f"   Sources: {', '.join(s['title'] for s in sources)}")
        else:
            context = ""
            sources = []
            print("   [WARNING]  No relevant chunks found")

        # Step 3: Generate answer with Groq
        print("[AI] Generating answer with Groq...")
        answer = await generate_answer_with_groq(
            query=request.query,
            context=context,
            history=conversation_history
        )
        
        print(f"   [RESULT] Answer from LLM service: {len(answer)} chars")
        
        # The answer is already cleaned in llm_service, so use it directly
        clean_answer = answer
        
        # Clear sources if hallucination guard triggered (off-topic refusal)
        refusal_keywords = [
            "outside the scope",
            "not related to geotechnical",
            "i'm here to help with questions related to",
            "cannot answer",
            "don't have information",
            "not within my expertise",
        ]
        if any(kw in clean_answer.lower() for kw in refusal_keywords):
            sources = []
            print("[GUARD] Off-topic refusal detected - clearing sources")
        
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
        
        # Step 5: Cache the answer (skip if Redis unavailable)
        if redis_client:
            try:
                await redis_client.set_cached_answer(request.query, clean_answer, sources=sources, ttl=3600)
            except Exception as cache_error:
                print(f"[WARNING] Failed to cache answer: {cache_error}")
        
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
