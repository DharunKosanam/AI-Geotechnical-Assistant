"""
Thread management endpoints - MongoDB-based storage
"""
from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime
from typing import Dict
import time
import uuid

from models import (
    ThreadCreateResponse,
    ThreadHistoryResponse,
    UpdateThreadRequest,
    DeleteThreadRequest,
    CreateThreadHistoryRequest,
    TitleGenerationRequest,
    SubmitActionsRequest,
    User,
)
from pydantic import BaseModel

from app.core import config
from app.core.database import (
    conversations_collection,
    messages_collection,
    files_collection,
    highlights_collection,
)
from app.dependencies.auth import get_current_user
from app.services.llm_service import get_llm
from app.services.rag_service import effective_ingest_status

router = APIRouter(prefix="/api/assistants/threads", tags=["threads"])

# Import thread messages storage from chat router
from app.routers.chat import _thread_messages


@router.post("", response_model=ThreadCreateResponse)
async def create_thread(current_user: User = Depends(get_current_user)):
    """Create a new thread (stored in MongoDB)"""
    try:
        # Generate a unique thread ID
        thread_id = f"thread_{uuid.uuid4().hex}"
        print(f"[OK] Created new thread: {thread_id}")
        return ThreadCreateResponse(threadId=thread_id)
    except Exception as error:
        print(f"[ERROR] Error creating thread: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


@router.get("/history")
async def list_threads(current_user: User = Depends(get_current_user)):
    """Get all conversation threads for the user"""
    try:
        print(f"[LIST] Fetching thread history for user: {current_user.id}")
        cursor = conversations_collection.find(
            {"userId": current_user.id}
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
            if config.CHAT_SHARING_ENABLED:
                # Flag-on extras only — the flag-off payload keys are pinned
                # byte-identical by test_chat_sharing (members is stored data,
                # never echoed flag-off).
                thread_data["shared"] = False
                thread_data["memberCount"] = len(doc.get("members") or [doc.get("userId")])
            threads.append(thread_data)

        if config.CHAT_SHARING_ENABLED:
            # Lab shared = threads the caller JOINED: member, not owner. A
            # second query, never a widening of the caller-scoped one above.
            shared_cursor = conversations_collection.find(
                {"members": current_user.id, "userId": {"$ne": current_user.id}}
            ).sort("updatedAt", -1)
            async for doc in shared_cursor:
                threads.append({
                    "threadId": doc.get("threadId"),
                    "name": doc.get("name"),
                    "isGroup": doc.get("isGroup", False),
                    "createdAt": doc.get("createdAt").isoformat() if doc.get("createdAt") else None,
                    "updatedAt": doc.get("updatedAt").isoformat() if doc.get("updatedAt") else None,
                    "shared": True,
                    "memberCount": len(doc.get("members") or []),
                })

        print(f"[OK] Retrieved {len(threads)} threads from history")
        return {"threads": threads}
        
    except Exception as error:
        print(f"[ERROR] Error fetching thread history: {error}")
        import traceback
        traceback.print_exc()
        return {"threads": []}


@router.post("/history")
async def create_thread_history(
    request: CreateThreadHistoryRequest,
    current_user: User = Depends(get_current_user),
):
    """Create a new thread entry in MongoDB history"""
    try:
        conversation_doc = {
            "userId": current_user.id,
            "threadId": request.threadId,
            "name": request.name,
            "isGroup": request.isGroup,
            # Membership (CHAT_SHARING_ENABLED): the owner is always a member.
            # Written regardless of flag state so the data never drifts while
            # the flag is off — the response and every flag-off read surface
            # are unchanged (list_threads does not echo it flag-off).
            "members": [current_user.id],
            "createdAt": datetime.now(),
            "updatedAt": datetime.now()
        }
        
        await conversations_collection.insert_one(conversation_doc)
        print(f"[OK] Created thread history entry: {request.threadId}")
        
        return {"success": True, "message": "Thread created in history"}
        
    except Exception as error:
        print(f"[ERROR] Error creating thread history: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


# Upper bound for a user-entered thread name. The auto-generated title is
# capped at 40 chars; a typed name gets more room but stays sidebar-sane.
THREAD_NAME_MAX_CHARS = 100

# Output cap for the title LLM call. A 3-5 word title is ~5-12 tokens; 32
# leaves ~3x headroom for a "Title:" prefix, quotes or a stray newline (all
# stripped by the cleaner in generate_thread_title, which keeps only the first
# line) while making a runaway answer impossible: at the measured ~25 tok/s
# that is under 1.5 s of generation. Thinking is off on this call, so no
# reasoning tokens compete for the budget.
TITLE_NUM_PREDICT = 32


def _title_llm():
    """The LLM for the title call ONLY (bounded 2026-08-26).

    ``get_llm()`` is shared with the answer path and, for Ollama, is built on
    llama-index defaults: ``context_window=-1`` resolves through ``ollama show``
    to the model's maximum (262144 for gemma4) and is sent as ``num_ctx``,
    which reloads the runner at 262k ctx (partially offloaded, ~8 tok/s) and
    flips it back to 12288 on the next chat turn; ``thinking=True`` then spends
    400-770 reasoning tokens on a five-word title. Measured 72-79 s against a
    30 s proxy timeout, so the title never landed. Here the same model, host
    and request timeout are used with the app-wide num_ctx (no runner
    flip-flop), thinking off, a small num_predict and the app-wide
    temperature. Groq is returned untouched, as is anything that is not a
    llama-index Ollama (the unit tests stand a double in for get_llm()).
    """
    llm = get_llm()
    if config.LLM_PROVIDER != "ollama":
        return llm
    from llama_index.llms.ollama import Ollama  # lazy, as in get_llm()
    if not isinstance(llm, Ollama):
        return llm
    return Ollama(
        model=config.OLLAMA_MODEL,
        base_url=config.OLLAMA_BASE_URL,
        request_timeout=llm.request_timeout,
        thinking=False,
        context_window=config.OLLAMA_NUM_CTX,
        temperature=config.OLLAMA_TEMPERATURE,
        additional_kwargs={"num_predict": TITLE_NUM_PREDICT},
    )


@router.put("/history")
async def update_thread(
    request: UpdateThreadRequest,
    current_user: User = Depends(get_current_user),
):
    """Update thread metadata (rename / group flag).

    Hardened live path: the name is trimmed server-side; an empty or
    whitespace-only name is rejected (400) rather than persisted; an
    over-long name is rejected (400); and a threadId that matches nothing
    for this user -- nonexistent or another user's -- returns 404 instead
    of silently reporting success. The update writes only name/isGroup and
    updatedAt on the conversations row: threadId is never rewritten, and
    nothing here touches files, messages, retrieval, or the document-set
    fingerprint (which hashes filename/chunkCount/status only).
    """
    try:
        if not request.threadId or request.threadId == "null":
            raise HTTPException(
                status_code=400,
                detail=f"Invalid threadId: '{request.threadId}'"
            )

        update_fields = {"updatedAt": datetime.now()}

        if request.newName is not None:
            name = request.newName.strip()
            if not name:
                raise HTTPException(
                    status_code=400,
                    detail="Thread name cannot be empty."
                )
            if len(name) > THREAD_NAME_MAX_CHARS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Thread name is too long (max {THREAD_NAME_MAX_CHARS} characters)."
                )
            update_fields["name"] = name

        if request.isGroup is not None:
            update_fields["isGroup"] = request.isGroup

        # Conditional rename (first-action auto-title): the name is part of
        # the match filter, so the replace-only-the-placeholder rule is
        # enforced atomically in one update -- no read-then-write window in
        # which a user rename could be clobbered.
        update_filter = {"userId": current_user.id, "threadId": request.threadId}
        if request.expectedCurrentName is not None:
            update_filter["name"] = request.expectedCurrentName

        result = await conversations_collection.update_one(
            update_filter,
            {"$set": update_fields}
        )

        if result.matched_count == 0:
            if request.expectedCurrentName is not None:
                # Distinguish "no such thread" (the hardened 404 contract)
                # from "thread exists but was renamed first" (409: the
                # caller's placeholder-replace lost the race and must not
                # be retried unconditionally).
                exists = await conversations_collection.find_one(
                    {"userId": current_user.id, "threadId": request.threadId}
                )
                if exists is not None:
                    print(
                        f"[TITLE] Conditional rename NOT applied for thread "
                        f"{request.threadId}: current name {exists.get('name')!r} "
                        f"!= expected placeholder {request.expectedCurrentName!r} (409)"
                    )
                    raise HTTPException(
                        status_code=409,
                        detail="Thread name has changed; not overwriting.",
                    )
            # Silent-failure guard (2026-08-26): a rename that matches nothing
            # used to leave no trace at all.
            print(
                f"[WARNING] Thread update NOT applied for thread {request.threadId}: "
                f"no matching thread for user {current_user.id} (404)"
            )
            raise HTTPException(status_code=404, detail="Thread not found")

        what = (
            f" name={update_fields['name']!r}" if "name" in update_fields else ""
        ) + (f" isGroup={update_fields['isGroup']}" if "isGroup" in update_fields else "")
        cond = " (conditional auto-title)" if request.expectedCurrentName is not None else ""
        print(f"[OK] Updated thread: {request.threadId}{what}{cond}")
        return {"success": True, "message": "Thread updated successfully"}

    except HTTPException:
        # Validation and not-found outcomes are the contract, not errors --
        # the blanket handler below must not flatten them into 500s (it
        # used to: the original 400 for a bad threadId surfaced as a 500).
        raise
    except Exception as error:
        print(f"[ERROR] Error updating thread: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


@router.delete("/history")
async def delete_thread(
    request: DeleteThreadRequest,
    current_user: User = Depends(get_current_user),
):
    """Delete a thread from MongoDB history"""
    try:
        result = await conversations_collection.delete_one(
            {"userId": current_user.id, "threadId": request.threadId}
        )
        
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread not found"
            )

        # Cascade: remove this thread's persisted chat messages so they aren't
        # orphaned. Scope is EXACTLY this user + this thread -- the same filter
        # the conversation delete above used -- so no other user's data and no
        # knowledge_base rows are ever touched. Log the count first (dry-run
        # visibility) before actually deleting.
        message_filter = {"userId": current_user.id, "threadId": request.threadId}
        pending = await messages_collection.count_documents(message_filter)
        print(
            f"[CASCADE] Deleting {pending} message(s) for thread "
            f"{request.threadId} (user {current_user.id})"
        )
        message_result = await messages_collection.delete_many(message_filter)
        print(
            f"[CASCADE] Deleted {message_result.deleted_count} message(s) for "
            f"thread {request.threadId}"
        )

        # Cascade: also remove any documents the user uploaded INTO this thread
        # (THREAD_DOC storage -- both the chunk docs and the parent file-metadata
        # doc, which all carry category=thread_upload + threadId + userId). Scope
        # is EXACTLY this user + this thread + the thread_upload category, so the
        # shared knowledge_base, the user's plain user_upload docs, and every
        # other thread's uploads are never matched. Guarded on a truthy threadId
        # so an empty value can never broad-match.
        deleted_thread_documents = 0
        if request.threadId:
            scope = {"userId": current_user.id, "threadId": request.threadId}
            doc_filter = {**scope, "category": "thread_upload"}
            # Dry-run visibility BEFORE the destructive call: log the exact filter
            # and a per-category picture -- how many thread_upload docs will go,
            # and (defense-in-depth) how many docs carrying this threadId are NOT
            # thread_upload and will therefore be LEFT UNTOUCHED (expected: 0).
            to_delete = await files_collection.count_documents(doc_filter)
            other_with_threadid = await files_collection.count_documents(
                {**scope, "category": {"$ne": "thread_upload"}}
            )
            print(
                f"[CASCADE] Thread-doc cleanup for thread {request.threadId} "
                f"(user {current_user.id}): filter={doc_filter} -> would delete "
                f"{to_delete} thread_upload doc(s); {other_with_threadid} "
                f"other-category doc(s) with this threadId (left untouched)"
            )
            doc_result = await files_collection.delete_many(doc_filter)
            deleted_thread_documents = doc_result.deleted_count
            print(
                f"[CASCADE] Deleted {deleted_thread_documents} thread_upload doc(s) "
                f"for thread {request.threadId}"
            )

        # Cascade: the thread's message highlights (HIGHLIGHTS_ENABLED). Same
        # exact {userId, threadId} scope as the messages above, so no other
        # user's or thread's highlights can match. Not flag-gated on purpose:
        # highlights created while the flag was on must not be orphaned by a
        # thread deleted while it is off (delete_many on an empty collection is
        # a no-op). Not added to the response body, which stays as before.
        highlight_result = await highlights_collection.delete_many(
            {"userId": current_user.id, "threadId": request.threadId}
        )
        print(
            f"[CASCADE] Deleted {highlight_result.deleted_count} highlight(s) for "
            f"thread {request.threadId}"
        )

        # Also delete the thread messages from memory
        if request.threadId in _thread_messages:
            del _thread_messages[request.threadId]

        print(f"[OK] Deleted thread: {request.threadId}")
        return {
            "success": True,
            "message": "Thread deleted successfully",
            "deleted_messages": message_result.deleted_count,
            "deleted_thread_documents": deleted_thread_documents,
        }
        
    except HTTPException:
        raise
    except Exception as error:
        print(f"[ERROR] Error deleting thread: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


@router.post("/{thread_id}/title")
async def generate_thread_title(
    thread_id: str,
    request: TitleGenerationRequest,
    current_user: User = Depends(get_current_user),
):
    """Generate a concise title for a thread using the configured LLM.

    Observability contract (2026-08-26, "title silence" incident): every
    outcome prints exactly one terminal line -- ``[TITLE] Skipped`` (empty
    text), ``[OK] Generated title`` or ``[ERROR] Error generating title``
    with the exception type and message -- plus one ``[TITLE] Generating``
    line on arrival. So a thread that keeps its timestamp placeholder with NO
    ``[TITLE]``/``title`` line in the journal means the request never reached
    this handler (the frontend skipped it, or it was rejected before the
    handler ran, e.g. 401) -- not that generation failed quietly here.
    """
    started = time.monotonic()
    try:
        message_text = request.text
        if not message_text:
            print(
                f"[TITLE] Skipped for thread {thread_id}: empty message text "
                f"(message={request.message!r}, content={request.content!r}) "
                f"-- returning timestamp fallback"
            )
            return {"title": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        print(
            f"[TITLE] Generating title for thread {thread_id} "
            f"(user={current_user.id}, {len(message_text)} chars, "
            f"provider={config.LLM_PROVIDER})"
        )

        # Use the configured LLM, bounded for a short title (see _title_llm)
        llm = _title_llm()
        
        # Improved prompt: more specific, clearer instructions
        prompt = f"""Summarize this query into a concise, 3-5 word title. Do not use quotes.

Rules:
- Use ONLY 3-5 words
- Be specific and descriptive
- Do not add quotes, periods, or any punctuation at the end
- Do not use XML tags or thinking process
- Just return the title words directly

User Query: {message_text[:200]}

Title:"""
        
        response = await llm.acomplete(prompt)
        elapsed = time.monotonic() - started
        raw_text = response.text or ""
        title = raw_text.strip()
        
        # Aggressive cleaning to remove any artifacts
        import re
        
        # Remove thinking tags
        title = re.sub(r'<think>.*?</think>', '', title, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove any XML/HTML tags
        title = re.sub(r'<[^>]+>', '', title)
        
        # Remove common prefixes that LLMs add
        title = re.sub(r'^(Title:|Answer:|Response:)\s*', '', title, flags=re.IGNORECASE)
        
        # Remove quotes (single, double, or smart quotes)
        title = title.strip('"').strip("'").strip('"').strip('"').strip()
        
        # Take only the first line
        title = title.split('\n')[0].strip()
        
        # Remove trailing punctuation
        title = title.rstrip('.!?,;:')
        
        # Truncate if too long (aim for 3-5 words, max 40 chars)
        if len(title) > 40:
            # Try to truncate at word boundary
            words = title[:40].rsplit(' ', 1)[0]
            title = words + "..." if len(words) < len(title) else words
        
        # Fallback if title is empty or too short after cleaning
        if not title or len(title) < 3:
            print(
                f"[TITLE] LLM output unusable after cleaning for thread {thread_id} "
                f"(raw={raw_text[:120]!r}) -- using the first words of the message"
            )
            # Use first few words of the message
            words = message_text[:80].split()[:5]
            title = ' '.join(words)
            if len(title) > 40:
                title = title[:37] + "..."
        
        print(f"[OK] Generated title for thread {thread_id} in {elapsed:.1f}s: {title}")
        return {"title": title}

    except Exception as error:
        # The caller gets a usable (timestamp) title either way, so the ONLY
        # trace of a failure is this line -- it must say what went wrong.
        elapsed = time.monotonic() - started
        print(
            f"[ERROR] Error generating title for thread {thread_id} after "
            f"{elapsed:.1f}s: {type(error).__name__}: {error} "
            f"-- returning timestamp fallback"
        )
        fallback_title = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"title": fallback_title}


@router.get("/{thread_id}/messages-history")
async def get_messages_history(
    thread_id: str,
    current_user: User = Depends(get_current_user),
):
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
        
        print(f"[OK] Retrieved {len(message_list)} messages for thread {thread_id}")
        return {"messages": message_list}
        
    except Exception as error:
        print(f"[ERROR] Error fetching messages: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


@router.get("/{thread_id}/history")
async def get_thread_messages_history(
    thread_id: str,
    current_user: User = Depends(get_current_user),
):
    """Alias for get_messages_history"""
    return await get_messages_history(thread_id, current_user)


@router.post("/cache/clear")
async def clear_citation_cache(current_user: User = Depends(get_current_user)):
    """Clear cache (placeholder for compatibility)"""
    try:
        print("[CLEAR]  Cache clear requested (no-op in current implementation)")
        return {
            "success": True,
            "message": "Cache cleared successfully"
        }
    except Exception as error:
        print(f"[ERROR] Error clearing cache: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


@router.post("/{thread_id}/actions")
async def submit_tool_actions(
    thread_id: str,
    request: SubmitActionsRequest,
    current_user: User = Depends(get_current_user),
):
    """Submit tool actions (placeholder for compatibility)"""
    try:
        print(f"[WARNING]  Tool actions not implemented in Groq migration")
        return {"success": True, "message": "Tool outputs acknowledged"}

    except Exception as error:
        print(f"[ERROR] Error submitting tool outputs: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred, please try again."
        )


# ---------------------------------------------------------------------------
# Thread sharing (CHAT_SHARING_ENABLED, default off). A separate router,
# included by main.py ONLY when the flag is on — flag-off these paths are
# absent and the route table is byte-identical to today (the inventory
# personal_router pattern). Decided semantics, not re-litigated here: an
# owner shares explicitly (isGroup true) and a thread id alone grants
# nothing; members read history and post but never rename/delete/remove
# others; a member may remove themselves; the owner may remove anyone but
# is never removed (owner ∈ members is an invariant); FILES ARE NEVER
# SHARED — nothing here touches files or retrieval scoping.
# ---------------------------------------------------------------------------
sharing_router = APIRouter(prefix="/api/assistants/threads", tags=["threads"])


@sharing_router.post("/{thread_id}/join")
async def join_thread(
    thread_id: str,
    current_user: User = Depends(get_current_user),
):
    """Join a SHARED thread by id. 404 unknown thread; 403 when the owner has
    not shared it (isGroup false) — possessing an id grants nothing.
    Idempotent: $addToSet, and joining twice (or the owner joining their own
    thread) changes nothing. The $each also repairs a legacy row missing the
    owner in members, keeping the owner-always-included invariant."""
    conv = await conversations_collection.find_one({"threadId": thread_id})
    if conv is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    if not conv.get("isGroup"):
        raise HTTPException(
            status_code=403,
            detail="This thread is not shared. Ask its owner to share it first.")
    to_add = [current_user.id]
    if conv.get("userId"):
        to_add.append(conv["userId"])
    await conversations_collection.update_one(
        {"threadId": thread_id},
        {"$addToSet": {"members": {"$each": to_add}}},
    )
    print(f"[SHARE] {current_user.id} joined thread {thread_id}")
    return {"success": True, "threadId": thread_id, "name": conv.get("name")}


@sharing_router.post("/{thread_id}/leave")
async def leave_thread(
    thread_id: str,
    current_user: User = Depends(get_current_user),
):
    """Remove SELF from a shared thread. The owner cannot leave their own
    thread (delete it instead); a non-member leaving is an idempotent
    no-op."""
    conv = await conversations_collection.find_one({"threadId": thread_id})
    if conv is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    if conv.get("userId") == current_user.id:
        raise HTTPException(
            status_code=403,
            detail="The owner cannot leave their own thread.")
    await conversations_collection.update_one(
        {"threadId": thread_id},
        {"$pull": {"members": current_user.id}},
    )
    print(f"[SHARE] {current_user.id} left thread {thread_id}")
    return {"success": True, "threadId": thread_id}


@sharing_router.delete("/{thread_id}/members/{member_id}")
async def remove_thread_member(
    thread_id: str,
    member_id: str,
    current_user: User = Depends(get_current_user),
):
    """Owner-only member removal. The owner themselves can never be removed
    (owner ∈ members is the invariant); removing a non-member is an
    idempotent no-op."""
    conv = await conversations_collection.find_one({"threadId": thread_id})
    if conv is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    if conv.get("userId") != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the thread owner can remove members.")
    if member_id == conv.get("userId"):
        raise HTTPException(
            status_code=400,
            detail="The owner cannot be removed from their own thread.")
    await conversations_collection.update_one(
        {"threadId": thread_id},
        {"$pull": {"members": member_id}},
    )
    print(f"[SHARE] owner removed {member_id} from thread {thread_id}")
    return {"success": True, "threadId": thread_id, "removed": member_id}


# ---------------------------------------------------------------------------
# Named persistent source sets (SOURCE_SETS_ENABLED, default off).
# The two thread-scoped endpoints below are the ONLY flag-gated surfaces --
# each 404s with the flag off, exactly as if the route did not exist. The
# rename hardening on PUT /history above is a live-path fix and is unflagged.
# ---------------------------------------------------------------------------

class RemoveSourceRequest(BaseModel):
    filename: str
    # Dry-run by default: the caller must explicitly confirm to delete.
    confirm: bool = False


@router.get("/sources-status")
async def source_sets_status():
    """Ungated feature handshake (kb /status pattern): lets the frontend
    decide whether to render the sources panel. Flag off -> enabled False."""
    return {"enabled": config.SOURCE_SETS_ENABLED}


@router.get("/{thread_id}/sources")
async def list_thread_sources(
    thread_id: str,
    current_user: User = Depends(get_current_user),
):
    """The thread's sources with status, chunk count, and provenance.

    Reads ONLY existing facts: lifecycle from the Phase 1
    effective_ingest_status helper over the parent docs (never recomputed
    here), chunk counts from the parents' chunkCount, partial indexing from
    the parents' stored ingest warning, and vision provenance from the
    chunks' existing metadata.visionDerived / pageStart fields. provenance
    is "verbatim", "vision" (every chunk model-transcribed, e.g. a scan or
    image), or "mixed" (some pages vision-transcribed, listed in
    visionPages)."""
    if not config.SOURCE_SETS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if not thread_id or thread_id == "null":
        raise HTTPException(status_code=400, detail="Invalid threadId")

    scope = {
        "userId": current_user.id,
        "threadId": thread_id,
        "category": "thread_upload",
    }
    now = datetime.now()

    # Vision facts per filename, from existing chunk fields only.
    vision_chunks: Dict[str, int] = {}
    vision_pages: Dict[str, set] = {}
    async for c in files_collection.find(
        {**scope, "chunkIndex": {"$exists": True}, "metadata.visionDerived": True},
        {"filename": 1, "pageStart": 1},
    ):
        fn = c.get("filename") or ""
        vision_chunks[fn] = vision_chunks.get(fn, 0) + 1
        if c.get("pageStart") is not None:
            vision_pages.setdefault(fn, set()).add(c["pageStart"])

    sources = []
    async for doc in files_collection.find(
        {**scope, "chunkIndex": {"$exists": False}},
        {"filename": 1, "status": 1, "error": 1, "warning": 1,
         "chunkCount": 1, "createdAt": 1},
    ):
        fn = doc.get("filename") or ""
        doc_status, reason = effective_ingest_status(doc, now=now)
        chunk_count = int(doc.get("chunkCount") or 0)
        v_n = vision_chunks.get(fn, 0)
        if v_n and chunk_count and v_n >= chunk_count:
            provenance = "vision"
        elif v_n:
            provenance = "mixed"
        else:
            provenance = "verbatim"
        sources.append({
            "filename": fn,
            "status": doc_status,
            "reason": reason,
            "chunkCount": chunk_count,
            "provenance": provenance,
            "visionChunkCount": v_n,
            "visionPages": sorted(vision_pages.get(fn, set())),
            "partiallyIndexed": bool(doc.get("warning")),
            "warning": doc.get("warning"),
            "createdAt": doc.get("createdAt").isoformat() if doc.get("createdAt") else None,
        })
    sources.sort(key=lambda s: s["filename"])
    return {"sources": sources}


@router.post("/{thread_id}/sources/remove")
async def remove_thread_source(
    thread_id: str,
    request: RemoveSourceRequest,
    current_user: User = Depends(get_current_user),
):
    """Remove ONE source from a source set -- dry-run by default.

    Scope is the whole-thread cascade filter narrowed by filename:
    {userId, threadId, category: "thread_upload", filename} matches exactly
    this source's chunks and its parent doc, and can never match the shared
    knowledge_base, plain user_upload docs, another thread's uploads, or the
    thread's other sources. The response always carries the counts of what
    WOULD be deleted plus the negative-check counts of what survives; the
    delete itself runs only with confirm=true. The conversations row is
    never touched -- removing the last source leaves the set intact and
    empty. The Phase 2 fingerprint changes automatically because it hashes
    the surviving parent docs."""
    if not config.SOURCE_SETS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if not thread_id or thread_id == "null":
        raise HTTPException(status_code=400, detail="Invalid threadId")
    filename = (request.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")

    # Ownership gate, mirroring delete_thread's: the conversation row must
    # exist for THIS user before any file count or delete runs.
    conv = await conversations_collection.find_one(
        {"userId": current_user.id, "threadId": thread_id}
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    scope = {
        "userId": current_user.id,
        "threadId": thread_id,
        "category": "thread_upload",
        "filename": filename,
    }
    chunks_to_delete = await files_collection.count_documents(
        {**scope, "chunkIndex": {"$exists": True}}
    )
    parents_to_delete = await files_collection.count_documents(
        {**scope, "chunkIndex": {"$exists": False}}
    )
    if chunks_to_delete == 0 and parents_to_delete == 0:
        raise HTTPException(status_code=404, detail="Source not found in this thread")

    # Negative checks -- everything the filter must NOT touch, counted so the
    # user (and the tests) see the isolation, not just the deletion:
    #   - the thread's OTHER sources
    #   - docs carrying this threadId in another category (expected 0)
    #   - the same filename anywhere else: knowledge_base / user_upload /
    #     other threads' copies
    untouched = {
        "otherSourcesInThread": await files_collection.count_documents(
            {"userId": current_user.id, "threadId": thread_id,
             "category": "thread_upload", "filename": {"$ne": filename}}
        ),
        "otherCategoriesInThread": await files_collection.count_documents(
            {"userId": current_user.id, "threadId": thread_id,
             "category": {"$ne": "thread_upload"}}
        ),
        "sameFilenameOtherCategories": await files_collection.count_documents(
            {"filename": filename, "category": {"$ne": "thread_upload"}}
        ),
        "sameFilenameOtherThreads": await files_collection.count_documents(
            {"filename": filename, "category": "thread_upload",
             "threadId": {"$ne": thread_id}}
        ),
    }
    preview = {
        "filename": filename,
        "chunksToDelete": chunks_to_delete,
        "parentDocsToDelete": parents_to_delete,
        "untouched": untouched,
    }
    print(
        f"[SOURCES] Remove '{filename}' from thread {thread_id} "
        f"(user {current_user.id}): filter={scope} -> would delete "
        f"{chunks_to_delete} chunk(s) + {parents_to_delete} parent doc(s); "
        f"untouched={untouched} (confirm={request.confirm})"
    )

    if not request.confirm:
        return {"dryRun": True, **preview}

    result = await files_collection.delete_many(scope)
    print(f"[SOURCES] Deleted {result.deleted_count} doc(s) for '{filename}'")
    return {"dryRun": False, **preview, "deleted": result.deleted_count}
