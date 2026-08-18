"""
Persistent text highlights + notes on assistant messages (HIGHLIGHTS_ENABLED).

One highlight = {threadId, messageId, userId, startOffset, endOffset,
selectedText, colour, note, createdAt, updatedAt}. Offsets are UTF-16 code
unit positions into the persisted ``messages.content`` (the source markdown),
which is write-once (chat.py only ever ``insert_one``s assistant rows), so an
anchor stays valid for the lifetime of the row. ``selectedText`` is the
RENDERED text the user selected; the frontend re-checks it at render time and
skips (never mis-places) a highlight whose text no longer matches. The
backend check here is deliberately the coarse, markdown-tolerant one it can
do without a renderer: the selected words must occur inside the source slice.

Authorisation mirrors the thread routes: the conversations row must exist for
THIS user (404 otherwise), the message must be an assistant row of that
thread for that user, and a highlight is only ever read/updated/deleted
through the same {threadId, userId} scope.

Flag-gated at REGISTRATION: ``register(app)`` includes the router only when
config.HIGHLIGHTS_ENABLED is on, so with the flag off the routes are absent
(not present-and-404ing) and the app's route table is unchanged.
"""
import html
import re
from datetime import datetime
from typing import List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel

from app.core import config
from app.core.database import (
    conversations_collection,
    highlights_collection,
    messages_collection,
)
from app.dependencies.auth import get_current_user
from app.services import highlights_export
from models import User

router = APIRouter(prefix="/api/assistants/threads", tags=["highlights"])

COLOURS = ("yellow", "green", "blue", "pink")
SELECTED_TEXT_MAX_CHARS = 5000
NOTE_MAX_CHARS = 2000


class HighlightCreateRequest(BaseModel):
    messageId: str
    startOffset: int
    endOffset: int
    selectedText: str
    colour: str = "yellow"
    note: str = ""


class HighlightUpdateRequest(BaseModel):
    colour: Optional[str] = None
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Pure validation helpers (unit-tested directly)
# ---------------------------------------------------------------------------

def utf16_len(text: str) -> int:
    """Length in UTF-16 code units -- the unit JS string offsets are in."""
    return len(text.encode("utf-16-le")) // 2


def utf16_slice(text: str, start: int, end: int) -> str:
    """text[start:end] measured in UTF-16 code units (JS semantics)."""
    return text.encode("utf-16-le")[2 * start:2 * end].decode("utf-16-le", errors="replace")


# A "word" is a maximal run of Unicode letters/digits. Underscore is
# deliberately excluded (\w would keep it) because "snake\_case" in the source
# renders as "snake_case": splitting on "_" on both sides makes them agree.
_WORD_RE = re.compile(r"[^\W_]+")


def _words(text: str) -> List[str]:
    return _WORD_RE.findall(text.casefold())


def _is_subsequence(needle: List[str], haystack: List[str]) -> bool:
    it = iter(haystack)
    return all(any(w == h for h in it) for w in needle)


def validate_anchor(content, start: int, end: int, selected_text: str) -> Optional[str]:
    """Return an error message, or None when the anchor is acceptable.

    Checks, in order: the message text is a string; 0 <= start < end <= len;
    the selection is non-blank and bounded; the source slice is at least as
    long as the rendered selection (rendering only ever removes characters --
    markdown syntax, escapes, entities, trimmed whitespace); and the words of
    the rendered selection occur, whole and in order, among the words of the
    source slice. Extra words in the slice are allowed -- link URLs, entity
    names ("&amp;"), TeX inside $...$, code-fence info strings all render to
    nothing or to glyphs -- but a wrong or shifted anchor changes the words
    themselves and is rejected. The exact rendered-text check is the
    frontend's job at render time; this is the coarse guard the backend can
    apply without a markdown renderer.
    """
    if not isinstance(content, str):
        return "Message text is not highlightable"
    if start < 0 or end <= start:
        return "startOffset must be >= 0 and < endOffset"
    if end > utf16_len(content):
        return "endOffset is beyond the end of the message"
    if not selected_text or not selected_text.strip():
        return "selectedText must not be blank"
    if len(selected_text) > SELECTED_TEXT_MAX_CHARS:
        return f"selectedText must be at most {SELECTED_TEXT_MAX_CHARS} characters"
    if end - start < utf16_len(selected_text):
        return "selectedText is longer than the offset range"
    slice_ = utf16_slice(content, start, end)
    needle = _words(selected_text)
    # Two views of the slice: raw (code spans render "&amp;" literally) and
    # entity-decoded (prose renders "&phi;" as the single letter φ). Either
    # matching is enough; a wrong anchor matches neither.
    if not (
        _is_subsequence(needle, _words(slice_))
        or _is_subsequence(needle, _words(html.unescape(slice_)))
    ):
        return "selectedText does not match the message text at the given offsets"
    return None


def validate_colour(colour: str) -> Optional[str]:
    if colour not in COLOURS:
        return f"colour must be one of: {', '.join(COLOURS)}"
    return None


def validate_note(note: str) -> Optional[str]:
    if len(note) > NOTE_MAX_CHARS:
        return f"note must be at most {NOTE_MAX_CHARS} characters"
    return None


def _serialize(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "threadId": doc.get("threadId"),
        "messageId": doc.get("messageId"),
        "userId": doc.get("userId"),
        "startOffset": doc.get("startOffset"),
        "endOffset": doc.get("endOffset"),
        "selectedText": doc.get("selectedText"),
        "colour": doc.get("colour"),
        "note": doc.get("note", ""),
        "createdAt": doc["createdAt"].isoformat() if doc.get("createdAt") else None,
        "updatedAt": doc["updatedAt"].isoformat() if doc.get("updatedAt") else None,
    }


# ---------------------------------------------------------------------------
# Authorisation helpers
# ---------------------------------------------------------------------------

async def _require_thread(thread_id: str, current_user: User) -> None:
    """The conversations row must exist for THIS user (threads.py contract)."""
    if not thread_id or thread_id == "null":
        raise HTTPException(status_code=400, detail="Invalid threadId")
    conv = await conversations_collection.find_one(
        {"userId": current_user.id, "threadId": thread_id}
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Thread not found")


def _object_id(value: str, what: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail=f"{what} not found")


async def _require_assistant_message(message_id: str, thread_id: str, current_user: User) -> dict:
    oid = _object_id(message_id, "Message")
    msg = await messages_collection.find_one(
        {"_id": oid, "threadId": thread_id, "userId": current_user.id}
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.get("role") != "assistant":
        raise HTTPException(
            status_code=422, detail="Highlights can only be attached to assistant messages"
        )
    return msg


async def _require_highlight(highlight_id: str, thread_id: str, current_user: User) -> dict:
    oid = _object_id(highlight_id, "Highlight")
    doc = await highlights_collection.find_one(
        {"_id": oid, "threadId": thread_id, "userId": current_user.id}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Highlight not found")
    return doc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/{thread_id}/highlights", status_code=status.HTTP_201_CREATED)
async def create_highlight(
    thread_id: str,
    request: HighlightCreateRequest,
    current_user: User = Depends(get_current_user),
):
    await _require_thread(thread_id, current_user)
    msg = await _require_assistant_message(request.messageId, thread_id, current_user)

    for err in (
        validate_anchor(msg.get("content"), request.startOffset, request.endOffset, request.selectedText),
        validate_colour(request.colour),
        validate_note(request.note),
    ):
        if err:
            raise HTTPException(status_code=422, detail=err)

    now = datetime.now()
    doc = {
        "threadId": thread_id,
        "messageId": request.messageId,
        "userId": current_user.id,
        "startOffset": request.startOffset,
        "endOffset": request.endOffset,
        "selectedText": request.selectedText,
        "colour": request.colour,
        "note": request.note,
        "createdAt": now,
        "updatedAt": now,
    }
    result = await highlights_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"highlight": _serialize(doc)}


@router.get("/{thread_id}/highlights")
async def list_highlights(
    thread_id: str,
    current_user: User = Depends(get_current_user),
):
    await _require_thread(thread_id, current_user)
    cursor = highlights_collection.find(
        {"threadId": thread_id, "userId": current_user.id}
    ).sort("createdAt", 1)
    highlights = []
    async for doc in cursor:
        highlights.append(_serialize(doc))
    return {"highlights": highlights}


@router.patch("/{thread_id}/highlights/{highlight_id}")
async def update_highlight(
    thread_id: str,
    highlight_id: str,
    request: HighlightUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    await _require_thread(thread_id, current_user)
    doc = await _require_highlight(highlight_id, thread_id, current_user)

    update = {}
    if request.colour is not None:
        err = validate_colour(request.colour)
        if err:
            raise HTTPException(status_code=422, detail=err)
        update["colour"] = request.colour
    if request.note is not None:
        err = validate_note(request.note)
        if err:
            raise HTTPException(status_code=422, detail=err)
        update["note"] = request.note
    if not update:
        raise HTTPException(status_code=422, detail="Nothing to update: provide colour and/or note")

    update["updatedAt"] = datetime.now()
    await highlights_collection.update_one(
        {"_id": doc["_id"], "threadId": thread_id, "userId": current_user.id},
        {"$set": update},
    )
    doc.update(update)
    return {"highlight": _serialize(doc)}


@router.delete("/{thread_id}/highlights/{highlight_id}")
async def delete_highlight(
    thread_id: str,
    highlight_id: str,
    current_user: User = Depends(get_current_user),
):
    await _require_thread(thread_id, current_user)
    doc = await _require_highlight(highlight_id, thread_id, current_user)
    await highlights_collection.delete_one(
        {"_id": doc["_id"], "threadId": thread_id, "userId": current_user.id}
    )
    return {"success": True}


# ---------------------------------------------------------------------------
# Thread-wide export (Markdown / Excel)
# ---------------------------------------------------------------------------

EXPORT_FORMATS = ("md", "xlsx")


async def _require_thread_doc(thread_id: str, current_user: User) -> dict:
    """Same gate as _require_thread, returning the conversations row (title)."""
    if not thread_id or thread_id == "null":
        raise HTTPException(status_code=400, detail="Invalid threadId")
    conv = await conversations_collection.find_one(
        {"userId": current_user.id, "threadId": thread_id}
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return conv


@router.get("/{thread_id}/highlights/export")
async def export_highlights(
    thread_id: str,
    format: str = Query("md", description="md or xlsx"),
    current_user: User = Depends(get_current_user),
):
    """Download every highlight in the thread as Markdown or Excel.

    Ordered by message (thread order), then position within the message; each
    row carries a context snippet from the message's source text. Zero
    highlights is a valid (empty) export, not an error.
    """
    fmt = (format or "").strip().lower()
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail="format must be 'md' or 'xlsx'")
    conv = await _require_thread_doc(thread_id, current_user)
    title = str(conv.get("name") or "Conversation")

    highlights = []
    async for doc in highlights_collection.find(
        {"threadId": thread_id, "userId": current_user.id}
    ).sort("createdAt", 1):
        highlights.append(doc)
    messages = []
    async for doc in messages_collection.find(
        {"threadId": thread_id, "userId": current_user.id}
    ).sort("createdAt", 1):
        messages.append(doc)

    rows = highlights_export.build_rows(highlights, messages)
    filename = highlights_export.export_filename(title, fmt)
    if fmt == "md":
        body = highlights_export.build_markdown(title, rows).encode("utf-8")
        media_type = highlights_export.MD_MEDIA_TYPE
    else:
        body = highlights_export.build_xlsx(title, rows)
        media_type = highlights_export.XLSX_MEDIA_TYPE
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def register(app: FastAPI) -> None:
    """Include the highlights routes ONLY when HIGHLIGHTS_ENABLED is on.

    Read at call time (config module attribute) so tests can toggle it. With
    the flag off nothing is added: the route table is byte-for-byte the
    pre-feature one, and /api/assistants/threads/{id}/highlights simply does
    not exist.
    """
    if config.HIGHLIGHTS_ENABLED:
        app.include_router(router)
