"""
Chat endpoints for handling messages with simple JSON responses using Groq + RAG
"""
import asyncio
import json
import re
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from datetime import datetime
from typing import List, Dict

from models import ChatRequest, ChatResponse, RAGChatRequest, RAGChatResponse, User
from app.core import config
from app.core.config import RATE_LIMIT_CHAT
from app.core.database import conversations_collection, files_collection, messages_collection
from app.core.rate_limit import limiter, rate_limit_identify, user_id_key
from app.dependencies.auth import get_current_user
from app.services.llm_service import get_llm, generate_answer_with_groq, rewrite_query_with_history, safe_print
from app.services.rag_service import (
    query_with_context,
    query_vector_store,
    query_thread_documents,
    thread_has_documents,
    get_clean_title,
)
from app.services.citation_filter import filter_sources_by_citations
from app.services.cache_service import get_redis_client
from app.services.intent_router import classify, KB_QUERY, GENERAL, MIXED, THREAD_DOC
from app.services.mode_handlers import handle_general, handle_thread_doc_fallback

router = APIRouter(tags=["chat"])

# In-memory storage for thread messages (used by threads.py)
_thread_messages = {}


@router.get("/chat/{thread_id}/history")
async def get_chat_history(thread_id: str, current_user: User = Depends(get_current_user)):
    """
    Get chat history for a specific thread from MongoDB.
    Returns all messages sorted by timestamp (oldest first).
    """
    try:
        print(f"[HISTORY] Fetching chat history for thread: {thread_id}")
        
        # Query MongoDB for messages in this thread
        cursor = messages_collection.find({
            "threadId": thread_id,
            "userId": current_user.id
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


async def _serve_cached_response(
    redis_client,
    cache_query: str,
    thread_id,
    user_id: str,
    query: str,
):
    """Return a cached RAGChatResponse for ``cache_query``, or None on miss/error.

    Extracted verbatim from the former inline Step 0 block so the same cache
    read + save-on-hit path can run either early (router OFF, plain key) or after
    the router assigns a mode (router ON, mode-scoped key). On a hit we still
    persist the user + assistant messages so chat history stays correct across
    thread switches. Any cache READ error is swallowed (returns None) so a cache
    problem never breaks the chat; the caller keeps ``redis_client`` for writing.
    """
    try:
        cached_answer = await redis_client.get_cached_answer(cache_query)
    except Exception as cache_error:
        print(f"[WARNING]  Cache check failed: {cache_error}")
        return None

    if not cached_answer:
        return None

    cached_text = cached_answer["answer"]
    cached_sources = cached_answer.get("sources", [])
    print(f"[CACHED] Found cached answer with {len(cached_sources)} sources")
    # IMPORTANT: Still save messages to DB even for cached answers
    # so chat history works when switching threads
    if thread_id:
        try:
            user_msg = {
                "threadId": thread_id,
                "userId": user_id,
                "role": "user",
                "content": query,
                "createdAt": datetime.now()
            }
            await messages_collection.insert_one(user_msg)

            assistant_msg = {
                "threadId": thread_id,
                "userId": user_id,
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


@router.post("/chat", response_model=RAGChatResponse)
@limiter.limit(RATE_LIMIT_CHAT, key_func=user_id_key)
async def chat_with_rag(
    request: Request,
    payload: RAGChatRequest,
    current_user: User = Depends(rate_limit_identify),
):
    """
    Main RAG endpoint with simple JSON response.
    Returns: { "answer": "...", "sources": [...] }
    """
    try:
        print(f"[RECEIVED] Received query: {payload.query}")

        # Base cache key, namespaced per user so a cached answer derived from one
        # user's private uploads can never be served to another user. When the
        # router is ENABLED the classified mode is appended below, so a query
        # cached in one mode can't serve a stale answer in another. With the
        # router OFF this stays the whole key -- byte-identical to before.
        cache_query = f"{current_user.id}:{payload.query}"

        # Extract threadId from request
        thread_id = None
        if hasattr(payload, 'threadId') and payload.threadId:
            thread_id = payload.threadId
        elif hasattr(payload, 'thread_id') and payload.thread_id:
            thread_id = payload.thread_id
        
        print(f"[THREAD] Thread ID: {thread_id}")
        if not thread_id:
            print("[WARNING] No threadId provided - messages will NOT be saved!")
        
        # Step 0: acquire Redis. When the router is OFF we also read the cache
        # here, exactly as before (plain key). When it is ON the read is deferred
        # to the router block below so the key can include the classified mode --
        # otherwise a query cached as KB_QUERY could be served for a GENERAL turn.
        redis_client = None
        try:
            redis_client = get_redis_client()
        except Exception as cache_error:
            print(f"[WARNING]  Cache check failed: {cache_error}")

        if not config.ROUTER_ENABLED and redis_client is not None:
            cached_response = await _serve_cached_response(
                redis_client, cache_query, thread_id, current_user.id, payload.query
            )
            if cached_response is not None:
                return cached_response
        
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
                    .find({"threadId": thread_id, "userId": current_user.id})
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

        # ROUTER: classify the message into a handling mode. Gated behind
        # ROUTER_ENABLED (default off). With the flag OFF, mode is forced to
        # KB_QUERY and the existing retrieval pipeline below runs byte-identically
        # to the pre-router path. Read via the config module so tests can toggle
        # it without re-import.
        #
        # Locked design: router first. GENERAL skips both the query rewrite and
        # retrieval (answers from the model alone, no citations). KB_QUERY and
        # MIXED run the shared-index retrieval pipeline below (MIXED with a blend
        # prompt). THREAD_DOC runs the SAME pipeline but retrieves ONLY this
        # thread's uploaded documents.
        if config.ROUTER_ENABLED:
            # THREAD_DOC is only possible when this thread actually has uploaded
            # documents; the router is told so it can never pick THREAD_DOC
            # otherwise (and the classifier enforces the same invariant).
            thread_has_attachments = bool(thread_id) and await thread_has_documents(
                thread_id, current_user.id
            )
            mode = await classify(
                payload.query,
                history=conversation_history,
                thread_has_attachments=thread_has_attachments,
            )
            print(f"[ROUTER] Classified mode: {mode} (attachments={thread_has_attachments})")

            # Deferred, mode-scoped cache read (router ON). The key now carries
            # the mode so a KB_QUERY answer can't be served for a GENERAL turn and
            # vice versa. The write at Step 5 uses this same mode-scoped key.
            cache_query = f"{cache_query}:{mode}"
            # THREAD_DOC answers depend on THIS thread's uploaded documents, so
            # the key must also carry the thread_id. Otherwise two threads (or a
            # deleted-and-recreated thread, which always gets a fresh id) with the
            # same query would share a THREAD_DOC answer and serve a stale/foreign
            # result. The new thread's distinct id makes its key distinct.
            if mode == THREAD_DOC and thread_id:
                cache_query = f"{cache_query}:{thread_id}"
            if redis_client is not None:
                cached_response = await _serve_cached_response(
                    redis_client, cache_query, thread_id, current_user.id, payload.query
                )
                if cached_response is not None:
                    return cached_response
        else:
            mode = KB_QUERY

        if mode == GENERAL:
            # GENERAL: no rewrite, no retrieval, no citations. Answer straight
            # from the model's own knowledge using the GENERAL system prompt. The
            # refusal-keyword guard and citation filter below are KB-only and are
            # deliberately skipped here.
            print("[ROUTER] GENERAL mode - skipping retrieval, answering from model knowledge")
            general_result = await handle_general(payload.query, conversation_history)
            clean_answer = general_result.answer
            sources = general_result.sources
            no_high_confidence_sources = general_result.no_high_confidence_sources
        else:
            # Step 1.5: Query rewrite — history-aware resolution + language
            # normalization. Vague follow-ups ("ok", "how does it differ") retrieve
            # poorly on their own, and non-English queries miss the English KB
            # entirely, so we rewrite into a standalone English query using the
            # conversation context. The rewriter is cost-aware: a plain-English first
            # message (no history) returns unchanged with no LLM call, while a
            # non-English first message is still translated. A "NONE" result means a
            # summary request — skip retrieval and answer from history alone.
            retrieval_query = payload.query
            skip_retrieval = False
            rewritten = await rewrite_query_with_history(payload.query, conversation_history)
            if rewritten == "NONE":
                skip_retrieval = True
                safe_print(f'[QUERY_REWRITE] Original: "{payload.query}" -> SKIPPED (no retrieval)')
            elif rewritten and rewritten != payload.query:
                retrieval_query = rewritten
                safe_print(f'[QUERY_REWRITE] Original: "{payload.query}" -> Rewritten: "{retrieval_query}"')
            else:
                safe_print(f'[QUERY_REWRITE] Original: "{payload.query}" -> unchanged')

            # Step 2: Query vector store using the (possibly rewritten) standalone
            # query. KB chunks and the user's uploads compete in one combined search
            # ranked purely by relevance (no upload prioritization), then reranking
            # returns the top survivors. For a summary request we skip retrieval.
            if skip_retrieval:
                chunks = []
                print("[SEARCH] Retrieval skipped for summary request - relying on conversation history")
            elif mode == THREAD_DOC:
                # Thread-scoped retrieval: ONLY this thread's uploaded documents,
                # never the shared index or another thread (isolation enforced in
                # query_thread_documents' DB filter).
                chunks = await query_thread_documents(
                    retrieval_query, thread_id, current_user.id
                )
                print(f"[SEARCH] Retrieved {len(chunks)} thread-document chunks")
            else:
                chunks = await query_vector_store(retrieval_query, top_k=8, user_id=current_user.id)
                print(f"[SEARCH] Retrieved {len(chunks)} total chunks")

            # Step 2b: Format context with academic titles and extract unique sources
            no_high_confidence_sources = False
            if chunks and len(chunks) > 0:
                # Context includes ALL chunks (high- and low-confidence) so the LLM
                # always has something to work with. Sources, below, exclude the
                # low-confidence ones.
                context = "\n\n".join([
                    f"[Source: {get_clean_title(chunk['filename'])['title']}]\n{chunk['text']}"
                    for chunk in chunks
                ])
                # When every retrieved chunk is below the reranker threshold they are
                # tagged low_confidence (see rag_service._apply_rerank_threshold):
                # the LLM still gets them as context, but we display NO sources.
                no_high_confidence_sources = all(c.get("low_confidence") for c in chunks)
                seen_titles: set[str] = set()
                sources: list[dict] = []
                for chunk in chunks:
                    if chunk.get("low_confidence"):
                        continue  # below reranker threshold: context only, not displayed
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
                        # Phase 7: provenance surfaced with the citation. Present on
                        # KB chunks (uploads + legacy backfill); None for user_upload.
                        "canonicalTitle": chunk.get("canonicalTitle"),
                        "uploader": chunk.get("uploaderName"),
                        "project": chunk.get("projectTag"),
                        "version": chunk.get("version"),
                    })
                print(f"   Sources: {', '.join(s['title'] for s in sources)}")
            else:
                context = ""
                sources = []
                print("   [WARNING]  No relevant chunks found")

            # Retrieval-confidence fallback (router ON only): retrieval RAN and
            # no chunk cleared the reranker threshold, so answer from the model's
            # own knowledge (GENERAL, no citations) instead of a low-confidence /
            # not-found KB answer.
            #
            # CRITICAL: this triggers only on a SUCCESSFUL retrieval that returned
            # no high-confidence chunk. Errors from query_vector_store above are
            # NOT caught here -- they propagate to the outer handler as a 500 -- so
            # a Mongo or reranker outage surfaces as an error and can never
            # silently produce an uncited model answer. Gated on ROUTER_ENABLED so
            # flag-off behavior is unchanged, and skipped for summary requests
            # (skip_retrieval) which answer from history by design.
            retrieval_had_high_confidence = any(
                not c.get("low_confidence") for c in chunks
            )
            fall_through_to_general = (
                config.ROUTER_ENABLED
                and not skip_retrieval
                and not retrieval_had_high_confidence
            )

            if fall_through_to_general:
                if mode == THREAD_DOC:
                    # The thread HAS an uploaded document (THREAD_DOC only fires
                    # when it does), but nothing cleared the reranker threshold.
                    # Use a thread-aware fallback so the answer acknowledges the
                    # document exists rather than implying none was uploaded.
                    print(
                        "[ROUTER] THREAD_DOC retrieval below reranker threshold - "
                        "thread-aware fallback (document exists, answer not found in it)"
                    )
                    general_result = await handle_thread_doc_fallback(
                        payload.query, conversation_history
                    )
                else:
                    print(
                        "[ROUTER] KB retrieval returned no chunk above the reranker "
                        "threshold - falling through to GENERAL (no citations)"
                    )
                    general_result = await handle_general(payload.query, conversation_history)
                clean_answer = general_result.answer
                sources = general_result.sources
                no_high_confidence_sources = general_result.no_high_confidence_sources
            else:
                # Step 3: Generate answer with Groq
                print("[AI] Generating answer with Groq...")
                answer = await generate_answer_with_groq(
                    query=payload.query,
                    context=context,
                    history=conversation_history,
                    mode=mode,
                )

                print(f"   [RESULT] Answer from LLM service: {len(answer)} chars")

                # The answer is already cleaned in llm_service, so use it directly
                clean_answer = answer

                # Clear sources if hallucination guard triggered (off-topic refusal).
                # KB_QUERY-only: GENERAL takes the branch above; MIXED is allowed
                # to answer non-geotechnical questions and blend model knowledge,
                # so it never sees this guard.
                refusal_keywords = [
                    "outside the scope",
                    "not related to geotechnical",
                    "i'm here to help with questions related to",
                    "cannot answer",
                    "don't have information",
                    "not within my expertise",
                ]
                if mode == KB_QUERY and any(kw in clean_answer.lower() for kw in refusal_keywords):
                    sources = []
                    print("[GUARD] Off-topic refusal detected - clearing sources")

                # Step 3b: Narrow displayed sources to what the LLM actually cited in its
                # formal [Source: ...] block. Retrieval/rerank/prompt are untouched; this
                # only trims the sources list so users don't see references the model
                # never used. filter_sources_by_citations falls back to all sources when
                # there is no citation block or nothing matches. Skipped when sources is
                # already empty (off-topic refusal, or the Problem 2 no-high-confidence
                # path), per the design.
                #
                # KB_QUERY-only: for MIXED the citations must cover exactly the
                # RETRIEVED chunks (the lab-document portion), never be narrowed or
                # grown by what the model wrote in prose -- the MIXED prompt tells
                # the model to attribute its own-knowledge claims inline, and those
                # claims carry no citation. So MIXED keeps the sources built from
                # retrieval in Step 2b unchanged.
                if mode == KB_QUERY and sources:
                    high_conf_chunks = [c for c in chunks if not c.get("low_confidence")]
                    cited_chunks = filter_sources_by_citations(clean_answer, high_conf_chunks)
                    cited_titles = {
                        get_clean_title(c["filename"])["title"] for c in cited_chunks
                    }
                    sources = [s for s in sources if s["title"] in cited_titles]

        print(f"    Final answer to return ({len(clean_answer)} chars)")
        
        # Step 4: Save messages to database for history
        if thread_id:
            try:
                print(f"[SAVE] Attempting to save messages for thread {thread_id}")
                
                # Save user message
                user_message = {
                    "threadId": thread_id,
                    "userId": current_user.id,
                    "role": "user",
                    "content": payload.query,
                    "createdAt": datetime.now()
                }
                user_result = await messages_collection.insert_one(user_message)
                print(f"[SAVE] User message saved with ID: {user_result.inserted_id}")
                
                # Save assistant message
                assistant_message = {
                    "threadId": thread_id,
                    "userId": current_user.id,
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
                await redis_client.set_cached_answer(cache_query, clean_answer, sources=sources, ttl=3600)
            except Exception as cache_error:
                print(f"[WARNING] Failed to cache answer: {cache_error}")
        
        # Step 6: Return simple JSON response
        return RAGChatResponse(
            answer=clean_answer,
            sources=sources,
            no_high_confidence_sources=no_high_confidence_sources,
        )
        
    except Exception as error:
        # Full error stays in the server logs; the client gets a generic message
        # so internal details (stack context, driver/DB errors) aren't leaked.
        print(f"[ERROR] Error in chat endpoint: {error}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )
