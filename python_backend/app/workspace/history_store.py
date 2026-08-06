"""MongoDB persistence for the GeoPilot workspace (runs + threads).

Durable, per-user storage so calculator runs and chat threads survive backend
restarts and the Excel export never dead-links. It uses two NEW collections
(``workspace_runs`` / ``workspace_threads``) and NEVER touches the Chat-tab
collections (conversations / messages / files) or the RAG pipeline.

Every read and write is scoped by ``user_id`` (the authenticated user id), so a
user can only ever list, open or append to their OWN runs and threads. Requests
for another user's ``_id`` simply do not match and return None.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId

# Module-level collection handles (patched with fakes in tests).
from app.core.database import (
    workspace_runs_collection as runs_collection,
    workspace_threads_collection as threads_collection,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value: str) -> Optional[ObjectId]:
    """Parse a hex string into an ObjectId, or None if it is not a valid id.

    Guards the by-id lookups so a malformed / foreign id yields a clean 404
    rather than a 500.
    """
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


# --- Runs ------------------------------------------------------------------
def _public_run(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "calculator_id": doc.get("calculator_id"),
        "source_filename": doc.get("source_filename"),
        "created_at": _iso(doc.get("created_at")),
        "summary": doc.get("summary", {}),
        "result_object": doc.get("result_object", {}),
    }


async def create_run(
    user_id: str,
    calculator_id: str,
    source_filename: str,
    result_object: Dict[str, Any],
    summary: Dict[str, Any],
) -> str:
    """Persist a calculator run and return its new id (string)."""
    res = await runs_collection.insert_one(
        {
            "user_id": user_id,
            "calculator_id": calculator_id,
            "source_filename": source_filename,
            "created_at": _now(),
            "result_object": result_object,
            "summary": summary,
        }
    )
    return str(res.inserted_id)


async def get_run(user_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    """One run by id, scoped to the user. None if missing / wrong user / bad id."""
    oid = _oid(run_id)
    if oid is None:
        return None
    doc = await runs_collection.find_one({"_id": oid, "user_id": user_id})
    return _public_run(doc) if doc else None


async def list_runs(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """A user's runs, newest first."""
    cursor = (
        runs_collection.find({"user_id": user_id})
        .sort("created_at", -1)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [_public_run(d) for d in docs]


# --- Threads ---------------------------------------------------------------
def _public_thread(
    doc: Dict[str, Any], *, include_messages: bool = False
) -> Dict[str, Any]:
    out = {
        "id": str(doc["_id"]),
        "title": doc.get("title"),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "message_count": len(doc.get("messages", []) or []),
    }
    if include_messages:
        out["messages"] = [
            {**m, "created_at": _iso(m.get("created_at"))}
            for m in (doc.get("messages", []) or [])
        ]
    return out


async def create_thread(user_id: str, title: str) -> str:
    """Create an empty thread and return its new id (string)."""
    now = _now()
    res = await threads_collection.insert_one(
        {
            "user_id": user_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
    )
    return str(res.inserted_id)


async def thread_exists(user_id: str, thread_id: str) -> bool:
    """Whether a thread id belongs to the user (cheap existence check)."""
    oid = _oid(thread_id)
    if oid is None:
        return False
    doc = await threads_collection.find_one(
        {"_id": oid, "user_id": user_id}, {"_id": 1}
    )
    return doc is not None


async def append_message(
    user_id: str, thread_id: str, message: Dict[str, Any]
) -> bool:
    """Append a message to a user's thread and bump ``updated_at``.

    Returns True if the thread matched (existed and was owned by the user).
    """
    oid = _oid(thread_id)
    if oid is None:
        return False
    now = _now()
    res = await threads_collection.update_one(
        {"_id": oid, "user_id": user_id},
        {"$push": {"messages": {**message, "created_at": now}},
         "$set": {"updated_at": now}},
    )
    return res.matched_count > 0


async def get_thread(user_id: str, thread_id: str) -> Optional[Dict[str, Any]]:
    """One thread with its messages, scoped to the user. None if not found."""
    oid = _oid(thread_id)
    if oid is None:
        return None
    doc = await threads_collection.find_one({"_id": oid, "user_id": user_id})
    return _public_thread(doc, include_messages=True) if doc else None


async def list_threads(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """A user's threads, most-recently-updated first (no message bodies)."""
    cursor = (
        threads_collection.find({"user_id": user_id})
        .sort("updated_at", -1)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [_public_thread(d) for d in docs]
