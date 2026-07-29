"""
Chat endpoints for handling messages with simple JSON responses using Groq + RAG
"""
import asyncio
import json
import re
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from datetime import datetime
from typing import List, Dict, Optional

from models import ChatRequest, ChatResponse, RAGChatRequest, RAGChatResponse, User
from app.core import config
from app.core.config import RATE_LIMIT_CHAT
from app.core.database import conversations_collection, files_collection, messages_collection
from app.core.rate_limit import limiter, rate_limit_identify, user_id_key
from app.dependencies.auth import get_current_user
from app.services.llm_service import (
    TokenEmitter,
    get_llm,
    generate_answer_with_groq,
    rewrite_query_with_history,
    safe_print,
)
from app.services.rag_service import (
    query_with_context,
    query_vector_store,
    query_thread_documents,
    thread_document_inventory,
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


def _mode_cache_key(
    base: str,
    mode: str,
    thread_id: Optional[str],
    doc_fingerprint: str,
) -> str:
    """Assemble the mode-scoped cache key (router ON path).

    KB_QUERY / GENERAL / MIXED: ``{base}:{mode}`` -- byte-identical to before.
    THREAD_DOC additionally carries the thread id AND the document-set
    fingerprint (Phase 2), so the key changes whenever a document is added,
    removed, or re-ingested. Nothing needs to remember to bust the cache: any
    path that mutates the document set changes the fingerprint, and with it the
    key -- so a cached answer (including its Phase 4 scope note, which names
    the document set) can only ever be served while the set it was computed
    for is still the thread's current set. An empty fingerprint (defensive;
    THREAD_DOC is unreachable for a documentless thread) keeps the old key.
    """
    key = f"{base}:{mode}"
    if mode == THREAD_DOC and thread_id:
        key = f"{key}:{thread_id}"
        if doc_fingerprint:
            key = f"{key}:{doc_fingerprint}"
    return key


def _join_names(names: List[str]) -> str:
    """'a.pdf' / 'a.pdf and b.pdf' / 'a.pdf, b.pdf and c.pdf' (plain ASCII)."""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _thread_scope_note(scope: Dict) -> str:
    """Deterministic retrieval-scope statement for a multi-document THREAD_DOC
    answer (Phase 4). Assembled from query_thread_documents' scope_out --
    structured fact from the actual retrieval -- and appended to the answer
    server-side, so the model cannot invent, omit, or contradict which files
    were searched. Returns "" for single-document threads (no scope preamble,
    today's behavior) and whenever the scope is not fully known.
    """
    searched = scope.get("searched") or []
    if len(searched) < 2 or "grounded" not in scope:
        return ""
    grounded = scope.get("grounded") or []
    no_relevant = [fn for fn in (scope.get("no_relevant") or []) if fn not in grounded]
    excluded = [
        fn for fn in (scope.get("excluded") or [])
        if fn not in grounded and fn not in no_relevant
    ]

    parts = [f"Searched {len(searched)} attached documents: {_join_names(searched)}."]
    if grounded:
        parts.append(f"This answer is grounded in {_join_names(grounded)}.")
        if no_relevant:
            # Searched-and-empty is the CORRECT outcome for a document with
            # nothing above the rerank threshold -- name it, don't omit it.
            parts.append(
                f"No content relevant to this question was found above the "
                f"confidence threshold in {_join_names(no_relevant)}."
            )
    else:
        parts.append(
            "No content relevant to this question was found above the "
            "confidence threshold in any of them."
        )
    if excluded:
        # Searched AND matched, but no context slot (more passing documents
        # than slots) -- distinct from searched-and-empty above.
        parts.append(
            f"{_join_names(excluded)} matched this question but was not "
            f"included in the context for this answer."
        )
    return "_" + " ".join(parts) + "_"


async def _run_chat_turn(
    payload: RAGChatRequest,
    current_user: User,
    emit: Optional[TokenEmitter] = None,
) -> RAGChatResponse:
    """Run one complete chat turn and return the response model.

    This is the ENTIRE body of the former POST /chat handler, moved verbatim so
    the JSON endpoint and the SSE endpoint execute the same code in the same
    order — retrieval, routing, generation, the refusal guard, citation
    narrowing, persistence and the cache write are defined exactly once. POST
    /chat is now a thin wrapper that awaits this and returns the result.

    ``emit``, when supplied by the SSE endpoint, is handed to the answer-
    generating call so tokens surface as they are produced. It changes ONLY how
    the answer text is delivered — never what it is, and never the ordering
    around it. With ``emit=None``, which is every non-streaming caller including
    POST /chat, this is byte-for-byte the pre-streaming path.

    Raises the same HTTPException(500) as before on failure, so /chat's error
    contract is unchanged; the SSE caller catches it and reports it as an event
    (it cannot change the status code once the stream has started).
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
            # thread_document_inventory answers has-attachments AND yields the
            # document-set fingerprint for the cache key from ONE query -- it
            # replaces the former thread_has_documents call here rather than
            # adding a second ~70ms Atlas round trip to the read path (Phase 2).
            thread_has_attachments, thread_doc_fp = await thread_document_inventory(
                thread_id, current_user.id
            )
            mode = await classify(
                payload.query,
                history=conversation_history,
                thread_has_attachments=thread_has_attachments,
            )
            print(f"[ROUTER] Classified mode: {mode} (attachments={thread_has_attachments})")

            # Deferred, mode-scoped cache read (router ON). The key carries the
            # mode so a KB_QUERY answer can't be served for a GENERAL turn; a
            # THREAD_DOC key additionally carries the thread id (a deleted-and-
            # recreated thread gets a fresh id) and the document-set fingerprint
            # (Phase 2: an upload/delete/re-ingest changes the key, so a stale
            # answer -- and its scope note naming the old document set -- can
            # never be served). The write at Step 5 uses this same key.
            cache_query = _mode_cache_key(cache_query, mode, thread_id, thread_doc_fp)
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
            general_result = await handle_general(
                payload.query, conversation_history, emit=emit
            )
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
            # Retrieval scope for THREAD_DOC (Phase 4): filled by
            # query_thread_documents with which documents were searched /
            # grounded the context / were searched-but-empty. Stays empty for
            # every other mode and for skipped retrieval, which disables the
            # scope statement below.
            thread_scope: Dict = {}
            if skip_retrieval:
                chunks = []
                print("[SEARCH] Retrieval skipped for summary request - relying on conversation history")
            elif mode == THREAD_DOC:
                # Thread-scoped retrieval: ONLY this thread's uploaded documents,
                # never the shared index or another thread (isolation enforced in
                # query_thread_documents' DB filter).
                chunks = await query_thread_documents(
                    retrieval_query, thread_id, current_user.id,
                    scope_out=thread_scope,
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
                # Phase 4: for a multi-document THREAD_DOC turn, hand the model
                # the retrieval scope as structured fact so it can attribute
                # correctly and never imply an absent document was read. The
                # user-facing scope statement is appended deterministically
                # below regardless of what the model does with this.
                if mode == THREAD_DOC and len(thread_scope.get("searched", [])) > 1:
                    scope_lines = (
                        f"[ATTACHED DOCUMENTS: {', '.join(thread_scope['searched'])}]\n"
                        f"[CONTEXT DRAWN FROM: "
                        f"{', '.join(thread_scope.get('grounded') or []) or 'none above the confidence threshold'}]"
                    )
                    context = f"{scope_lines}\n\n{context}"
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
                        payload.query, conversation_history, emit=emit
                    )
                else:
                    print(
                        "[ROUTER] KB retrieval returned no chunk above the reranker "
                        "threshold - falling through to GENERAL (no citations)"
                    )
                    general_result = await handle_general(
                        payload.query, conversation_history, emit=emit
                    )
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
                    emit=emit,
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

            # Phase 4: multi-document THREAD_DOC turns state their retrieval
            # scope -- which attached documents were searched, which ground the
            # answer, and which were searched but held nothing above the
            # threshold. Appended deterministically from the retrieval facts
            # (never the model's account), BEFORE persistence and the cache
            # write so the user, the thread history, and the cache all carry
            # the same text. Single-document threads get no note ("" from the
            # builder) and read exactly as before; the note covers both the
            # grounded branch and the THREAD_DOC confidence fallback above.
            if mode == THREAD_DOC:
                scope_note = _thread_scope_note(thread_scope)
                if scope_note:
                    clean_answer = f"{clean_answer}\n\n{scope_note}"

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
    return await _run_chat_turn(payload, current_user)


# ---------------------------------------------------------------------------
# SSE streaming variant (Phase 2, layer 1) — additive, flag-gated
# ---------------------------------------------------------------------------
# Wire format. One event per line-pair, payload always JSON (so a newline inside
# an answer can never be mistaken for an SSE record separator):
#
#   event: start   data: {"status":"thinking"}      once, immediately
#   event: token   data: {"text":"..."}             one or more; concatenate in order
#   event: done    data: {"sources":[...],"no_high_confidence_sources":false}
#   event: error   data: {"detail":"..."}           terminal, replaces `done`
#
# `sources` can only be emitted at the END: the final list depends on the
# finished answer (the refusal guard clears it, and citation narrowing trims it
# to what the model actually cited). That is a property of the existing pipeline,
# not of streaming, and it is preserved exactly.
#
# Tokens are produced deep inside the turn (llm_service), which cannot `yield`
# into this generator, so they travel over a queue: the turn task puts text on
# it, this generator takes text off it and frames it as SSE.
SSE_HEARTBEAT_SECONDS = 15


def _sse(event: str, data: Dict) -> str:
    """Format one SSE event. ensure_ascii=False keeps γ/φ/° intact."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sse_chat_turn(payload: RAGChatRequest, current_user: User):
    """Async generator producing the SSE event stream for one chat turn."""
    # Flush headers and prove liveness before any slow work starts — this is
    # what turns a blank 3-minute wait into a connection the client can see.
    yield _sse("start", {"status": "thinking"})

    tokens: "asyncio.Queue[str]" = asyncio.Queue()

    async def emit(text: str) -> None:
        await tokens.put(text)

    # The turn runs as its own Task so that a client disconnect can CANCEL it.
    # Awaiting the coroutine directly would not do this: when the client goes
    # away Starlette cancels the response task, and an un-owned coroutine would
    # be left running — Ollama would generate an answer for a browser that is no
    # longer there, holding the GPU that everyone else is queued behind.
    turn = asyncio.create_task(_run_chat_turn(payload, current_user, emit=emit))
    streamed = ""
    getter: Optional[asyncio.Task] = None
    try:
        # Pump: forward tokens as they appear, heartbeat when neither a token
        # nor the turn has produced anything for a while. SSE comments (": ...")
        # are ignored by clients but keep every proxy in the chain from calling
        # the connection idle — the 240s/300s read timeouts only measure the gap
        # BETWEEN reads, so a byte every 15s means a slow answer can no longer
        # 504 no matter how long it takes.
        while True:
            if getter is None:
                getter = asyncio.create_task(tokens.get())
            done, _ = await asyncio.wait(
                {getter, turn},
                timeout=SSE_HEARTBEAT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if getter in done:
                text = getter.result()
                getter = None
                streamed += text
                yield _sse("token", {"text": text})
                continue
            if turn in done:
                break
            yield ": keep-alive\n\n"

        # The turn is finished but tokens it emitted last may still be queued.
        while not tokens.empty():
            text = tokens.get_nowait()
            streamed += text
            yield _sse("token", {"text": text})

        result = turn.result()

        # Reconcile. The streamed text IS the answer for a normal generation, but
        # two paths legitimately produce an answer without streaming it: a Redis
        # cache hit (returns before any LLM call) and the Groq provider. Emit
        # whatever the client has not already received so the message on screen
        # always equals result.answer — the same text that was persisted.
        if result.answer != streamed:
            if result.answer.startswith(streamed):
                yield _sse("token", {"text": result.answer[len(streamed):]})
            else:
                # Cannot happen for the paths above; if it ever did, the text on
                # screen is what the user read — log and leave it alone rather
                # than appending a contradictory second copy.
                print(
                    f"[STREAM] Answer/stream mismatch "
                    f"(streamed {len(streamed)} chars, final {len(result.answer)}) — "
                    f"keeping the streamed text"
                )

        yield _sse(
            "done",
            {
                "sources": result.sources,
                "no_high_confidence_sources": result.no_high_confidence_sources,
            },
        )
    except HTTPException as http_error:
        # The status line is long gone (we sent 200 with the first event), so a
        # failure has to arrive as an event. Same message the JSON endpoint
        # would have put in its body.
        yield _sse("error", {"detail": http_error.detail})
    except asyncio.CancelledError:
        # Client disconnected. Nothing to send; the finally below stops the work.
        print("[STREAM] Client disconnected — cancelling in-flight chat turn")
        raise
    except Exception as error:
        print(f"[ERROR] Streaming chat turn failed: {error}")
        import traceback
        traceback.print_exc()
        yield _sse("error", {"detail": "An internal error occurred, please try again."})
    finally:
        # Runs on EVERY exit path, including the generator being closed when the
        # browser goes away. Cancelling propagates into the Ollama call, whose
        # `finally` closes the HTTP connection, which is what makes Ollama drop
        # the generation instead of finishing it for nobody.
        if getter is not None and not getter.done():
            getter.cancel()
        if not turn.done():
            turn.cancel()
            try:
                await turn
            except asyncio.CancelledError:
                pass
            except Exception as cleanup_error:
                print(f"[STREAM] Cancelled turn raised on teardown: {cleanup_error}")


@router.post("/chat/stream")
@limiter.limit(RATE_LIMIT_CHAT, key_func=user_id_key)
async def chat_with_rag_stream(
    request: Request,
    payload: RAGChatRequest,
    current_user: User = Depends(rate_limit_identify),
):
    """
    SSE variant of POST /chat. Same turn, same answer, same sources — delivered
    as a stream so the browser can render progress instead of waiting on a blank
    screen, and so the connection never sits silent long enough to be timed out.

    Self-gating: with STREAMING_ENABLED off this 404s, exactly as if the route
    did not exist, and POST /chat is untouched.
    """
    if not config.STREAMING_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not Found",
        )

    return StreamingResponse(
        _sse_chat_turn(payload, current_user),
        media_type="text/event-stream",
        headers={
            # nginx buffers proxied responses by default, which would hold the
            # whole stream back and defeat the entire feature. nginx honours this
            # header, so no nginx config change is needed.
            "X-Accel-Buffering": "no",
            # no-transform additionally tells intermediaries not to re-chunk or
            # compress the stream.
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        },
    )
